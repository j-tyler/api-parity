"""An unresolved live link must not turn a matching prefix into a successful chain."""

import json

from api_parity.case_generator import CaseGenerator
from api_parity.cli import dispatch, parse_args
from api_parity.executor import Executor
from api_parity.models import ChainCase, ChainStep, RequestCase, ResponseCase


def test_chain_outcomes_depend_on_steps_actually_compared(
    tmp_path, monkeypatch, capsys, fixture_cel_evaluator_path,
):
    # Keep link extraction, execution, comparison and bundle replay real.
    # Fix the generated chain and the transport responses for reproducibility.
    operations = [("createItem", "/items"), ("getItem", "/items/{id}"), ("finish", "/finish")]
    link = {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}
    paths = {}
    steps = []
    for index, (op, path) in enumerate(operations):
        response = {"description": "success"}
        if index == 0:
            response["links"] = {"read": link}
        paths[path] = {"get": {"operationId": op, "responses": {"200": response}}}
        if index == 1:
            paths[path]["get"]["parameters"] = [
                {"in": "path", "name": "id", "required": True, "schema": {"type": "string"}}
            ]
        request = RequestCase(case_id=op, operation_id=op, method="GET",
                              path_template=path, rendered_path=path.replace("{id}", "seed"),
                              path_parameters={"id": "seed"} if index == 1 else {})
        steps.append(ChainStep(step_index=index, request_template=request,
                               link_source=link if index == 1 else None))
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

    def send(self, client, request, timeout, target_name):
        calls.append(request.operation_id)
        status = 404 if (mode == "mismatch" and request.operation_id == "getItem"
                         and target_name == "Target B") else 200
        body = {"id": "live"} if mode != "blocked" else {}
        return ResponseCase(status_code=status, body=body, elapsed_ms=1)

    monkeypatch.setattr(Executor, "_execute_single", send)

    def run(command, directory, *flags):
        calls.clear()
        assert dispatch(parse_args([command, *common, "--out", str(directory), *flags])) == 0
        filename = "summary.json" if command == "explore" else "replay_summary.json"
        return json.loads((directory / filename).read_text())

    source = tmp_path / "source"
    explore_flags = ["--spec", str(spec), "--stateful", "--log-chains"]
    assert run("explore", source, *explore_flags)["chain_mismatches"] == 1
    assert calls == ["createItem"] * 2 + ["getItem"] * 2
    replay_flags = ["--in", str(source)]
    assert run("replay", tmp_path / "persistent", *replay_flags)["still_mismatch"] == 1

    mode = "blocked"
    blocked = tmp_path / "blocked"
    summary = run("explore", blocked, *explore_flags, "--ensure-coverage")
    assert (summary["chain_errors"], summary["chain_matches"], summary["chain_mismatches"]) == (1, 0, 0)
    # The final two operations are backfilled as single requests outside the chain.
    assert calls == ["createItem"] * 2 + ["getItem"] * 2 + ["finish"] * 2
    log = (blocked / "chains.txt").read_text()
    assert "Steps: 1" in log and "Outcome: ERROR" in log and "Links traversed: 0" in log
    capsys.readouterr()
    summary = run("replay", tmp_path / "incomplete", *replay_flags)
    assert calls == ["createItem"] * 2
    assert summary["errors"] == 1 and summary["fixed_bundles"] == [] and summary["now_match"] == 0
    assert "compared 1/3 steps. Step 1 (getItem)" in capsys.readouterr().out

    mode = "fixed"
    assert run("replay", tmp_path / "fixed", *replay_flags)["now_match"] == 1
    assert calls == ["createItem"] * 2 + ["getItem"] * 2 + ["finish"] * 2
