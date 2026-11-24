"""Stateful coverage backfill must reach operations anywhere in the spec."""

import json

import httpx
import pytest

from api_parity.case_generator import CaseGenerator
from api_parity.cli import dispatch, parse_args
from api_parity.executor import Executor
from api_parity.models import ChainCase, ChainStep, RequestCase


@pytest.mark.parametrize("orphans_first", [False, True], ids=["last", "first"])
def test_ensure_coverage_targets_only_missing_operations(
    tmp_path, monkeypatch, fixture_cel_evaluator_path, orphans_first, caplog, capsys,
):
    ok = {"200": {"description": "OK"}}
    paths = {}
    steps = []
    # The old shared budget for four missing operations ran out after 24 cases.
    for i in range(25):
        name = f"linked{i}"
        path = f"/{name}"
        paths[path] = {"get": {"operationId": name, "responses": ok}}
        case = RequestCase(
            case_id=name, operation_id=name, method="GET", path_template=path, rendered_path=path,
        )
        steps.append(ChainStep(step_index=i, request_template=case))
    paths["/excluded"] = {"get": {"operationId": "excluded", "responses": ok}}

    # Put both generation failures between a fixed request and one that can use its full budget.
    missing = {
        "/fixed": {"get": {"operationId": "fixed", "responses": ok}},
        "/png": {"post": {
            "operationId": "uploadImage", "responses": ok,
            "requestBody": {"required": True, "content": {"image/png": {"schema": {"type": "string"}}}},
        }},
        "/empty-range": {"get": {
            "operationId": "emptyRange", "responses": ok,
            "parameters": [{"name": "n", "in": "query", "required": True,
                            "schema": {"type": "integer", "minimum": 5, "maximum": 1}}],
        }},
        "/orphan": {"get": {
            "responses": ok,
            "parameters": [{"name": "n", "in": "query", "required": True, "schema": {"type": "integer"}}],
        }},
    }
    paths = missing | paths if orphans_first else paths | missing
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.3", "info": {"title": "test", "version": "1"}, "paths": paths,
    }))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "targets": {side: {"base_url": f"http://{side}"} for side in ("a", "b")},
        "comparison_rules": "rules.json",
    }))
    (tmp_path / "rules.json").write_text(json.dumps({
        "default_rules": {"status_code": {"predefined": "exact_match"}},
    }))

    # Fix the discovered chain so only backfill exercises standalone generation.
    chain = ChainCase(chain_id="linked", steps=steps)
    monkeypatch.setattr(CaseGenerator, "generate_chains", lambda *args, **kwargs: [chain])
    generated = []
    generate = CaseGenerator._generate_for_operation

    def record(self, op, operation_id, max_cases, seed):
        generated.append((operation_id, max_cases, seed))
        yield from generate(self, op, operation_id, max_cases, seed)

    monkeypatch.setattr(CaseGenerator, "_generate_for_operation", record)
    received = []

    def respond(request):
        received.append((request.url.host, request.url.path))
        return httpx.Response(409 if (request.url.host, request.url.path) == ("b", "/orphan") else 200)

    monkeypatch.setattr(Executor, "_build_client_kwargs", lambda self, target, timeout: {
        "base_url": target.base_url, "transport": httpx.MockTransport(respond),
    })
    out = tmp_path / "out"
    args = parse_args([
        "explore", "--spec", str(spec), "--config", str(config), "--target-a", "a", "--target-b", "b",
        "--stateful", "--seed", "42", "--exclude", "excluded", "--ensure-coverage", "--out", str(out),
    ])
    assert dispatch(args) == 0
    for side in ("a", "b"):
        assert received.count((side, "/fixed")) == 1
        assert received.count((side, "/orphan")) == 3
        assert (side, "/png") not in received
        assert (side, "/empty-range") not in received
        assert (side, "/excluded") not in received
    assert json.loads((out / "summary.json").read_text())["mismatches"] == 3
    assert generated == [(name, 3, 42) for name in ("fixed", "uploadImage", "emptyRange", "get_/orphan")]
    assert any("uploadImage" in message and "image/png" in message for message in caplog.messages)
    assert any("emptyRange" in message and "Unsatisfiable" in message for message in caplog.messages)
    assert "2 operations still uncovered after coverage tests:\n  - emptyRange\n  - uploadImage" in capsys.readouterr().out
