"""An unresolved live link must not turn a matching prefix into a successful chain."""

import json

from api_parity.case_generator import CaseGenerator
from api_parity.cli import dispatch, parse_args
from api_parity.executor import Executor, RequestError
from api_parity.models import ChainCase, ChainStep, RequestCase, ResponseCase


def test_chain_outcomes_depend_on_steps_actually_compared(
    tmp_path, monkeypatch, capsys, fixture_cel_evaluator_path,
):
    # Keep link extraction, execution, comparison and bundle replay real.
    # Fix the generated chain and the transport responses for reproducibility.
    operations = [("createItem", "/items"), ("getItem", "/items/{id}/owners/{owner}"), ("finish", "/finish/{child}")]
    link = {"operationId": "getItem", "parameters": {"id": "$response.body#/id", "owner": "$response.body#/owner"}}
    child = {"operationId": "finish", "parameters": {"child": "$response.body#/child_id"}}
    paths = {}
    steps = []
    for index, (op, path) in enumerate(operations):
        response = {"description": "success"}
        if index == 0:
            response["links"] = {"read": link}
        if index == 1:
            response["links"] = {"child": child}
        paths[path] = {"get": {"operationId": op, "responses": {"200": response}}}
        path_params = {}
        if index > 0:
            path_params = dict.fromkeys(("id", "owner") if index == 1 else ("child",), "seed")
            paths[path]["get"]["parameters"] = [
                {"in": "path", "name": param, "required": True, "schema": {"type": "string"}}
                for param in path_params
            ]
        request = RequestCase(case_id=op, operation_id=op, method="GET",
                              path_template=path, rendered_path=path.format(**path_params),
                              path_parameters=path_params)
        steps.append(ChainStep(step_index=index, request_template=request,
                               link_source=[None, link, child][index]))
    chain = ChainCase(chain_id="chain", steps=steps)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"openapi": "3.0.3", "info": {"title": "test", "version": "1"},
                                "paths": paths}))
    (tmp_path / "rules.json").write_text(json.dumps({
        "default_rules": {"status_code": {"predefined": "exact_match"}}
    }))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"targets": {t: {"base_url": f"http://{t}"} for t in ("a", "b")},
                                 "comparison_rules": "rules.json"}))
    common = ["--config", str(config), "--target-a", "a", "--target-b", "b"]
    monkeypatch.setattr(CaseGenerator, "generate_chains", lambda *a, **kw: [chain])
    monkeypatch.setattr(CaseGenerator, "generate", lambda *a, **kw: (s.request_template for s in steps))
    mode = "mismatch"
    calls = []

    def send(self, client, request, timeout, target_name, **kwargs):
        calls.append(request.operation_id)
        if mode == "transport":
            raise RequestError("connection timeout")
        status = 404 if (mode == "mismatch" and request.operation_id == "getItem"
                         and target_name == "Target B") else 200
        if mode == "blocked":
            status = 422
        if mode == "later mismatch" and request.operation_id == "finish" and target_name == "Target B":
            status = 404
        body = {"id": "live"} if mode != "blocked" else {}
        if request.operation_id == "createItem" and mode not in ("blocked", "missing owner"):
            body["owner"] = "owner-live"
        if request.operation_id == "getItem" and mode != "tail blocked":
            body["child_id"] = "child"
        return ResponseCase(status_code=status, body=body, elapsed_ms=1)

    monkeypatch.setattr(Executor, "_execute_single", send)

    def run(command, directory, *flags):
        calls.clear()
        assert dispatch(parse_args([command, *common, "--out", str(directory), *flags])) == 0
        filename = "summary.json" if command == "explore" else "replay_summary.json"
        return json.loads((directory / filename).read_text())

    source = tmp_path / "source"
    explore_flags = ["--spec", str(spec), "--stateful", "--log-chains", "--ensure-coverage"]
    assert run("explore", source, *explore_flags)["chain_mismatches"] == 1
    assert calls == ["createItem"] * 2 + ["getItem"] * 2
    log = (source / "chains.txt").read_text()
    assert "createItem -> getItem -> finish" in log and "Compared: 2/3" in log
    assert "Outcome: MISMATCH" in log and "Links traversed: 1" in log
    replay_flags = ["--in", str(source)]
    assert run("replay", tmp_path / "persistent", *replay_flags)["still_mismatch"] == 1

    mode = "blocked"
    blocked = tmp_path / "blocked"
    summary = run("explore", blocked, *explore_flags)
    assert (summary["chain_errors"], summary["chain_matches"], summary["chain_mismatches"]) == (0, 0, 0)
    assert summary["chain_incomplete"] == 1
    assert calls == ["createItem"] * 2
    log = (blocked / "chains.txt").read_text()
    assert "Compared: 1/3" in log and "Outcome: INCOMPLETE" in log and "Links traversed: 0" in log
    capsys.readouterr()
    summary = run("replay", tmp_path / "incomplete", *replay_flags)
    assert calls == ["createItem"] * 2
    assert summary["incomplete"] == 1
    assert summary["incomplete_bundles"] == [next((source / "mismatches").iterdir()).name]
    assert summary["errors"] == 0 and summary["fixed_bundles"] == [] and summary["now_match"] == 0
    output = capsys.readouterr().out
    assert "compared 1/3 steps. Step 1 (getItem)" in output and "$response.body#/id" in output

    mode = "missing owner"
    assert run("explore", tmp_path / "partial_explore", *explore_flags)["chain_incomplete"] == 1
    assert calls == ["createItem"] * 2
    result = run("replay", tmp_path / "partial_replay", *replay_flags)
    assert result["incomplete"] == 1 and result["now_match"] == 0
    assert calls == ["createItem"] * 2

    mode = "tail blocked"
    result = run("replay", tmp_path / "verified_prefix", *replay_flags)
    assert result["now_match"] == 1 and result["incomplete"] == result["errors"] == 0
    assert calls == ["createItem"] * 2 + ["getItem"] * 2
    assert "original mismatch at step 1 passed" in capsys.readouterr().out

    mode = "later mismatch"
    assert run("replay", tmp_path / "changed", *replay_flags)["different_mismatch"] == 1

    mode = "transport"
    failed = tmp_path / "transport"
    result = run("explore", failed, *explore_flags)
    assert result["chain_errors"] == 1 and result["chain_incomplete"] == 0
    assert calls == ["createItem"]
    log = (failed / "chains.txt").read_text()
    assert "createItem -> getItem -> finish" in log and "Compared: 0/3" in log
    assert "Outcome: ERROR" in log and "Links traversed: 0" in log

    mode = "fixed"
    assert run("replay", tmp_path / "fixed", *replay_flags)["now_match"] == 1
    assert calls == ["createItem"] * 2 + ["getItem"] * 2 + ["finish"] * 2
