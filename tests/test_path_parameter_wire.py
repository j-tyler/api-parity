"""Compare path parameter data with the actual request-target bytes seen by HTTPX."""

import json

import httpx
import pytest

from api_parity.case_generator import CaseGenerator, HeaderRef, LinkFields
from api_parity.executor import Executor
from api_parity.models import ChainCase, ChainStep, RequestCase, TargetConfig
from api_parity.path_rendering import collection_path


def capture_targets(monkeypatch, handler, link_fields=None):
    monkeypatch.setattr(Executor, "_build_client_kwargs", lambda self, config, timeout: {
        "base_url": config.base_url, "transport": httpx.MockTransport(handler),
    })
    return Executor(TargetConfig(base_url="http://a"), TargetConfig(base_url="http://b"), link_fields=link_fields)


def test_generated_path_escapes_once(tmp_path, monkeypatch):
    value = "a#b?c+ d%23"
    spec = {"openapi": "3.0.3", "info": {"title": "paths", "version": "1"},
            "paths": {"/widgets/{id}/view": {"get": {"operationId": "getWidget",
                "parameters": [{"name": "id", "in": "path", "required": True,
                                "schema": {"type": "string", "enum": [value]}}],
                "responses": {"200": {"description": "ok"}}}}}}
    path = tmp_path / "paths.json"
    path.write_text(json.dumps(spec))
    case = next(CaseGenerator(path).generate(seed=42, max_cases=1))
    received = []

    def handle(request):
        received.append(request.url.raw_path)
        return httpx.Response(200)

    with capture_targets(monkeypatch, handle) as executor:
        executor.execute(case)
        executor.execute_chain(ChainCase(chain_id="read", steps=[ChainStep(step_index=0, request_template=case)]))
    assert received == [b"/widgets/a%23b%3Fc%2B%20d%2523/view"] * 4


@pytest.mark.parametrize(("source", "value", "expected"), [
    ("$response.body#/id", "a#b", b"a%23b"),
    ("$response.body#/id", "a?b+c %2F/{other}", b"a%3Fb%2Bc%20%252F%2F%7Bother%7D"),
    ("$response.body#/id", "..", b"%2E%2E"),
    ("$response.header.Location", "/tenants/a%2Fb/widgets;v=2", b"tenants/a%2Fb/widgets;v=2"),
])
def test_linked_and_substituted_targets(monkeypatch, source, value, expected):
    create = RequestCase(case_id="create", operation_id="create", method="POST",
                         path_template="/widgets", rendered_path="/widgets")
    read = RequestCase(case_id="read", operation_id="read", method="GET",
                       path_template="/widgets/{id};view={other}/details",
                       path_parameters={"id": "seed", "other": "prefix-{label}"},
                       rendered_path="/widgets/seed;view=seed/details", query={"q": ["ok"]})
    steps = [ChainStep(step_index=0, request_template=create), ChainStep(
        step_index=1, request_template=read, link_source={"source_operation": "create",
                                                         "parameters": {"id": source}})]
    received = []

    def handle(request):
        if request.method == "POST":
            return httpx.Response(201, json={"id": value, "label": "x#%2F"},
                                  headers={"Location": value} if "header" in source else {})
        received.append(request.url.raw_path)
        return httpx.Response(200)

    fields = LinkFields(body_pointers={"id", "label"}, headers=[HeaderRef("location", "Location")])
    with capture_targets(monkeypatch, handle, fields) as executor:
        executor.execute_chain(ChainCase(chain_id="linked", steps=steps))
    assert received == [b"/widgets/" + expected + b";view=prefix-x%23%252F/details?q=ok"] * 2


@pytest.mark.parametrize(("source", "value", "expected"), [
    ("$response.header.Location", "/tenants/a%2Fb/widgets;v=2", "tenants/a%2Fb/widgets;v=2"),
    ("$response.header.Content-Location", "tenants/a%2Fb", "tenants/a%2Fb"),
    ("$response.body#/links/self", "/tenants/a%2Fb", "tenants/a%2Fb"),
    ("$response.body#/id", "/42", "42"),
    ("$response.body#/id", "team/42%2F", "team%2F42%252F"),
])
def test_forwarded_request_path_reuses_each_targets_resource(monkeypatch, source, value, expected):
    def step(index, operation, method, path, parameters, link=None):
        request = RequestCase(case_id=operation, operation_id=operation, method=method,
                              path_template=path, path_parameters=parameters, rendered_path=path)
        return ChainStep(step_index=index, request_template=request, link_source=link)

    chain = ChainCase(chain_id="forward", steps=[
        step(0, "create", "POST", "/create", {}),
        step(1, "read", "GET", "/{id}", {"id": "seed"},
             {"source_operation": "create", "parameters": {"id": source}}),
        step(2, "delete", "DELETE", "/{other}", {"other": "seed"},
             {"source_operation": "read", "parameters": {"other": "$request.path.id"}}),
    ])
    received = []

    def handle(request):
        host = request.url.host
        if request.method == "POST":
            resource = value + "-" + host
            return httpx.Response(201, json={"id": resource, "links": {"self": resource}},
                                  headers={"Location": resource, "Content-Location": resource})
        received.append((host, request.method, request.url.raw_path))
        return httpx.Response(200)

    fields = LinkFields(body_pointers={"id", "links/self"}, headers=[
        HeaderRef("location", "Location"), HeaderRef("content-location", "Content-Location")])
    with capture_targets(monkeypatch, handle, fields) as executor:
        executor.execute_chain(chain)
    assert received == [(host, method, f"/{expected}-{host}".encode())
                        for method in ("GET", "DELETE") for host in ("a", "b")]


@pytest.mark.parametrize(("schema", "style", "explode", "value", "expected"), [
    ({"type": "object", "additionalProperties": {"type": "string"}}, "simple", False,
     {"k": "a/b", "n": "x,y"}, b"k,a%2Fb,n,x%2Cy"),
    ({"type": "array", "items": {"type": "string"}}, "matrix", True,
     ["a/b", "x,y"], b";id=a%2Fb;id=x%2Cy"),
])
def test_generated_collection_link_survives_bundle_round_trip(
    tmp_path, monkeypatch, schema, style, explode, value, expected,
):
    parameter = {"in": "path", "name": "id", "required": True, "style": style, "explode": explode,
                 "schema": {"$ref": "#/components/schemas/Owner"}}
    response = {"description": "ok", "links": {
        "read": {"operationId": "read", "parameters": {"id": "$response.body#/owner"}}
    }, "content": {"application/json": {"schema": {
        "type": "object", "required": ["owner"], "properties": {"owner": schema},
    }}}}
    spec = {"openapi": "3.0.3", "info": {"title": "collection link", "version": "1"},
            "components": {"schemas": {"Owner": schema}}, "paths": {
                "/start": {"post": {"operationId": "start", "responses": {"200": response}}},
                "/items/{id}": {"get": {"operationId": "read", "parameters": [parameter],
                                        "responses": {"200": {"description": "ok"}}}},
            }}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    chains = CaseGenerator(path).generate_chains(max_chains=16, max_steps=2, seed=42)
    linked = next(chain for chain in chains if any(step.link_source for step in chain.steps))
    reads = []

    def handle(request):
        if request.method == "POST":
            return httpx.Response(200, json={"owner": value})
        reads.append(request.url.raw_path)
        return httpx.Response(200)

    with capture_targets(monkeypatch, handle) as executor:
        executor.execute_chain(ChainCase.model_validate_json(linked.model_dump_json()))
    assert reads == [b"/items/" + expected] * 2


@pytest.mark.parametrize(("style", "explode", "value", "expected"), [
    ("simple", True, {"k": True, "n": "a=b"}, "k=true,n=a%3Db"),
    ("label", True, ["a.b", "c/d"], ".a%2Eb.c%2Fd"),
    ("matrix", False, {"a": 42, "b": "x"}, ";id=a,42,b,x"),
    ("matrix", True, {"a": "x;y"}, ";a=x%3By"),
    ("json", False, {"a": [1]}, "%7B%22a%22%3A%5B1%5D%7D"),
    ("simple", False, {"a": [1]}, None),
])
def test_collection_path_serialization(style, explode, value, expected):
    options = {"types": ["object", "array"], "style": style, "explode": explode}
    assert collection_path("id", value, options) == expected
