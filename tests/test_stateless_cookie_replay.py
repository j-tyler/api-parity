"""A standalone mismatch must reproduce the Cookie header each target received."""

import json
from pathlib import Path

import httpx
import pytest

from api_parity.artifact_writer import ArtifactWriter
from api_parity.bundle_loader import discover_bundles, load_bundle
from api_parity.cli import dispatch, parse_args
from api_parity.executor import Executor, RequestError
from api_parity.models import (
    ComparisonResult, RequestCase, SecretsConfig, TargetConfig, TargetInfo,
)
from api_parity.schema_validator import SchemaValidator


@pytest.fixture
def wire(monkeypatch):
    """Install a transport below HTTPX cookie handling and capture requests there."""
    seen = []
    set_cookies = {}
    statuses = {}
    original = Executor._build_client_kwargs

    def handle(request):
        host, path = request.url.host, request.url.path
        cookie = request.headers.get("cookie")
        seen.append((host, path, cookie))
        status = statuses.get((host, path, cookie), 200)
        headers = [("set-cookie", value) for value in set_cookies.get((host, path), [])]
        return httpx.Response(status, headers=headers, json={})

    def kwargs(self, target, timeout):
        return {**original(self, target, timeout), "transport": httpx.MockTransport(handle), "trust_env": False}

    monkeypatch.setattr(Executor, "_build_client_kwargs", kwargs)
    return seen, set_cookies, statuses


A = TargetConfig(base_url="https://a.test")
B = TargetConfig(base_url="https://b.test")


def case(path, **kwargs):
    return RequestCase(case_id=path.strip("/"), operation_id=path.strip("/"), method="GET",
                       path_template=path, rendered_path=path, **kwargs)


def save(out, executor, request, redact=()):
    spec = Path(__file__).parent / "fixtures/test_api.yaml"
    writer = ArtifactWriter(out, SecretsConfig(redact_fields=list(redact)), spec_path=spec)
    responses = executor.execute(request)
    diff = ComparisonResult(match=False, mismatch_type="status_code", summary="status", details={})
    bundle = writer.write_mismatch(request, *responses, diff, TargetInfo(name="a", base_url=A.base_url),
                                   TargetInfo(name="b", base_url=B.base_url))
    return bundle, load_bundle(bundle, schema_override=SchemaValidator(spec)).request_case


def test_generated_cookie_dependent_mismatch_survives_two_replays(tmp_path, wire, fixture_cel_evaluator_path):
    seen, cookies, statuses = wire
    expected = {}
    for host in ("a.test", "b.test"):
        cookies[host, "/start"] = [f"sid={host}-root; Path=/", f"sid={host}-balance; Path=/balance",
                                    "unrelated=off-path-value; Path=/elsewhere"]
        cookies[host, "/balance"] = ["sid=rotated; Path=/balance"]
        expected[host] = f"sid={host}-balance; sid={host}-root"
        statuses[host, "/balance", None] = 401
    statuses["b.test", "/balance", expected["b.test"]] = 409

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.3", "info": {"title": "Sessions", "version": "1"},
        "paths": {"/" + op: {"get": {"operationId": op, "responses": {"200": {"description": "ok"}}}}
                  for op in ("start", "balance")},
    }))
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"default_rules": {"status_code": {"predefined": "exact_match"}}}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"targets": {"a": {"base_url": A.base_url}, "b": {"base_url": B.base_url}},
                                  "comparison_rules": str(rules)}))
    flags = ["--config", str(config), "--target-a", "a", "--target-b", "b"]
    source = tmp_path / "explore"
    assert dispatch(parse_args(["explore", *flags, "--spec", str(spec), "--seed", "42", "--out", str(source)])) == 0
    balance_calls = [(host, "/balance", header) for host, header in expected.items()]
    assert [call for call in seen if call[1] == "/balance"] == balance_calls

    for name in ("replay", "again", "fixed"):
        out = tmp_path / name
        seen.clear()
        if name == "fixed":
            statuses["b.test", "/balance", expected["b.test"]] = 200
        assert dispatch(parse_args(["replay", *flags, "--in", str(source), "--out", str(out)])) == 0
        summary = json.loads((out / "replay_summary.json").read_text())
        assert seen == balance_calls
        assert (summary["still_mismatch"], summary["now_match"]) == ((0, 1) if name == "fixed" else (1, 0))
        bundle, = discover_bundles(source)
        for side, header in zip(("target_a", "target_b"), expected.values()):
            trace = json.loads((bundle / f"{side}.json").read_text())
            assert trace["request"]["headers"]["cookie"] == [header]
        assert "off-path-value" not in (bundle / "case.json").read_text()
        source = out

    settings = json.loads(config.read_text())
    settings["secrets"] = {"redact_fields": ["$..cookies.sid"]}
    config.write_text(json.dumps(settings))
    statuses["b.test", "/balance", expected["b.test"]] = 409
    redacted = tmp_path / "redacted"
    assert dispatch(parse_args(["replay", *flags, "--in", str(tmp_path / "explore"), "--out", str(redacted)])) == 0
    seen.clear()
    withheld = tmp_path / "withheld"
    assert dispatch(parse_args(["replay", *flags, "--spec", str(spec), "--in", str(redacted),
                                "--out", str(withheld)])) == 0
    summary = json.loads((withheld / "replay_summary.json").read_text())
    assert (summary["errors"], summary["now_match"], seen) == (1, 0, [])


@pytest.mark.parametrize("explicit", [{}, {"mode": "read"}])
def test_one_sided_session_replays_without_cookies_from_other_bundles(tmp_path, wire, explicit):
    seen, cookies, _ = wire
    cookies["a.test", "/start"] = ["sid=a; Path=/"]
    for host in ("a.test", "b.test"):
        cookies[host, "/balance"] = ["later=other-bundle; Path=/"]
    with Executor(A, B) as executor:
        executor.execute(case("/start"))
        bundle, loaded = save(tmp_path, executor, case("/balance", cookies=explicit), ["$..password"])
        original = seen[-2:]
        assert original == [("a.test", "/balance", "sid=a; mode=read" if explicit else "sid=a"),
                            ("b.test", "/balance", "mode=read" if explicit else None)]
        # Also support normal Python callers that load case.json directly.
        for request in (loaded, RequestCase.model_validate_json((bundle / "case.json").read_bytes())):
            executor.execute(request)
            assert seen[-2:] == original


@pytest.mark.parametrize("selector", [
    "$..cookies['auth.token']", "$.request.headers.cookie", "$..headers['set-cookie']",
    "$.session_cookies.target_b.value", "$..headers", "$..cookies",
    "($.request where method).headers.cookie", "($ where operation_id).session_cookies.target_b.value",
])
def test_redacted_session_requires_an_explicit_cookie_before_sending(tmp_path, wire, selector):
    seen, cookies, _ = wire
    cookies["b.test", "/start"] = ["auth.token=private-b; Path=/"]
    with Executor(A, B) as executor:
        executor.execute(case("/start"))
        bundle, request = save(tmp_path, executor, case("/balance"), [selector])
    assert all(b"private-b" not in path.read_bytes() for path in bundle.iterdir())
    seen.clear()
    with Executor(A, B) as executor:
        with pytest.raises(RequestError, match="target_b session cookie was redacted"):
            executor.execute(request)
    assert seen == []
    with Executor(A, B.model_copy(update={"headers": {"Cookie": "auth.token=fresh"}})) as executor:
        executor.execute(request)
    assert seen == [("a.test", "/balance", None), ("b.test", "/balance", "auth.token=fresh")]


def test_configured_cookie_is_read_from_replay_config_and_case_headers_still_win(tmp_path, wire):
    seen, cookies, _ = wire
    cookies["b.test", "/start"] = ["sid=from-jar; Path=/"]
    with Executor(A, B.model_copy(update={"headers": {"Cookie": "sid=old-config"}})) as executor:
        executor.execute(case("/start"))
        bundle, request = save(tmp_path, executor, case("/balance", cookies={"mode": "read"}))
    assert all(b"old-config" not in path.read_bytes() for path in bundle.iterdir())
    with Executor(A, B.model_copy(update={"headers": {"Cookie": "sid=new-config"}})) as executor:
        executor.execute(request)
        assert seen[-1][2] == "sid=new-config"
        executor.execute(request.model_copy(update={"headers": {"cOoKiE": ["sid=case"]}}))
        assert [call[2] for call in seen[-2:]] == ["sid=case", "sid=case"]


@pytest.mark.parametrize("new_url", ["https://other.test", "http://b.test", "https://b.test/other"])
def test_saved_session_cookie_stays_scoped_to_its_endpoint(tmp_path, wire, new_url):
    seen, cookies, _ = wire
    cookies["b.test", "/start"] = ["sid=b; Path=/balance; Secure"]
    with Executor(A, B) as executor:
        executor.execute(case("/start"))
        _, request = save(tmp_path, executor, case("/balance"))
    seen.clear()
    with Executor(A, TargetConfig(base_url=new_url)) as executor:
        with pytest.raises(RequestError, match="target_b session cookie belongs to a different endpoint"):
            executor.execute(request)
    assert seen == []
