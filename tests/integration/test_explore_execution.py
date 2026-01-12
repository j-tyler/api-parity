"""Tests for explore CLI execution (requires mock servers and CEL evaluator)."""

import json
import subprocess
import sys

from tests.integration.explore_helpers import (
    PROJECT_ROOT,
    TEST_API_SPEC,
    create_runtime_config,
)


class TestExploreExecution:
    """Tests for actual explore execution (requires mock servers and CEL evaluator)."""

    def test_explore_basic_execution(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test basic explore execution with a few cases."""
        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "5",
                "--seed", "12345",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        print(f"stderr:\n{result.stderr}")

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "Total cases:" in result.stdout

        # Check that summary.json was written
        summary_path = out_dir / "summary.json"
        assert summary_path.exists(), f"Expected {summary_path} to exist"

        with open(summary_path) as f:
            summary = json.load(f)

        assert "total_cases" in summary
        assert summary["total_cases"] > 0

    def test_explore_writes_mismatch_bundles(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that mismatches are written to bundles.

        With variant A and B servers, we expect some mismatches (shuffled arrays,
        price differences beyond tolerance for some fields).
        """
        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "20",
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=120,
        )

        print(f"stdout:\n{result.stdout}")
        print(f"stderr:\n{result.stderr}")

        assert result.returncode == 0

        # Check for mismatch bundles
        mismatches_dir = out_dir / "mismatches"
        if mismatches_dir.exists():
            bundles = list(mismatches_dir.iterdir())
            if bundles:
                # Verify bundle structure
                bundle = bundles[0]
                assert (bundle / "case.json").exists()
                assert (bundle / "target_a.json").exists()
                assert (bundle / "target_b.json").exists()
                assert (bundle / "diff.json").exists()
                assert (bundle / "metadata.json").exists()

                # Verify diff structure
                with open(bundle / "diff.json") as f:
                    diff = json.load(f)
                assert "match" in diff
                assert diff["match"] is False
                assert "mismatch_type" in diff

    def test_explore_with_timeout(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that timeout options are respected."""
        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "3",
                "--timeout", "10",
                "--operation-timeout", "healthCheck:5",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        assert result.returncode == 0
        assert "Timeout: 10.0s" in result.stdout

    def test_explore_with_exclude(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that --exclude prevents operations from being tested."""
        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )
        out_dir = tmp_path / "artifacts"

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "10",
                "--exclude", "healthCheck",
                "--exclude", "deleteWidget",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        assert result.returncode == 0
        # healthCheck should not appear in output
        assert "healthCheck:" not in result.stdout

    def test_explore_seed_reproducibility(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that the same seed produces the same cases."""
        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )

        def run_explore(out_name: str) -> dict:
            out_dir = tmp_path / out_name
            subprocess.run(
                [
                    sys.executable, "-m", "api_parity.cli",
                    "explore",
                    "--spec", str(TEST_API_SPEC),
                    "--config", str(config_path),
                    "--target-a", "server_a",
                    "--target-b", "server_b",
                    "--out", str(out_dir),
                    "--max-cases", "5",
                    "--seed", "99999",
                ],
                capture_output=True,
                cwd=PROJECT_ROOT,
                timeout=60,
            )
            with open(out_dir / "summary.json") as f:
                return json.load(f)

        summary1 = run_explore("run1")
        summary2 = run_explore("run2")

        # Same seed should produce same number of cases per operation
        assert summary1["operations"] == summary2["operations"]


class TestCELEvaluatorIntegration:
    """Tests verifying CEL evaluator works correctly in the full pipeline."""

    def test_cel_expressions_evaluated(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that CEL expressions from comparison rules are evaluated."""
        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )
        out_dir = tmp_path / "artifacts"

        # Run with healthCheck which uses both_positive for uptime_seconds
        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--max-cases", "10",
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        # Should complete without CEL errors
        assert result.returncode == 0
        assert "CEL evaluator crashed" not in result.stderr
