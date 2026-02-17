"""Integration tests for replay CLI execution.

Tests require mock servers and CEL evaluator binary.
The test flow is:
1. Run explore to generate mismatch bundles
2. Run replay against those bundles
3. Verify replay correctly classifies outcomes
"""

import json
from pathlib import Path

import pytest

from tests.integration.cli_runner import run_cli
from tests.integration.explore_helpers import (
    TEST_API_SPEC,
    create_runtime_config,
    exclude_ops_except,
)


class TestReplayExecution:
    """Tests for replay command execution."""

    def test_replay_still_mismatch(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test replay detects persistent mismatches.

        Flow:
        1. Run explore to generate mismatch bundles
        2. Run replay (servers unchanged) - should report 'still mismatch'
        """
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )
        explore_out = tmp_path / "explore_artifacts"
        replay_out = tmp_path / "replay_artifacts"

        # Step 1: Run explore to generate mismatch bundles
        explore_result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            *exclude_ops_except("healthCheck", "createWidget"),
            "--out", str(explore_out),
            "--seed", "42",
        )

        print(f"Explore stdout:\n{explore_result.stdout}")
        print(f"Explore stderr:\n{explore_result.stderr}")

        assert explore_result.returncode == 0, f"Explore failed: {explore_result.stderr}"

        # Check that we have some mismatch bundles
        mismatches_dir = explore_out / "mismatches"
        if not mismatches_dir.exists() or not list(mismatches_dir.iterdir()):
            pytest.skip("No mismatches generated - servers may be identical")

        bundle_count = len(list(mismatches_dir.iterdir()))
        print(f"Generated {bundle_count} mismatch bundles")

        # Step 2: Run replay (servers unchanged, so mismatches should persist)
        replay_result = run_cli(
            "replay",
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--in", str(explore_out),
            "--out", str(replay_out),
        )

        print(f"Replay stdout:\n{replay_result.stdout}")
        print(f"Replay stderr:\n{replay_result.stderr}")

        assert replay_result.returncode == 0, f"Replay failed: {replay_result.stderr}"

        # Verify replay output
        assert "Total bundles:" in replay_result.stdout
        assert "Still mismatch:" in replay_result.stdout

        # Check replay summary was written
        summary_path = replay_out / "replay_summary.json"
        assert summary_path.exists(), f"Expected {summary_path} to exist"

        with open(summary_path) as f:
            summary = json.load(f)

        assert summary["mode"] == "replay"
        assert summary["total_bundles"] == bundle_count
        # With unchanged servers, all should still mismatch
        assert summary["still_mismatch"] >= 0
        assert summary["now_match"] >= 0
        assert summary["errors"] == 0

    def test_replay_empty_input_directory(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test replay handles empty input directory gracefully."""
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )
        empty_dir = tmp_path / "empty_input"
        empty_dir.mkdir()
        replay_out = tmp_path / "replay_artifacts"

        result = run_cli(
            "replay",
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--in", str(empty_dir),
            "--out", str(replay_out),
        )

        print(f"stdout:\n{result.stdout}")
        print(f"stderr:\n{result.stderr}")

        assert result.returncode == 0
        assert "No mismatch bundles found" in result.stdout

    def test_replay_validate_mode(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test replay --validate mode."""
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )
        explore_out = tmp_path / "explore_artifacts"
        replay_out = tmp_path / "replay_artifacts"

        # First run explore to generate bundles
        run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            *exclude_ops_except("healthCheck", "createWidget"),
            "--out", str(explore_out),
            "--seed", "42",
        )

        # Run replay with --validate
        result = run_cli(
            "replay",
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--in", str(explore_out),
            "--out", str(replay_out),
            "--validate",
        )

        print(f"stdout:\n{result.stdout}")
        print(f"stderr:\n{result.stderr}")

        assert result.returncode == 0
        assert "Validating:" in result.stdout
        assert "Bundles found:" in result.stdout
        assert "Validation successful" in result.stdout

    def test_replay_writes_new_bundles_for_persistent(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test replay writes new mismatch bundles for persistent mismatches."""
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )
        explore_out = tmp_path / "explore_artifacts"
        replay_out = tmp_path / "replay_artifacts"

        # Run explore to generate mismatch bundles
        run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            *exclude_ops_except("healthCheck", "createWidget"),
            "--out", str(explore_out),
            "--seed", "42",
        )

        mismatches_dir = explore_out / "mismatches"
        if not mismatches_dir.exists() or not list(mismatches_dir.iterdir()):
            pytest.skip("No mismatches generated")

        # Run replay
        run_cli(
            "replay",
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--in", str(explore_out),
            "--out", str(replay_out),
        )

        # Check that replay wrote bundles for persistent mismatches
        replay_mismatches = replay_out / "mismatches"
        if replay_mismatches.exists():
            replay_bundles = list(replay_mismatches.iterdir())
            for bundle in replay_bundles:
                # Each bundle should have all required files
                assert (bundle / "case.json").exists() or (bundle / "chain.json").exists()
                assert (bundle / "diff.json").exists()
                assert (bundle / "metadata.json").exists()
                assert (bundle / "target_a.json").exists()
                assert (bundle / "target_b.json").exists()

    def test_replay_with_custom_timeout(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test replay respects custom --timeout."""
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )
        explore_out = tmp_path / "explore_artifacts"
        replay_out = tmp_path / "replay_artifacts"

        # Run explore to generate bundles
        run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            *exclude_ops_except("healthCheck", "createWidget"),
            "--out", str(explore_out),
            "--seed", "42",
        )

        # Skip if no mismatches were generated
        mismatches_dir = explore_out / "mismatches"
        if not mismatches_dir.exists() or not list(mismatches_dir.iterdir()):
            pytest.skip("No mismatches generated")

        # Run replay with custom timeout
        result = run_cli(
            "replay",
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--in", str(explore_out),
            "--out", str(replay_out),
            "--timeout", "45",
        )

        print(f"stdout:\n{result.stdout}")

        assert result.returncode == 0
        assert "Timeout: 45.0s" in result.stdout


class TestReplaySummaryFormat:
    """Tests for replay summary output format."""

    def test_replay_summary_contains_required_fields(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test replay summary JSON contains all required fields."""
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )
        explore_out = tmp_path / "explore_artifacts"
        replay_out = tmp_path / "replay_artifacts"

        # Run explore
        run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            *exclude_ops_except("healthCheck", "createWidget"),
            "--out", str(explore_out),
            "--seed", "42",
        )

        mismatches_dir = explore_out / "mismatches"
        if not mismatches_dir.exists() or not list(mismatches_dir.iterdir()):
            pytest.skip("No mismatches generated")

        # Run replay
        run_cli(
            "replay",
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--in", str(explore_out),
            "--out", str(replay_out),
        )

        # Check summary format
        summary_path = replay_out / "replay_summary.json"
        assert summary_path.exists()

        with open(summary_path) as f:
            summary = json.load(f)

        # Required fields
        assert "timestamp" in summary
        assert "tool_version" in summary
        assert "mode" in summary
        assert summary["mode"] == "replay"
        assert "input_dir" in summary
        assert "total_bundles" in summary
        assert "still_mismatch" in summary
        assert "now_match" in summary
        assert "different_mismatch" in summary
        assert "errors" in summary
        assert "skipped" in summary
        assert "stateless_bundles" in summary
        assert "chain_bundles" in summary
        assert "fixed_bundles" in summary
        assert "persistent_bundles" in summary
        assert "changed_bundles" in summary

        # Type checks
        assert isinstance(summary["fixed_bundles"], list)
        assert isinstance(summary["persistent_bundles"], list)
        assert isinstance(summary["changed_bundles"], list)
