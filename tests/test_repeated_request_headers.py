"""Repeated RequestCase header values are individual HTTP fields on both targets."""

import httpx
import pytest

from api_parity.executor import Executor
from api_parity.models import RequestCase, TargetConfig


def test_execute_sends_every_header_value_in_order(monkeypatch):
    seen = {}
    preferences = ["respond-async", "wait=10"]

    def reply(request):
        headers = request.headers
        seen[request.url.host] = (
            headers.get_list("Prefer"), headers.get_list("X-Label"), headers.get_list("X-Empty"),
            headers.get_list("X-Default"),
        )
        if headers.get_list("Prefer") != preferences:
            return httpx.Response(400)
        return httpx.Response(200 if request.url.host == "a" else 409)

    original_kwargs = Executor._build_client_kwargs

    def client_kwargs(self, config, timeout):
        return {**original_kwargs(self, config, timeout), "transport": httpx.MockTransport(reply)}

    monkeypatch.setattr(Executor, "_build_client_kwargs", client_kwargs)
    case = RequestCase(
        case_id="direct", operation_id="job", method="GET", path_template="/job", rendered_path="/job",
        headers={
            "Prefer": preferences, "X-Label": ["one,two", "café", "café"], "X-Empty": [""], "X-Default": [],
        },
    )
    targets = [TargetConfig(base_url=f"http://{side}", headers={"prefer": "default", "X-Default": side})
               for side in ("a", "b")]
    with Executor(*targets) as executor:
        a, b = executor.execute(case)

    assert seen == {side: (preferences, ["one,two", "caf?", "caf?"], [""], [side]) for side in ("a", "b")}
    assert (a.status_code, b.status_code) == (200, 409)


@pytest.mark.parametrize("content_type", ["application/json", "multipart/form-data"])
def test_execute_uses_first_content_type_and_credentials(monkeypatch, content_type):
    seen = []

    def reply(request):
        seen.append(request)
        return httpx.Response(200)

    original_kwargs = Executor._build_client_kwargs

    def client_kwargs(self, config, timeout):
        return {**original_kwargs(self, config, timeout), "transport": httpx.MockTransport(reply)}

    monkeypatch.setattr(Executor, "_build_client_kwargs", client_kwargs)
    headers = {
        "CONTENT-type": [], "Content-Type": [content_type, "text/plain"], "content-type": ["image/png"],
        "Cookie": ["a=1; b=2", "c=3"], "cookie": ["d=4"],
        "authorization": ["Bearer first", "Bearer second"], "Authorization": ["Bearer third"],
        "Prefer": ["respond-async", "return=minimal"],
    }
    case = RequestCase(case_id="post", operation_id="post", method="POST", path_template="/", rendered_path="/",
                       headers=headers, body={"n": 1})
    targets = [TargetConfig(base_url=f"http://{side}", headers={"Content-Type": "text/plain"})
               for side in ("a", "b")]
    with Executor(*targets) as executor:
        executor.execute(case)

    assert [request.url.host for request in seen] == ["a", "b"]
    for request in seen:
        assert request.headers.get_list("cookie") == ["a=1; b=2"]
        assert request.headers.get_list("authorization") == ["Bearer first"]
        assert request.headers.get_list("prefer") == headers["Prefer"]
        [sent_type] = request.headers.get_list("content-type")
        if content_type == "application/json":
            assert sent_type == content_type and request.content == b'{"n":1}'
        else:
            media, boundary = sent_type.split("; boundary=")
            assert media == content_type
            assert request.content.startswith(b"--" + boundary.strip('"').encode() + b"\r\n")
    assert case.headers == headers
