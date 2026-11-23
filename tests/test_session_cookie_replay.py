"""Bundle replay must account for the session under which each target was tested."""

import json
from pathlib import Path

import httpx
import pytest

from api_parity.artifact_writer import ArtifactWriter
from api_parity.bundle_loader import discover_bundles, load_bundle
from api_parity.cli import dispatch, parse_args
from api_parity.executor import Executor
from api_parity.models import (
    ChainCase, ChainStep, ComparisonResult, RequestCase, SecretsConfig, TargetConfig, TargetInfo,
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


def save(out, executor, request, redact=(), *, store=False):
    spec = Path(__file__).parent / "fixtures/test_api.yaml"
    writer = ArtifactWriter(out, SecretsConfig(redact_fields=list(redact), store_session_cookies=store), spec_path=spec)
    responses = executor.execute(request)
    diff = ComparisonResult(match=False, mismatch_type="status_code", summary="status", details={})
    bundle = writer.write_mismatch(request, *responses, diff, TargetInfo(name="a", base_url=A.base_url),
                                   TargetInfo(name="b", base_url=B.base_url))
    return bundle, load_bundle(bundle, schema_override=SchemaValidator(spec)).request_case


def configure_cli(tmp_path, *, store=False, operations=("start", "balance")):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "openapi": "3.0.3", "info": {"title": "Sessions", "version": "1"},
        "paths": {"/" + op: {"get": {"operationId": op, "responses": {"200": {"description": "ok"}}}}
                  for op in operations},
    }))
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"default_rules": {"status_code": {"predefined": "exact_match"}}}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"targets": {"a": {"base_url": A.base_url}, "b": {"base_url": B.base_url}},
                                  "secrets": {"store_session_cookies": store}, "comparison_rules": str(rules)}))
    flags = ["--config", str(config), "--target-a", "a", "--target-b", "b"]
    return spec, config, flags


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

    spec, config, flags = configure_cli(tmp_path, store=True)
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
            assert trace["request"]["session_cookies"][side]["cookies"] is None
            assert all(part.split("=")[1] not in (bundle / f"{side}.json").read_text()
                       for part in header.split("; "))
        assert "off-path-value" not in (bundle / "case.json").read_text()
        source = out

    settings = json.loads(config.read_text())
    settings["secrets"] = {"store_session_cookies": True, "redact_fields": ["$..cookies.sid"]}
    config.write_text(json.dumps(settings))
    statuses["b.test", "/balance", expected["b.test"]] = 409
    redacted = tmp_path / "redacted"
    assert dispatch(parse_args(["replay", *flags, "--in", str(tmp_path / "explore"), "--out", str(redacted)])) == 0
    seen.clear()
    withheld = tmp_path / "withheld"
    assert dispatch(parse_args(["replay", *flags, "--spec", str(spec), "--in", str(redacted),
                                "--out", str(withheld)])) == 0
    summary = json.loads((withheld / "replay_summary.json").read_text())
    assert (summary["errors"], summary["now_match"], summary["incomplete"]) == (0, 0, 1)
    assert seen == [(host, "/balance", None) for host in expected]
    name, = summary["incomplete_bundles"]
    assert summary["session_bundles"][name] == {"target_a": ["omitted"], "target_b": ["omitted"]}


@pytest.mark.parametrize("explicit", [{}, {"mode": "read"}])
def test_one_sided_session_replays_without_cookies_from_other_bundles(tmp_path, wire, explicit):
    seen, cookies, _ = wire
    cookies["a.test", "/start"] = ["sid=a; Path=/"]
    for host in ("a.test", "b.test"):
        cookies[host, "/balance"] = ["later=other-bundle; Path=/"]
    with Executor(A, B) as executor:
        executor.execute(case("/start"))
        bundle, loaded = save(tmp_path, executor, case("/balance", cookies=explicit), ["$..password"], store=True)
        original = seen[-2:]
        assert original == [("a.test", "/balance", "sid=a; mode=read" if explicit else "sid=a"),
                            ("b.test", "/balance", "mode=read" if explicit else None)]
        # Also support normal Python callers that load case.json directly.
        for request in (loaded, RequestCase.model_validate_json((bundle / "case.json").read_bytes())):
            executor.execute(request)
            assert seen[-2:] == original


@pytest.mark.parametrize("selector", [
    "$..cookies['auth.token']", "$.request.headers.cookie", "$..headers['set-cookie']",
    "$.session_cookies.target_b.cookies[0].value", "$..headers", "$..cookies",
    "($.request where body).headers.cookie", "($.request where method).headers.cookie", "($ where operation_id).session_cookies.target_b.cookies[0].value",
])
def test_redacted_session_is_omitted_unless_caller_supplies_a_cookie(tmp_path, wire, selector):
    seen, cookies, _ = wire
    cookies["b.test", "/start"] = ["auth.token=private-b; Path=/"]
    with Executor(A, B) as executor:
        executor.execute(case("/start"))
        bundle, request = save(tmp_path, executor, case("/balance"), [selector], store=True)
    assert all(b"private-b" not in path.read_bytes() for path in bundle.iterdir())
    seen.clear()
    with Executor(A, B) as executor:
        _, response = executor.execute(request)
    assert response.session_replay == "omitted"
    assert seen == [("a.test", "/balance", None), ("b.test", "/balance", None)]
    seen.clear()
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


@pytest.mark.parametrize("new_url", ["https://other.test", "http://b.test", "https://b.test:8443", "https://b.test/other"])
def test_saved_session_cookie_stays_scoped_to_its_endpoint(tmp_path, wire, new_url):
    seen, cookies, _ = wire
    cookies["b.test", "/start"] = ["sid=b; Path=/balance; Secure"]
    with Executor(A, B) as executor:
        executor.execute(case("/start"))
        _, request = save(tmp_path, executor, case("/balance"), store=True)
    seen.clear()
    with Executor(A, TargetConfig(base_url=new_url)) as executor:
        _, response = executor.execute(request)
    assert len(seen) == 2 and [call[2] for call in seen] == [None, None]
    assert response.session_replay == "omitted"


def run_cli(command, flags, out, *args):
    assert dispatch(parse_args([command, *flags, "--out", str(out), *map(str, args)])) == 0
    name = "replay_summary.json" if command == "replay" else "summary.json"
    return json.loads((out / name).read_text())


def test_new_deployment_runs_without_sending_saved_credentials(tmp_path, wire, fixture_cel_evaluator_path):
    seen, cookies, statuses = wire
    spec, config, flags = configure_cli(tmp_path, store=True)
    for host in ("a.test", "b.test"):
        cookies[host, "/start"] = ["route=old-deployment; Path=/"]
    statuses["b.test", "/balance", "route=old-deployment"] = 500
    source = tmp_path / "explore"
    assert run_cli("explore", flags, source, "--spec", spec, "--seed", 42)["mismatches"] == 1

    settings = json.loads(config.read_text())
    settings["targets"] = {"a": {"base_url": "https://new-a.test"}, "b": {"base_url": "https://new-b.test"}}
    config.write_text(json.dumps(settings))
    statuses["new-b.test", "/balance", None] = 500
    seen.clear()
    out = tmp_path / "new"
    result = run_cli("replay", flags, out, "--in", source)
    assert seen == [("new-a.test", "/balance", None), ("new-b.test", "/balance", None)]
    assert (result["still_mismatch"], result["errors"]) == (1, 0)
    name, = result["persistent_bundles"]
    assert result["session_bundles"][name] == {"target_a": ["omitted"], "target_b": ["omitted"]}

    statuses["new-b.test", "/balance", None] = 200
    repeated = run_cli("replay", flags, tmp_path / "again", "--in", out)
    assert repeated["incomplete"] == 1 and repeated["now_match"] == 0
    ignored = run_cli("replay", flags, tmp_path / "ignored", "--in", out, "--ignore-session-cookies")
    assert ignored["now_match"] == 1 and ignored["incomplete"] == 0


def test_default_does_not_persist_inherited_values_and_reports_the_missing_session(
    tmp_path, wire, fixture_cel_evaluator_path
):
    seen, cookies, statuses = wire
    spec, config, flags = configure_cli(tmp_path)
    # Exercise an existing config with no secrets section.
    settings = json.loads(config.read_text())
    del settings["secrets"]
    config.write_text(json.dumps(settings))
    for host in ("a.test", "b.test"):
        cookies[host, "/start"] = ["sid=fixture-only-value; Path=/"]
        statuses[host, "/balance", None] = 401
    statuses["b.test", "/balance", "sid=fixture-only-value"] = 409
    source = tmp_path / "explore"
    assert run_cli("explore", flags, source, "--spec", spec, "--seed", 42)["mismatches"] == 1
    assert all(b"fixture-only-value" not in path.read_bytes() for path in source.rglob("*") if path.is_file())
    seen.clear()
    result = run_cli("replay", flags, tmp_path / "without-session", "--in", source)
    assert seen == [("a.test", "/balance", None), ("b.test", "/balance", None)]
    assert (result["incomplete"], result["now_match"], result["errors"]) == (1, 0, 0)
    for target in settings["targets"].values():
        target["headers"] = {"Cookie": "sid=current"}
    config.write_text(json.dumps(settings))
    statuses["b.test", "/balance", "sid=current"] = 409
    supplied = run_cli("replay", flags, tmp_path / "configured", "--in", source)
    assert supplied["still_mismatch"] == 1
    assert seen[-2:] == [("a.test", "/balance", "sid=current"), ("b.test", "/balance", "sid=current")]


@pytest.mark.parametrize("denied", [401, 403])
def test_new_authentication_denial_cannot_clear_the_saved_failure(tmp_path, wire, fixture_cel_evaluator_path, denied):
    seen, cookies, statuses = wire
    spec, _, flags = configure_cli(tmp_path, store=True)
    for host in ("a.test", "b.test"):
        cookies[host, "/start"] = ["sid=has-expired; Path=/"]
    statuses["b.test", "/balance", "sid=has-expired"] = 409
    source = tmp_path / "explore"
    assert run_cli("explore", flags, source, "--spec", spec, "--seed", 42)["mismatches"] == 1
    for host in ("a.test", "b.test"):
        statuses[host, "/balance", "sid=has-expired"] = denied
    seen.clear()
    result = run_cli("replay", flags, tmp_path / "expired", "--in", source)
    assert seen == [("a.test", "/balance", "sid=has-expired"), ("b.test", "/balance", "sid=has-expired")]
    name, = result["incomplete_bundles"]
    assert result["session_bundles"][name] == {"target_a": ["restored", "auth_rejected"],
                                               "target_b": ["restored", "auth_rejected"]}
    assert (result["now_match"], result["errors"]) == (0, 0)

    for host in ("a.test", "b.test"):
        statuses[host, "/balance", "sid=has-expired"] = 200
    assert run_cli("replay", flags, tmp_path / "fixed", "--in", source)["now_match"] == 1


@pytest.mark.parametrize("store,delete", [(False, False), (True, False), (True, True)])
def test_chain_reuses_external_cookies_and_renews_its_own(tmp_path, monkeypatch, wire,
                                                         fixture_cel_evaluator_path, store, delete):
    from api_parity.case_generator import CaseGenerator

    seen, cookies, statuses = wire
    spec, _, flags = configure_cli(tmp_path, store=store, operations=("start", "profile", "balance"))

    def chain(*ops):
        return ChainCase(chain_id=ops[-1], steps=[
            ChainStep(step_index=i, request_template=case("/" + op)) for i, op in enumerate(ops)
        ])

    # The public stateful runner executes one setup chain, then an independent two-step chain.
    monkeypatch.setattr(CaseGenerator, "generate_chains", lambda *a, **kw: [chain("start"), chain("profile", "balance")])
    for host in ("a.test", "b.test"):
        cookies[host, "/start"] = [f"sid=boundary-{host}; Path=/balance", "unused=off-path-value; Path=/other"]
        cookies[host, "/profile"] = ["sid=original-chain; Path=/"]
    statuses["b.test", "/balance", "sid=boundary-b.test; sid=original-chain"] = 409
    source = tmp_path / "explore"
    assert run_cli("explore", flags, source, "--spec", spec, "--stateful")["chain_mismatches"] == 1
    bundle, = discover_bundles(source)
    saved = (bundle / "chain.json").read_text()
    assert "original-chain" not in saved and "off-path-value" not in saved
    assert ("boundary-b.test" in saved) is store
    if not store:
        assert all(b"boundary-b.test" not in path.read_bytes() for path in bundle.iterdir())

    for host in ("a.test", "b.test"):
        cookies[host, "/profile"] = ["sid=renewed; Path=/"]
        if delete:
            cookies[host, "/profile"].append("sid=; Max-Age=0; Path=/balance")
        statuses[host, "/balance", "sid=renewed"] = 401
    statuses["b.test", "/balance", "sid=boundary-b.test; sid=renewed"] = 409
    seen.clear()
    result = run_cli("replay", flags, tmp_path / "replayed", "--in", source)
    reproducible = store and not delete
    assert seen == [("a.test", "/profile", None), ("b.test", "/profile", None),
                    ("a.test", "/balance", "sid=boundary-a.test; sid=renewed" if reproducible else "sid=renewed"),
                    ("b.test", "/balance", "sid=boundary-b.test; sid=renewed" if reproducible else "sid=renewed")]
    assert (result["still_mismatch"], result["incomplete"], result["now_match"]) == (
        (1, 0, 0) if reproducible else (0, 1, 0)
    )


def test_a_previously_denied_session_can_replay_as_fixed(tmp_path, wire, fixture_cel_evaluator_path):
    _, cookies, statuses = wire
    spec, _, flags = configure_cli(tmp_path, store=True)
    for host, code in (("a.test", 401), ("b.test", 403)):
        cookies[host, "/start"] = ["sid=denied; Path=/"]
        statuses[host, "/balance", "sid=denied"] = code
    source = tmp_path / "explore"
    assert run_cli("explore", flags, source, "--spec", spec, "--seed", 42)["mismatches"] == 1
    statuses["b.test", "/balance", "sid=denied"] = 401
    result = run_cli("replay", flags, tmp_path / "same-denial", "--in", source)
    assert (result["now_match"], result["incomplete"]) == (1, 0)


@pytest.mark.parametrize("selector", [
    "$.steps[0].request.headers.cookie",
    "$.steps[0].request_template.session_cookies.target_a.cookies[0].value",
])
def test_chain_redaction_protects_repeated_copies_of_an_inherited_cookie(tmp_path, wire, selector):
    seen, cookies, _ = wire
    for host in ("a.test", "b.test"):
        cookies[host, "/start"] = ["sid=synthetic-shared; Path=/"]
    spec = Path(__file__).parent / "fixtures/test_api.yaml"
    secret = SecretsConfig(store_session_cookies=True, redact_fields=[selector])
    chain = ChainCase(chain_id="shared", steps=[
        ChainStep(step_index=i, request_template=case(path)) for i, path in enumerate(("/profile", "/balance"))
    ])
    matched = ComparisonResult(match=True, summary="ok", details={})
    diff = ComparisonResult(match=False, mismatch_type="status_code", summary="status", details={})
    with Executor(A, B) as executor:
        executor.execute(case("/start"))
        traces = executor.execute_chain(chain)
    bundle = ArtifactWriter(tmp_path, secret, spec_path=spec).write_chain_mismatch(
        chain, *traces, [matched, diff], 1, TargetInfo(name="a", base_url=A.base_url), TargetInfo(name="b", base_url=B.base_url),
    )
    assert all(b"synthetic-shared" not in path.read_bytes() for path in bundle.iterdir())
    seen.clear()
    with Executor(A, B) as executor:
        executor.execute_chain(load_bundle(bundle, schema_override=SchemaValidator(spec)).chain_case)
    assert len(seen) == 4 and all(header is None for _, _, header in seen)


def test_chain_session_can_be_rejected_after_the_chain_issues_its_own_cookie(
    tmp_path, monkeypatch, wire, fixture_cel_evaluator_path
):
    from api_parity.case_generator import CaseGenerator

    seen, cookies, statuses = wire
    spec, _, flags = configure_cli(tmp_path, store=True, operations=("start", "profile", "balance"))
    chains = [ChainCase(chain_id=ops[-1], steps=[ChainStep(step_index=i, request_template=case("/" + op))
                                                 for i, op in enumerate(ops)])
              for ops in (("start",), ("profile", "balance"))]
    monkeypatch.setattr(CaseGenerator, "generate_chains", lambda *a, **kw: chains)
    for host in ("a.test", "b.test"):
        cookies[host, "/start"] = ["sid=boundary; Path=/profile"]
        cookies[host, "/profile"] = ["cart=original; Path=/balance"]
    statuses["b.test", "/balance", "cart=original"] = 409
    source = tmp_path / "explore"
    assert run_cli("explore", flags, source, "--spec", spec, "--stateful")["chain_mismatches"] == 1
    for host in ("a.test", "b.test"):
        cookies[host, "/profile"] = ["cart=current; Path=/balance"]
        statuses[host, "/balance", "cart=current"] = 401
    seen.clear()
    result = run_cli("replay", flags, tmp_path / "replay", "--in", source)
    assert seen == [("a.test", "/profile", "sid=boundary"), ("b.test", "/profile", "sid=boundary"),
                    ("a.test", "/balance", "cart=current"), ("b.test", "/balance", "cart=current")]
    name, = result["incomplete_bundles"]
    assert result["now_match"] == 0 and "auth_rejected" in result["session_bundles"][name]["target_b"]
