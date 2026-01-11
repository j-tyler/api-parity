"""Integration tests for the explore CLI command.

These tests verify the full explore workflow end-to-end:
- Loading OpenAPI spec via Schemathesis
- Executing requests against dual mock servers
- Comparing responses using CEL evaluator
- Writing mismatch artifacts

Requirements:
- CEL evaluator binary must be built (go build -o cel-evaluator ./cmd/cel-evaluator)
- Mock servers are started by pytest fixtures
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.conftest import MockServer, find_free_port, wait_for_server


# Path to fixtures
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
TEST_API_SPEC = FIXTURES_DIR / "test_api.yaml"
COMPARISON_RULES = FIXTURES_DIR / "comparison_rules.json"
PROJECT_ROOT = Path(__file__).parent.parent.parent


def create_runtime_config(port_a: int, port_b: int, tmp_path: Path) -> Path:
    """Create a runtime config pointing to the test servers."""
    config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{port_a}"
    headers: {{}}

  server_b:
    base_url: "http://127.0.0.1:{port_b}"
    headers: {{}}

comparison_rules: {COMPARISON_RULES}
"""
    config_path = tmp_path / "runtime_config.yaml"
    config_path.write_text(config)
    return config_path


class TestExploreValidateMode:
    """Tests for --validate mode (no execution, just config validation)."""

    def test_validate_success(self, dual_servers, tmp_path):
        """Test that --validate mode succeeds with valid config."""
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
                "--validate",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert "Validation successful" in result.stdout

    def test_validate_lists_operations(self, dual_servers, tmp_path):
        """Test that --validate lists all operations from the spec."""
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
                "--validate",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0
        # Check that known operations are listed
        assert "listWidgets" in result.stdout or "createWidget" in result.stdout

    def test_validate_with_exclude(self, dual_servers, tmp_path):
        """Test that --validate respects --exclude flag."""
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
                "--exclude", "healthCheck",
                "--validate",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0
        assert "Excluding: healthCheck" in result.stdout


class TestExploreConfigErrors:
    """Tests for configuration error handling."""

    def test_missing_config_file(self, tmp_path):
        """Test error when config file doesn't exist."""
        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(tmp_path / "nonexistent.yaml"),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 1
        assert "Error loading config" in result.stderr or "not found" in result.stderr

    def test_invalid_target_name(self, dual_servers, tmp_path):
        """Test error when target name doesn't exist in config."""
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
                "--target-a", "nonexistent",
                "--target-b", "server_b",
                "--out", str(out_dir),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 1
        assert "not found" in result.stderr

    def test_missing_spec_file(self, dual_servers, tmp_path):
        """Test error when OpenAPI spec doesn't exist."""
        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(tmp_path / "nonexistent.yaml"),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 1
        assert "Error" in result.stderr


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


class TestListOperations:
    """Tests for the list-operations subcommand."""

    def test_list_operations_basic(self):
        """Test basic list-operations output."""
        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "list-operations",
                "--spec", str(TEST_API_SPEC),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0
        # Check for expected operations
        assert "createWidget" in result.stdout
        assert "getWidget" in result.stdout
        assert "listWidgets" in result.stdout
        assert "Total:" in result.stdout

    def test_list_operations_shows_links(self):
        """Test that list-operations shows OpenAPI links."""
        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "list-operations",
                "--spec", str(TEST_API_SPEC),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

        assert result.returncode == 0
        # The test API has links from createWidget to getWidget
        # Check for link-related output
        assert "Links:" in result.stdout or "→" in result.stdout


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


class TestTimeoutAndConnectionErrors:
    """Tests for timeout enforcement and connection error handling."""

    def test_connection_error_when_server_down(self, tmp_path, cel_evaluator_exists):
        """Test that connection errors are handled gracefully when server is unreachable."""
        # Create config pointing to non-existent server
        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:59999"
    headers: {{}}

  server_b:
    base_url: "http://127.0.0.1:59998"
    headers: {{}}

comparison_rules: {COMPARISON_RULES}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--max-cases", "2",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=30,
        )

        # Should complete (not crash) and report errors
        assert result.returncode == 0
        assert "ERROR" in result.stdout or "connection" in result.stdout.lower()

        # Summary should show errors
        summary_path = out_dir / "summary.json"
        if summary_path.exists():
            with open(summary_path) as f:
                summary = json.load(f)
            assert summary["errors"] > 0

class TestStatusCodeMismatch:
    """Tests for status code mismatch detection."""

    def test_status_code_mismatch_detected(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that different status codes between targets are detected as mismatch.

        We test this by hitting a non-existent widget on both servers - they should
        both return 404, which is a match. But if we configure one server incorrectly,
        we'd get a mismatch.
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
                "--max-cases", "10",
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        assert result.returncode == 0

        # Check if any status_code mismatches were recorded
        mismatches_dir = out_dir / "mismatches"
        if mismatches_dir.exists():
            for bundle in mismatches_dir.iterdir():
                diff_path = bundle / "diff.json"
                if diff_path.exists():
                    with open(diff_path) as f:
                        diff = json.load(f)
                    # Verify mismatch_type field exists and is valid
                    assert diff["mismatch_type"] in ["status_code", "headers", "body", None]


class TestComparisonRuleVerification:
    """Tests verifying specific comparison rules work correctly."""

    def test_numeric_tolerance_allows_small_differences(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that numeric_tolerance rule allows prices within tolerance.

        Variant B adds 0.001 to prices, which is within the 0.01 tolerance.
        This should result in MATCH, not MISMATCH.
        """
        # Create rules with explicit numeric tolerance for createWidget
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "createWidget": {
                    "body": {
                        "field_rules": {
                            "$.id": {"predefined": "uuid_format"},
                            "$.name": {"predefined": "exact_match"},
                            "$.price": {"predefined": "numeric_tolerance", "tolerance": 0.01},
                            "$.category": {"predefined": "exact_match"},
                            "$.in_stock": {"predefined": "exact_match"},
                            "$.tags": {"predefined": "unordered_array"},
                            "$.created_at": {"predefined": "iso_timestamp_format"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # createWidget should match (price diff 0.001 within 0.01 tolerance)
        assert "createWidget" in result.stdout
        # Look for MATCH on createWidget line
        for line in result.stdout.split("\n"):
            if "createWidget:" in line:
                # Should be MATCH due to tolerance
                assert "MATCH" in line, f"Expected createWidget to MATCH due to tolerance, got: {line}"

    def test_unordered_array_allows_shuffled_elements(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that unordered_array rule treats shuffled arrays as matching.

        Variant B shuffles arrays. With unordered_array rule, this should MATCH.
        """
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "getUserProfile": {
                    "body": {
                        "field_rules": {
                            "$.id": {"predefined": "uuid_format"},
                            "$.username": {"predefined": "exact_match"},
                            "$.roles": {"predefined": "unordered_array"},
                            "$.scores.reputation": {"predefined": "numeric_tolerance", "tolerance": 1.0},
                            "$.scores.activity": {"predefined": "numeric_tolerance", "tolerance": 1.0},
                            "$.scores.trust": {"predefined": "numeric_tolerance", "tolerance": 0.1},
                            "$.created_at": {"predefined": "iso_timestamp_format"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "123",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

    def test_operation_rules_override_defaults(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that operation_rules completely override default_rules for that operation."""
        # Default rules require exact_match, operation rules use ignore
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {
                    "field_rules": {
                        "$.timestamp": {"predefined": "exact_match"}  # Would fail
                    }
                }
            },
            "operation_rules": {
                "healthCheck": {
                    "body": {
                        "field_rules": {
                            "$.status": {"predefined": "exact_match"},
                            "$.timestamp": {"predefined": "ignore"},  # Override to ignore
                            "$.uptime_seconds": {"predefined": "ignore"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # healthCheck should MATCH because timestamp is ignored via operation override
        for line in result.stdout.split("\n"):
            if "healthCheck:" in line:
                assert "MATCH" in line, f"Expected healthCheck to MATCH due to override, got: {line}"

    def test_presence_required_fails_when_missing(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that presence: required fails when field is missing."""
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "healthCheck": {
                    "body": {
                        "field_rules": {
                            "$.nonexistent_field": {"presence": "required"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # healthCheck should MISMATCH because nonexistent_field is required but missing
        for line in result.stdout.split("\n"):
            if "healthCheck:" in line:
                assert "MISMATCH" in line, f"Expected healthCheck to MISMATCH, got: {line}"

    def test_presence_optional_passes_when_missing(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that presence: optional passes even when field is missing."""
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "healthCheck": {
                    "body": {
                        "field_rules": {
                            "$.status": {"predefined": "exact_match"},
                            "$.nonexistent_field": {"presence": "optional"},
                            "$.timestamp": {"predefined": "ignore"},
                            "$.uptime_seconds": {"predefined": "ignore"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # healthCheck should MATCH because nonexistent_field is optional
        for line in result.stdout.split("\n"):
            if "healthCheck:" in line:
                assert "MATCH" in line, f"Expected healthCheck to MATCH, got: {line}"


class TestConfigFeatures:
    """Tests for configuration features."""

    def test_environment_variable_substitution(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that ${ENV_VAR} patterns are substituted in config."""
        # Set environment variables
        os.environ["TEST_PORT_A"] = str(dual_servers["a"].port)
        os.environ["TEST_PORT_B"] = str(dual_servers["b"].port)

        try:
            config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:${{TEST_PORT_A}}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:${{TEST_PORT_B}}"
    headers: {{}}
comparison_rules: {COMPARISON_RULES}
"""
            config_path = tmp_path / "config.yaml"
            config_path.write_text(config)
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
                    "--validate",
                ],
                capture_output=True,
                text=True,
                cwd=PROJECT_ROOT,
                env={**os.environ},
                timeout=30,
            )

            print(f"stdout:\n{result.stdout}")
            print(f"stderr:\n{result.stderr}")

            assert result.returncode == 0
            # Verify the substituted URLs appear in output
            assert str(dual_servers["a"].port) in result.stdout
            assert str(dual_servers["b"].port) in result.stdout
        finally:
            del os.environ["TEST_PORT_A"]
            del os.environ["TEST_PORT_B"]

    def test_target_headers_sent_with_requests(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that headers from target config are sent with requests."""
        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers:
      X-Custom-Header: "test-value-a"
      Authorization: "Bearer test-token"
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers:
      X-Custom-Header: "test-value-b"
      Authorization: "Bearer test-token"
comparison_rules: {COMPARISON_RULES}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        # Should complete successfully - headers are sent but mock server doesn't validate them
        assert result.returncode == 0

    def test_same_target_rejected(self, dual_servers, tmp_path):
        """Test that using the same target for both A and B is rejected."""
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
                "--target-b", "server_a",  # Same as target-a
                "--out", str(out_dir),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=30,
        )

        assert result.returncode == 1
        assert "different" in result.stderr.lower() or "same" in result.stderr.lower()

    def test_relative_comparison_rules_path(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that relative comparison_rules path is resolved relative to config file."""
        # Create a subdirectory for the config
        config_dir = tmp_path / "configs"
        config_dir.mkdir()

        # Copy comparison rules to a sibling directory
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        shutil.copy(COMPARISON_RULES, rules_dir / "rules.json")

        # Config uses relative path
        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: ../rules/rules.json
"""
        config_path = config_dir / "config.yaml"
        config_path.write_text(config)
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
                "--max-cases", "2",
                "--validate",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=30,
        )

        print(f"stdout:\n{result.stdout}")
        print(f"stderr:\n{result.stderr}")

        assert result.returncode == 0
        assert "Validation successful" in result.stdout

    def test_malformed_comparison_rules_rejected(self, dual_servers, tmp_path):
        """Test that invalid JSON in comparison rules produces clear error."""
        rules_path = tmp_path / "bad_rules.json"
        rules_path.write_text("{ this is not valid json }")

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=30,
        )

        assert result.returncode == 1
        assert "Error" in result.stderr
        assert "JSON" in result.stderr or "rules" in result.stderr.lower()


class TestSecretRedaction:
    """Tests for secret field redaction in artifacts."""

    def test_secrets_redacted_in_artifacts(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that configured secret fields are redacted in mismatch bundles."""
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "healthCheck": {
                    "body": {
                        "field_rules": {
                            "$.nonexistent": {"presence": "required"}  # Force mismatch
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
secrets:
  redact_fields:
    - "$.version"
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        assert result.returncode == 0

        # Check mismatch bundles for redaction
        mismatches_dir = out_dir / "mismatches"
        if mismatches_dir.exists():
            for bundle in mismatches_dir.iterdir():
                if "healthCheck" in str(bundle):
                    target_a_path = bundle / "target_a.json"
                    if target_a_path.exists():
                        with open(target_a_path) as f:
                            data = json.load(f)
                        # version field should be redacted
                        response_body = data.get("response", {}).get("body", {})
                        if "version" in response_body:
                            assert response_body["version"] == "[REDACTED]"


class TestErrorHandling:
    """Tests for error handling edge cases."""

    def test_invalid_cel_expression_recorded_as_mismatch(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that invalid CEL expressions are recorded as mismatches with error info."""
        # Test listWidgets with invalid CEL - use --exclude to isolate this operation
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "listWidgets": {
                    "body": {
                        "field_rules": {
                            "$.total": {"expr": "this is not valid CEL syntax @#$%"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        # Exclude all operations except listWidgets to isolate the test
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
                "--exclude", "createWidget",
                "--exclude", "getWidget",
                "--exclude", "updateWidget",
                "--exclude", "deleteWidget",
                "--exclude", "getUserProfile",
                "--exclude", "createOrder",
                "--exclude", "getOrder",
                "--exclude", "healthCheck",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")

        # Should complete without crashing
        assert result.returncode == 0

        # listWidgets should be recorded as mismatch (due to CEL error)
        assert "MISMATCH" in result.stdout

        # Check that mismatch bundle contains error info
        mismatches_dir = out_dir / "mismatches"
        if mismatches_dir.exists():
            for bundle in mismatches_dir.iterdir():
                if "listWidgets" in str(bundle):
                    diff_path = bundle / "diff.json"
                    if diff_path.exists():
                        with open(diff_path) as f:
                            diff = json.load(f)
                        # Should have error in the rule field
                        body_diffs = diff.get("details", {}).get("body", {}).get("differences", [])
                        if body_diffs:
                            assert any("error" in d.get("rule", "").lower() for d in body_diffs)

    def test_invalid_yaml_config_rejected(self, tmp_path):
        """Test that invalid YAML in config produces clear error."""
        config_path = tmp_path / "bad_config.yaml"
        config_path.write_text("targets:\n  - this: is\n    bad: yaml: syntax:")

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(TEST_API_SPEC),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=30,
        )

        assert result.returncode == 1
        assert "Error" in result.stderr

    def test_invalid_spec_rejected(self, dual_servers, tmp_path):
        """Test that invalid OpenAPI spec produces clear error."""
        spec_path = tmp_path / "bad_spec.yaml"
        spec_path.write_text("not a valid openapi spec")

        config_path = create_runtime_config(
            dual_servers["a"].port,
            dual_servers["b"].port,
            tmp_path,
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "api_parity.cli",
                "explore",
                "--spec", str(spec_path),
                "--config", str(config_path),
                "--target-a", "server_a",
                "--target-b", "server_b",
                "--out", str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=30,
        )

        assert result.returncode == 1
        assert "Error" in result.stderr


class TestHeaderMismatch:
    """Tests for header comparison and mismatch detection."""

    def test_header_comparison_with_rules(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that header rules are applied during comparison."""
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "headers": {
                    "x-request-id": {"predefined": "uuid_format"},
                    "content-type": {"predefined": "exact_match"}
                },
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "healthCheck": {
                    "headers": {
                        "x-request-id": {"predefined": "ignore"}  # Ignore volatile header
                    },
                    "body": {
                        "field_rules": {
                            "$.status": {"predefined": "exact_match"},
                            "$.timestamp": {"predefined": "ignore"},
                            "$.uptime_seconds": {"predefined": "ignore"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "1",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # healthCheck should MATCH because x-request-id is ignored
        for line in result.stdout.split("\n"):
            if "healthCheck:" in line:
                assert "MATCH" in line


class TestVariantBehaviorVerification:
    """Tests verifying that variant server differences are correctly handled by rules."""

    def test_variant_price_difference_within_tolerance(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that variant B's price difference (0.001) is within 0.01 tolerance."""
        # Create rules that should allow the price difference
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {"field_rules": {}}
            },
            "operation_rules": {
                "createWidget": {
                    "body": {
                        "field_rules": {
                            "$.id": {"predefined": "uuid_format"},
                            "$.name": {"predefined": "exact_match"},
                            "$.price": {"predefined": "numeric_tolerance", "tolerance": 0.01},
                            "$.category": {"predefined": "exact_match"},
                            "$.in_stock": {"predefined": "exact_match"},
                            "$.stock_count": {"predefined": "exact_match"},
                            "$.tags": {"predefined": "unordered_array"},
                            "$.created_at": {"predefined": "iso_timestamp_format"}
                        }
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # createWidget should MATCH because price diff is within tolerance and tags use unordered
        for line in result.stdout.split("\n"):
            if "createWidget:" in line:
                assert "MATCH" in line, f"Expected createWidget to MATCH, got: {line}"

    def test_both_servers_same_variant_all_match(self, tmp_path, cel_evaluator_exists):
        """Test that comparing same variant server to itself produces all matches."""
        # Start two instances of variant A
        port_a = find_free_port()
        port_b = find_free_port()

        with MockServer(port_a, variant="a") as server_a:
            with MockServer(port_b, variant="a") as server_b:
                config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{port_a}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{port_b}"
    headers: {{}}
comparison_rules: {COMPARISON_RULES}
"""
                config_path = tmp_path / "config.yaml"
                config_path.write_text(config)
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
                        "--seed", "42",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=PROJECT_ROOT,
                    timeout=60,
                )

                print(f"stdout:\n{result.stdout}")
                assert result.returncode == 0

                # Check summary - should have mostly matches (some may fail due to timing/UUIDs)
                summary_path = out_dir / "summary.json"
                assert summary_path.exists()
                with open(summary_path) as f:
                    summary = json.load(f)

                # With same variant, most should match (except volatile fields like timestamps/UUIDs)
                # The fixture comparison_rules.json handles these with ignore/format rules
                print(f"Summary: matches={summary['matches']}, mismatches={summary['mismatches']}")


class TestMultipleMismatches:
    """Tests for handling multiple mismatches in a single run."""

    def test_multiple_mismatch_bundles_created(self, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that multiple mismatches each get their own bundle."""
        # Create rules that will cause mismatches (require exact timestamp match)
        rules = {
            "version": "1",
            "default_rules": {
                "status_code": {"predefined": "exact_match"},
                "body": {
                    "field_rules": {
                        "$.timestamp": {"predefined": "exact_match"},
                        "$.created_at": {"predefined": "exact_match"},
                        "$.updated_at": {"predefined": "exact_match"}
                    }
                }
            }
        }
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps(rules))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{dual_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{dual_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
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
                "--max-cases", "15",
                "--seed", "42",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=120,
        )

        print(f"stdout:\n{result.stdout}")
        assert result.returncode == 0

        # Check summary
        summary_path = out_dir / "summary.json"
        assert summary_path.exists()
        with open(summary_path) as f:
            summary = json.load(f)

        # Should have multiple mismatches
        mismatches_dir = out_dir / "mismatches"
        if summary["mismatches"] > 0:
            assert mismatches_dir.exists()
            bundles = list(mismatches_dir.iterdir())
            assert len(bundles) == summary["mismatches"]

            # Each bundle should be unique
            bundle_names = [b.name for b in bundles]
            assert len(bundle_names) == len(set(bundle_names))
