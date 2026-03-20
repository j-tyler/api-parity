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
