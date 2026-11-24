"""Repeated RequestCase header values are individual HTTP fields on both targets."""

import httpx

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
