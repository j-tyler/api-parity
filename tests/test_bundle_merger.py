"""Tests for bundle merger - merge logic with tmp_path fixtures.

Tests use minimal on-disk bundle structures (case.json, diff.json, metadata.json)
to verify deduplication, error handling, and output structure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_parity.bundle_merger import BundleMergeError, MergeSummary, merge_bundles


def _write_stateless_bundle(
    base_dir: Path, name: str, operation_id: str, diff_data: dict
) -> Path:
    """Write a minimal stateless mismatch bundle to disk.

    Args:
        base_dir: Run output directory (will contain mismatches/ subdirectory).
        name: Bundle directory name (typically timestamp-prefixed).
        operation_id: The operation ID for case.json.
        diff_data: Contents of diff.json.

    Returns:
        Path to the created bundle directory.
    """
    bundle_dir = base_dir / "mismatches" / name
    bundle_dir.mkdir(parents=True, exist_ok=True)

    case = {
        "operation_id": operation_id,
        "method": "GET",
        "path": "/test",
        "path_parameters": {},
    }
    with open(bundle_dir / "case.json", "w") as f:
        json.dump(case, f)

    with open(bundle_dir / "diff.json", "w") as f:
        json.dump(diff_data, f)

    metadata = {"tool_version": "0.1.0", "target_a": "a", "target_b": "b"}
    with open(bundle_dir / "metadata.json", "w") as f:
        json.dump(metadata, f)

    return bundle_dir


def _write_chain_bundle(
    base_dir: Path, name: str, operation_id: str, diff_data: dict
) -> Path:
    """Write a minimal chain mismatch bundle to disk.

    Args:
        base_dir: Run output directory (will contain mismatches/ subdirectory).
        name: Bundle directory name.
        operation_id: Operation ID for the first step.
        diff_data: Contents of diff.json (should have type: "chain").

    Returns:
        Path to the created bundle directory.
    """
    bundle_dir = base_dir / "mismatches" / name
    bundle_dir.mkdir(parents=True, exist_ok=True)

    chain = {
        "steps": [
            {
                "request_template": {
                    "operation_id": operation_id,
                    "method": "POST",
                    "path": "/widgets",
                    "path_parameters": {},
                },
                "link_source": None,
            }
        ]
    }
    with open(bundle_dir / "chain.json", "w") as f:
        json.dump(chain, f)

    with open(bundle_dir / "diff.json", "w") as f:
        json.dump(diff_data, f)

    metadata = {"tool_version": "0.1.0", "target_a": "a", "target_b": "b"}
    with open(bundle_dir / "metadata.json", "w") as f:
        json.dump(metadata, f)

    return bundle_dir


def _body_diff(paths: list[str]) -> dict:
    """Create a stateless body diff with the given failing paths."""
    return {
        "type": "stateless",
        "mismatch_type": "body",
        "details": {
            "body": {
                "differences": [{"path": p, "a_value": "x", "b_value": "y"} for p in paths]
            }
        },
    }


def _chain_body_diff(mismatch_step: int, paths: list[str]) -> dict:
    """Create a chain diff with body mismatch at the given step."""
    return {
        "type": "chain",
        "mismatch_step": mismatch_step,
        "steps": [
            {
                "mismatch_type": "body",
                "details": {
                    "body": {
                        "differences": [
                            {"path": p, "a_value": "x", "b_value": "y"} for p in paths
                        ]
                    }
                },
            }
        ],
    }


class TestMergeDuplicatePatterns:
    """Two bundles with the same failure pattern from different runs produce one output."""

    def test_identical_pattern_keeps_latest(self, tmp_path: Path):
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        out = tmp_path / "merged"

        # Same operation, same body paths — same failure pattern
        _write_stateless_bundle(run1, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"]))
        _write_stateless_bundle(run2, "20260102T000000__op1__case2", "op1", _body_diff(["$.id"]))

        summary = merge_bundles([run1, run2], out)

        assert summary.total_bundles_scanned == 2
        assert summary.unique_patterns == 1
        assert summary.bundles_kept == 1
        assert summary.bundles_deduplicated == 1

        # The newer bundle should be the one kept
        kept_bundles = list((out / "mismatches").iterdir())
        assert len(kept_bundles) == 1
        assert kept_bundles[0].name == "20260102T000000__op1__case2"


class TestMergeDifferentPatterns:
    """Two bundles with different failure patterns are both kept."""

    def test_different_paths_both_kept(self, tmp_path: Path):
        run1 = tmp_path / "run1"
        out = tmp_path / "merged"

        _write_stateless_bundle(run1, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"]))
        _write_stateless_bundle(run1, "20260101T000001__op1__case2", "op1", _body_diff(["$.name"]))

        summary = merge_bundles([run1], out)

        assert summary.total_bundles_scanned == 2
        assert summary.unique_patterns == 2
        assert summary.bundles_kept == 2
        assert summary.bundles_deduplicated == 0


class TestMergeBinaryAndExtraFieldBundles:
    """Binary body and extra-field body mismatches are not collapsed together."""

    def test_binary_and_json_body_both_kept(self, tmp_path: Path):
        """Binary body mismatch and JSON body mismatch are distinct patterns."""
        run1 = tmp_path / "run1"
        out = tmp_path / "merged"

        json_diff = _body_diff(["$.id"])
        binary_diff = {
            "type": "stateless",
            "mismatch_type": "body",
            "details": {
                "binary_body": {
                    "differences": [{"path": "binary_body", "a_value": "abc", "b_value": "def"}]
                }
            },
        }

        _write_stateless_bundle(run1, "20260101T000000__op1__json", "op1", json_diff)
        _write_stateless_bundle(run1, "20260101T000001__op1__binary", "op1", binary_diff)

        summary = merge_bundles([run1], out)

        assert summary.unique_patterns == 2
        assert summary.bundles_kept == 2
        assert summary.bundles_deduplicated == 0

    def test_extra_fields_and_json_body_both_kept(self, tmp_path: Path):
        """Extra-fields body mismatch and JSON body mismatch are distinct patterns."""
        run1 = tmp_path / "run1"
        out = tmp_path / "merged"

        json_diff = _body_diff(["$.id"])
        extra_diff = {
            "type": "stateless",
            "mismatch_type": "body",
            "details": {
                "extra_fields": {
                    "differences": [{"path": "$.custom", "a_value": 1, "b_value": 2}]
                }
            },
        }

        _write_stateless_bundle(run1, "20260101T000000__op1__json", "op1", json_diff)
        _write_stateless_bundle(run1, "20260101T000001__op1__extra", "op1", extra_diff)

        summary = merge_bundles([run1], out)

        assert summary.unique_patterns == 2
        assert summary.bundles_kept == 2
        assert summary.bundles_deduplicated == 0


class TestMergeSchemaViolationBundles:
    """Different schema violations on the same operation are not collapsed."""

    def test_different_schema_violations_both_kept(self, tmp_path: Path):
        """Two schema violations with different paths on same op are distinct.

        Regression: previously all schema violations for one operation collapsed
        to (operation_id, "schema_violation"), so merging runs where one found
        missing $.id and another found unexpected $.debug would silently discard
        one bundle.
        """
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        out = tmp_path / "merged"

        diff_missing_id = {
            "type": "stateless",
            "mismatch_type": "schema_violation",
            "details": {
                "schema": {
                    "differences": [
                        {"path": "$.id", "target_a": "<violation: missing>", "target_b": "<not checked>"}
                    ]
                }
            },
        }
        diff_unexpected_debug = {
            "type": "stateless",
            "mismatch_type": "schema_violation",
            "details": {
                "schema": {
                    "differences": [
                        {"path": "$.debug", "target_a": "<not checked>", "target_b": "<violation: additional>"}
                    ]
                }
            },
        }

        _write_stateless_bundle(run1, "20260101T000000__op1__missing_id", "op1", diff_missing_id)
        _write_stateless_bundle(run2, "20260102T000000__op1__extra_debug", "op1", diff_unexpected_debug)

        summary = merge_bundles([run1, run2], out)

        assert summary.unique_patterns == 2
        assert summary.bundles_kept == 2
        assert summary.bundles_deduplicated == 0

    def test_same_schema_violation_deduplicated(self, tmp_path: Path):
        """Same schema violation path on same op across runs is deduplicated."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        out = tmp_path / "merged"

        diff = {
            "type": "stateless",
            "mismatch_type": "schema_violation",
            "details": {
                "schema": {
                    "differences": [
                        {"path": "$.id", "target_a": "<violation: missing>", "target_b": "<not checked>"}
                    ]
                }
            },
        }

        _write_stateless_bundle(run1, "20260101T000000__op1__v1", "op1", diff)
        _write_stateless_bundle(run2, "20260102T000000__op1__v2", "op1", diff)

        summary = merge_bundles([run1, run2], out)

        assert summary.unique_patterns == 1
        assert summary.bundles_kept == 1
        assert summary.bundles_deduplicated == 1


class TestMergeCorruptedBundles:
    """Corrupted bundles are skipped with errors, valid bundles still processed."""

    def test_invalid_json_skipped(self, tmp_path: Path):
        run1 = tmp_path / "run1"
        out = tmp_path / "merged"

        # Valid bundle
        _write_stateless_bundle(run1, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"]))

        # Corrupted bundle — invalid JSON in diff.json
        bad_bundle = run1 / "mismatches" / "20260101T000001__op1__case2"
        bad_bundle.mkdir(parents=True, exist_ok=True)
        (bad_bundle / "diff.json").write_text("{invalid json")
        (bad_bundle / "case.json").write_text('{"operation_id": "op1"}')
        (bad_bundle / "metadata.json").write_text("{}")

        summary = merge_bundles([run1], out)

        assert summary.bundles_kept == 1
        assert len(summary.errors) == 1
        assert "20260101T000001__op1__case2" in summary.errors[0]


class TestMergeEmptyInput:
    """Empty input directories produce empty output."""

    def test_empty_dirs(self, tmp_path: Path):
        run1 = tmp_path / "run1"
        run1.mkdir()
        out = tmp_path / "merged"

        summary = merge_bundles([run1], out)

        assert summary.total_bundles_scanned == 0
        assert summary.unique_patterns == 0
        assert summary.bundles_kept == 0
        assert summary.bundles_deduplicated == 0


def _write_multistep_chain_bundle(
    base_dir: Path,
    name: str,
    step_operation_ids: list[str],
    diff_data: dict,
) -> Path:
    """Write a chain bundle with multiple steps.

    Args:
        base_dir: Run output directory.
        name: Bundle directory name.
        step_operation_ids: Operation IDs for each step in order.
        diff_data: Contents of diff.json.

    Returns:
        Path to the created bundle directory.
    """
    bundle_dir = base_dir / "mismatches" / name
    bundle_dir.mkdir(parents=True, exist_ok=True)

    chain = {
        "steps": [
            {
                "request_template": {
                    "operation_id": op_id,
                    "method": "POST",
                    "path": f"/{op_id}",
                    "path_parameters": {},
                },
                "link_source": None if i == 0 else "prev",
            }
            for i, op_id in enumerate(step_operation_ids)
        ]
    }
    with open(bundle_dir / "chain.json", "w") as f:
        json.dump(chain, f)

    with open(bundle_dir / "diff.json", "w") as f:
        json.dump(diff_data, f)

    metadata = {"tool_version": "0.1.0", "target_a": "a", "target_b": "b"}
    with open(bundle_dir / "metadata.json", "w") as f:
        json.dump(metadata, f)

    return bundle_dir


class TestMergeChainBundles:
    """Chain bundles are deduplicated using mismatch_step in the key."""

    def test_same_chain_pattern_deduped(self, tmp_path: Path):
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        out = tmp_path / "merged"

        diff = _chain_body_diff(mismatch_step=0, paths=["$.id"])
        _write_chain_bundle(run1, "20260101T000000__op1__chain1", "op1", diff)
        _write_chain_bundle(run2, "20260102T000000__op1__chain2", "op1", diff)

        summary = merge_bundles([run1, run2], out)

        assert summary.unique_patterns == 1
        assert summary.bundles_kept == 1
        assert summary.bundles_deduplicated == 1

    def test_diverging_chains_both_kept(self, tmp_path: Path):
        """Chains sharing a prefix but diverging later are distinct mismatches.

        createOrder->getInvoice failing at step 1 on $.id is a different
        regression than createOrder->getShipment failing at step 1 on $.id.
        Both must survive deduplication.
        """
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        out = tmp_path / "merged"

        # Same diff structure: body mismatch at step 1 on $.id
        diff = {
            "type": "chain",
            "mismatch_step": 1,
            "steps": [
                {"mismatch_type": None, "match": True, "details": {}},
                {
                    "mismatch_type": "body",
                    "details": {
                        "body": {
                            "differences": [{"path": "$.id", "a_value": "x", "b_value": "y"}]
                        }
                    },
                },
            ],
        }

        # Chain A: createOrder -> getInvoice (fails at step 1)
        _write_multistep_chain_bundle(
            run1, "20260101T000000__chain_a", ["createOrder", "getInvoice"], diff
        )
        # Chain B: createOrder -> getShipment (fails at step 1)
        _write_multistep_chain_bundle(
            run2, "20260102T000000__chain_b", ["createOrder", "getShipment"], diff
        )

        summary = merge_bundles([run1, run2], out)

        assert summary.unique_patterns == 2
        assert summary.bundles_kept == 2
        assert summary.bundles_deduplicated == 0


class TestMergeOutputStructure:
    """Output has correct bundle structure for replay."""

    def test_output_has_mismatches_subdir(self, tmp_path: Path):
        run1 = tmp_path / "run1"
        out = tmp_path / "merged"

        _write_stateless_bundle(run1, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"]))

        merge_bundles([run1], out)

        # Output should have mismatches/ subdirectory
        assert (out / "mismatches").is_dir()

        # Each bundle should have the required files
        bundles = list((out / "mismatches").iterdir())
        assert len(bundles) == 1
        bundle = bundles[0]
        assert (bundle / "case.json").is_file()
        assert (bundle / "diff.json").is_file()
        assert (bundle / "metadata.json").is_file()


class TestMergeIdempotent:
    """Re-running merge into the same --out directory succeeds."""

    def test_remerge_same_output_dir(self, tmp_path: Path):
        """Merging again into an existing output directory does not crash.

        Regression: shutil.copytree() raised FileExistsError when the
        destination bundle directory already existed from a previous merge.
        Common in iterative workflows where users merge again after adding
        another explore run.
        """
        run1 = tmp_path / "run1"
        out = tmp_path / "merged"

        _write_stateless_bundle(run1, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"]))

        # First merge
        summary1 = merge_bundles([run1], out)
        assert summary1.bundles_kept == 1

        # Second merge into same output — should not raise FileExistsError
        summary2 = merge_bundles([run1], out)
        assert summary2.bundles_kept == 1

    def test_remerge_with_new_run_adds_bundles(self, tmp_path: Path):
        """Re-merging with additional input adds new bundles to output."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        out = tmp_path / "merged"

        _write_stateless_bundle(run1, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"]))

        # First merge with run1 only
        merge_bundles([run1], out)

        # Add a second run with a different pattern
        _write_stateless_bundle(run2, "20260102T000000__op2__case1", "op2", _body_diff(["$.name"]))

        # Re-merge with both runs
        summary = merge_bundles([run1, run2], out)
        assert summary.bundles_kept == 2


class TestMergeSummaryFile:
    """merge_summary.json is written with expected fields."""

    def test_summary_written(self, tmp_path: Path):
        run1 = tmp_path / "run1"
        out = tmp_path / "merged"

        _write_stateless_bundle(run1, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"]))

        merge_bundles([run1], out)

        summary_path = out / "merge_summary.json"
        assert summary_path.is_file()

        with open(summary_path) as f:
            data = json.load(f)

        assert data["total_bundles_scanned"] == 1
        assert data["unique_patterns"] == 1
        assert data["bundles_kept"] == 1
        assert data["bundles_deduplicated"] == 0
        assert data["errors"] == []
        assert str(run1) in data["input_dir_counts"]


class TestMergeRejectsReplayOutput:
    """Merge only accepts explore output, not replay output."""

    def test_replay_output_rejected(self, tmp_path: Path):
        """Directory with replay_summary.json is rejected."""
        replay_dir = tmp_path / "replay_results"
        _write_stateless_bundle(
            replay_dir, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"])
        )
        # Mark as replay output
        replay_summary = {"mode": "replay", "total_bundles": 1}
        with open(replay_dir / "replay_summary.json", "w") as f:
            json.dump(replay_summary, f)

        out = tmp_path / "merged"

        with pytest.raises(BundleMergeError, match="replay output"):
            merge_bundles([replay_dir], out)

    def test_explore_output_accepted(self, tmp_path: Path):
        """Directory with summary.json (explore) is accepted."""
        explore_dir = tmp_path / "explore_results"
        _write_stateless_bundle(
            explore_dir, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"])
        )
        # Explore output has summary.json, not replay_summary.json
        with open(explore_dir / "summary.json", "w") as f:
            json.dump({"total_cases": 10}, f)

        out = tmp_path / "merged"
        summary = merge_bundles([explore_dir], out)

        assert summary.bundles_kept == 1

    def test_mixed_explore_and_replay_rejected(self, tmp_path: Path):
        """If any input is replay output, the entire merge is rejected."""
        explore_dir = tmp_path / "explore"
        replay_dir = tmp_path / "replay"

        _write_stateless_bundle(
            explore_dir, "20260101T000000__op1__case1", "op1", _body_diff(["$.id"])
        )
        _write_stateless_bundle(
            replay_dir, "20260102T000000__op1__case2", "op1", _body_diff(["$.id"])
        )
        with open(replay_dir / "replay_summary.json", "w") as f:
            json.dump({"mode": "replay"}, f)

        out = tmp_path / "merged"

        with pytest.raises(BundleMergeError, match="replay output"):
            merge_bundles([explore_dir, replay_dir], out)
