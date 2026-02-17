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

    def test_explore_comprehensive_execution(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test comprehensive explore execution including options and output.

        Combined test verifying:
        - Basic execution works and writes summary
        - Mismatch bundles are written correctly
        - Timeout and exclude options are respected
        - CEL expressions are evaluated without errors
        """
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )
        out_dir = tmp_path / "artifacts"

        # Run explore with various options
        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(out_dir),
                "--seed", "42",
                "--timeout", "10",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        print(f"stderr:\n{result.stderr}")

        # Basic execution checks
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "Total cases:" in result.stdout
        assert "Timeout: 10.0s" in result.stdout
        assert "CEL evaluator crashed" not in result.stderr

        # Summary should be written
        summary_path = out_dir / "summary.json"
        assert summary_path.exists(), f"Expected {summary_path} to exist"

        with open(summary_path) as f:
            summary = json.load(f)

        assert "total_cases" in summary
        assert summary["total_cases"] > 0

        # Check mismatch bundles if any exist
        mismatches_dir = out_dir / "mismatches"
        if mismatches_dir.exists():
            bundles = list(mismatches_dir.iterdir())
            if bundles:
                bundle = bundles[0]
                assert (bundle / "case.json").exists()
                assert (bundle / "target_a.json").exists()
                assert (bundle / "target_b.json").exists()
                assert (bundle / "diff.json").exists()
                assert (bundle / "metadata.json").exists()

                with open(bundle / "diff.json") as f:
                    diff = json.load(f)
                assert "match" in diff
                assert diff["match"] is False
                assert "mismatch_type" in diff

    def test_explore_with_exclude(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test that --exclude prevents operations from being tested."""
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
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

    def test_explore_seed_reproducibility(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test that the same seed produces the same cases."""
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
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
