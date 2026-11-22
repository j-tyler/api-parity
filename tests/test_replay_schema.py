"""Replay must repeat the schema checks that produced an explore failure."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from api_parity.artifact_writer import ArtifactWriter, BUNDLE_SPEC_FILENAME
from api_parity.bundle_loader import BundleLoadError, discover_bundles, load_bundle
from api_parity.bundle_merger import merge_bundles
from api_parity.case_generator import CaseGenerator
from api_parity.cel_evaluator import CELEvaluator
from api_parity.cli import dispatch, parse_args
from api_parity.comparator import Comparator
from api_parity.config_loader import load_comparison_library
from api_parity.executor import Executor
from api_parity.models import (
    ChainCase, ChainExecution, ChainStep, ChainStepExecution, OperationRules,
    RequestCase, ResponseCase, TargetInfo,
)
from api_parity.schema_validator import SchemaValidator


@pytest.fixture
def replay_env(tmp_path, monkeypatch, fixture_cel_evaluator_path):
    # All requests are intercepted below; ignore host proxy configuration.
    for name in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"version": "1", "default_rules": {}}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "targets": {"a": {"base_url": "http://a"}, "b": {"base_url": "http://b"}},
        "comparison_rules": str(rules),
    }))
    bodies = [{"id": "invalid"}, {"id": "invalid"}]
    send = Mock(side_effect=lambda client, request, timeout, target_name: ResponseCase(
        status_code=200, body=bodies[0 if target_name == "Target A" else 1], elapsed_ms=1,
    ))
    monkeypatch.setattr(Executor, "_execute_single", send)
    return config, bodies, send


def write_spec(path, id_type="integer"):
    path.write_text(json.dumps({
        "openapi": "3.0.3", "info": {"title": "Replay", "version": "1"},
        "paths": {"/items": {"get": {"operationId": "getItems", "responses": {
            "200": {"description": "OK", "content": {"application/json": {"schema": {
                "$ref": "#/components/schemas/Item",
            }}}},
        }}}},
        "components": {"schemas": {"Item": {
            "type": "object", "required": ["id"],
            "properties": {"id": {"type": id_type}},
        }}},
    }))
    return path


def request(case_id="case"):
    return RequestCase(case_id=case_id, operation_id="getItems", method="GET",
                       path_template="/items", rendered_path="/items")


def make_bundle(out, spec, bodies, chain=False, legacy=False, case_id="case"):
    responses = [ResponseCase(status_code=200, body=b, elapsed_ms=1) for b in bodies]
    with CELEvaluator() as cel:
        diff = Comparator(cel, load_comparison_library(), SchemaValidator(spec)).compare(
            *responses, OperationRules(), "getItems",
        )
    assert not diff.match
    writer = ArtifactWriter(out, spec_path=None if legacy else spec)
    info = TargetInfo(name="test", base_url="http://test")
    case = request(case_id)
    if not chain:
        return writer.write_mismatch(case, *responses, diff, info, info)
    chain_case = ChainCase(chain_id=case_id, steps=[ChainStep(step_index=0, request_template=case)])
    executions = [ChainExecution(steps=[ChainStepExecution(
        step_index=0, request=case, response=r,
    )]) for r in responses]
    return writer.write_chain_mismatch(chain_case, *executions, [diff], 0, info, info)


def replay(config, source, out, *flags, expected_exit=0):
    result = dispatch(parse_args([
        "replay", "--config", str(config), "--target-a", "a", "--target-b", "b",
        "--in", str(source), "--out", str(out), *flags,
    ]))
    assert result == expected_exit
    return json.loads((out / "replay_summary.json").read_text())


def test_explore_records_contract_and_replay_preserves_it(tmp_path, monkeypatch, replay_env):
    config, bodies, _ = replay_env
    spec = write_spec(tmp_path / "source.json")
    original_bytes = spec.read_bytes()
    monkeypatch.setattr(CaseGenerator, "generate", lambda *a, **kw: iter([request()]))
    explore_out = tmp_path / "explore"
    assert dispatch(parse_args([
        "explore", "--spec", str(spec), "--config", str(config),
        "--target-a", "a", "--target-b", "b", "--out", str(explore_out),
    ])) == 0
    bundle = discover_bundles(explore_out)[0]
    assert (bundle / BUNDLE_SPEC_FILENAME).read_bytes() == original_bytes
    spec.unlink()  # Replay must be independent of the original file's location.
    summary = replay(config, explore_out, tmp_path / "replay")
    assert summary["still_mismatch"] == 1
    assert summary["now_match"] == 0
    persisted = discover_bundles(tmp_path / "replay")[0]
    assert (persisted / BUNDLE_SPEC_FILENAME).read_bytes() == original_bytes
    assert replay(config, tmp_path / "replay", tmp_path / "again")["still_mismatch"] == 1
    bodies[:] = [{"id": 1}, {"id": 1}]
    assert replay(config, explore_out, tmp_path / "fixed")["now_match"] == 1


@pytest.mark.parametrize("chain", [False, True])
@pytest.mark.parametrize("extra_fields", [False, True])
def test_schema_and_extra_field_failures_survive_replay(tmp_path, replay_env, chain, extra_fields):
    config, bodies, _ = replay_env
    if extra_fields:
        bodies[:] = [{"id": 1, "extra": "a"}, {"id": 1, "extra": "b"}]
    spec = write_spec(tmp_path / "spec.json")
    source = tmp_path / "explore"
    make_bundle(source, spec, bodies, chain=chain)
    result = replay(config, source, tmp_path / "replay")
    assert result["still_mismatch"] == 1
    assert result["now_match"] == 0
    assert result["errors"] == 0
    bodies[:] = [{"id": 1}, {"id": 1}]
    assert replay(config, source, tmp_path / "fixed")["now_match"] == 1


@pytest.mark.parametrize("chain", [False, True])
@pytest.mark.parametrize("extra_fields", [False, True])
def test_legacy_schema_failures_are_unverifiable_until_spec_supplied(
    tmp_path, replay_env, chain, extra_fields,
):
    config, bodies, send = replay_env
    if extra_fields:
        bodies[:] = [{"id": 1, "extra": "a"}, {"id": 1, "extra": "b"}]
    spec = write_spec(tmp_path / "spec.json")
    source = tmp_path / "explore"
    bundle = make_bundle(source, spec, bodies, chain=chain, legacy=True)
    assert load_bundle(bundle).schema_validator is None
    result = replay(config, source, tmp_path / "replay", expected_exit=1)
    assert result["errors"] == 1
    assert result["unverifiable_bundles"] == [bundle.name]
    assert result["now_match"] == 0
    assert send.call_count == 2
    assert replay(config, source, tmp_path / "recovered", "--spec", str(spec))["still_mismatch"] == 1


def test_merged_bundles_use_their_own_contracts_and_legacy_still_loads(tmp_path, replay_env):
    config, bodies, _ = replay_env
    integer_spec = write_spec(tmp_path / "integer.json")
    string_spec = write_spec(tmp_path / "string.json", "string")
    first, second, legacy = (tmp_path / name for name in ("first", "second", "legacy"))
    make_bundle(first, integer_spec, bodies, case_id="integer")
    # Use an extra-field failure so merge preserves both patterns for the same operation.
    make_bundle(second, string_spec, [{"id": "x", "extra": 1}, {"id": "x", "extra": 2}], case_id="string")
    legacy_path = make_bundle(legacy, integer_spec, bodies, legacy=True, case_id="legacy")
    # A historical status failure still replays without a schema snapshot.
    diff_path = legacy_path / "diff.json"
    diff = json.loads(diff_path.read_text())
    diff.update(mismatch_type="status_code", details={})
    diff_path.write_text(json.dumps(diff))
    merged = tmp_path / "merged"
    merge_bundles([first, second, legacy], merged)
    result = replay(config, merged, tmp_path / "replay")
    assert result["total_bundles"] == 3
    assert result["still_mismatch"] == 1  # integer contract still rejects strings
    assert result["now_match"] == 2  # string contract and legacy status test pass
    assert result["errors"] == 0


@pytest.mark.parametrize("content", ["{", "{}", "null", "paths: []"])
def test_corrupt_snapshot_is_not_silently_ignored(tmp_path, replay_env, content):
    config, bodies, _ = replay_env
    spec = write_spec(tmp_path / "spec.json")
    source = tmp_path / "explore"
    bundle = make_bundle(source, spec, bodies)
    (bundle / BUNDLE_SPEC_FILENAME).write_text(content)
    with pytest.raises(BundleLoadError, match="Invalid openapi.json"):
        load_bundle(bundle)
    assert dispatch(parse_args([
        "replay", "--config", str(config), "--target-a", "a", "--target-b", "b",
        "--in", str(source), "--out", str(tmp_path / "replay"), "--validate",
    ])) == 1


def test_repeated_merge_does_not_attach_stale_schema_to_legacy_bundle(tmp_path, replay_env):
    _, bodies, _ = replay_env
    spec = write_spec(tmp_path / "spec.json")
    source, merged = tmp_path / "source", tmp_path / "merged"
    bundle = make_bundle(source, spec, bodies)
    merge_bundles([source], merged)
    (bundle / BUNDLE_SPEC_FILENAME).unlink()
    merge_bundles([source], merged)
    assert load_bundle(discover_bundles(merged)[0]).schema_validator is None


@pytest.mark.parametrize("suffix", [".yaml", ".yml"])
def test_yaml_input_preserves_format_and_native_examples(tmp_path, replay_env, suffix):
    import yaml

    config, bodies, _ = replay_env
    original = write_spec(tmp_path / "spec.json")
    spec = json.loads(original.read_text())
    from datetime import date
    spec["components"]["schemas"]["Item"]["example"] = {"date": date(2026, 1, 1)}
    yaml_path = tmp_path / f"source{suffix}"
    yaml_path.write_text(yaml.safe_dump(spec))
    source = tmp_path / "out"
    bundle = make_bundle(source, yaml_path, bodies)
    assert (bundle / "openapi.yaml").read_bytes() == yaml_path.read_bytes()
    assert load_bundle(bundle).schema_validator is not None
    assert replay(config, source, tmp_path / "bad")["still_mismatch"] == 1
    persisted = discover_bundles(tmp_path / "bad")[0]
    assert (persisted / "openapi.yaml").read_bytes() == yaml_path.read_bytes()
    bodies[:] = [{"id": 1}, {"id": 1}]
    assert replay(config, source, tmp_path / "fixed")["now_match"] == 1


def test_exponent_numeric_constraints_keep_their_type(tmp_path, replay_env):
    config, bodies, _ = replay_env
    spec_path = write_spec(tmp_path / "spec.json", "number")
    spec = json.loads(spec_path.read_text())
    spec["components"]["schemas"]["Item"]["properties"]["id"]["maximum"] = 1e20
    spec_path.write_text(json.dumps(spec))
    bodies[:] = [{"id": 2e20}, {"id": 2e20}]
    source = tmp_path / "source"
    make_bundle(source, spec_path, bodies)
    assert replay(config, source, tmp_path / "bad")["still_mismatch"] == 1
    bodies[:] = [{"id": 1e20}, {"id": 1e20}]
    assert replay(config, source, tmp_path / "fixed")["now_match"] == 1


def test_ambiguous_snapshot_is_rejected(tmp_path, replay_env):
    _, bodies, _ = replay_env
    spec = write_spec(tmp_path / "spec.json")
    bundle = make_bundle(tmp_path / "out", spec, bodies)
    (bundle / "openapi.yaml").write_bytes(spec.read_bytes())
    with pytest.raises(BundleLoadError, match="both openapi.json and openapi.yaml"):
        load_bundle(bundle)


@pytest.mark.parametrize("chain", [False, True])
def test_legacy_rule_failure_is_reported(tmp_path, replay_env, chain):
    config, bodies, _ = replay_env
    spec = write_spec(tmp_path / "spec.json")
    make_bundle(tmp_path / "source", spec, bodies, chain=chain, legacy=True)
    (tmp_path / "rules.json").write_text(json.dumps({"default_rules": {
        "body": {"field_rules": {"$.id": {"predefined": "exact_match"}}},
    }}))
    bodies[1] = {"id": 5}
    result = replay(config, tmp_path / "source", tmp_path / "out")
    assert result["different_mismatch"] == 1
    assert result["errors"] == 0


@pytest.mark.parametrize("chain", [False, True])
def test_changed_violation_path_and_spec_override(tmp_path, replay_env, chain):
    config, bodies, _ = replay_env
    spec = write_spec(tmp_path / "spec.json")
    document = json.loads(spec.read_bytes())
    props = document["components"]["schemas"]["Item"]["properties"]
    props["name"] = {"type": "string"}
    spec.write_text(json.dumps(document))
    source = tmp_path / "source"
    make_bundle(source, spec, bodies, chain=chain)
    bodies[:] = [{"id": 1, "name": 42}] * 2
    assert replay(config, source, tmp_path / "out")["different_mismatch"] == 1
    props["name"] = {"type": "integer"}
    spec.write_text(json.dumps(document))
    assert replay(config, source, tmp_path / "fixed", "--spec", str(spec))["now_match"] == 1


def test_redacted_run_keeps_sensitive_spec_external(tmp_path, replay_env):
    config, bodies, _ = replay_env
    spec = write_spec(tmp_path / "spec.json")
    document = json.loads(spec.read_bytes())
    document["components"]["schemas"]["Item"]["example"] = {"password": "example-secret"}
    spec.write_text(json.dumps(document))
    source = tmp_path / "legacy"
    make_bundle(source, spec, bodies, legacy=True)
    settings = json.loads(config.read_bytes())
    settings["secrets"] = {"redact_fields": ["$..password"]}
    config.write_text(json.dumps(settings))
    out = tmp_path / "redacted"
    assert replay(config, source, out, "--spec", str(spec))["still_mismatch"] == 1
    bundle = discover_bundles(out)[0]
    assert not (bundle / "openapi.json").exists()
    assert all(b"example-secret" not in path.read_bytes() for path in bundle.iterdir())
    bodies[:] = [{"id": 1}] * 2
    assert replay(config, out, tmp_path / "fixed", "--spec", str(spec))["now_match"] == 1


@pytest.mark.parametrize("validate", [False, True])
def test_identical_snapshots_parse_once_per_run(tmp_path, replay_env, monkeypatch, validate):
    config, bodies, _ = replay_env
    spec = write_spec(tmp_path / "spec.json")
    source = tmp_path / "source"
    for index in range(4):
        make_bundle(source, spec, bodies, case_id=str(index))
    original = SchemaValidator.__init__
    calls = []
    def count(self, path, **kwargs):
        calls.append(path)
        original(self, path, **kwargs)
    monkeypatch.setattr(SchemaValidator, "__init__", count)
    flags = ["--validate"] if validate else []
    assert dispatch(parse_args(["replay", "--config", str(config), "--in", str(source),
                                "--out", str(tmp_path / "out"), "--target-a", "a",
                                "--target-b", "b", *flags])) == 0
    assert len(calls) == 1
