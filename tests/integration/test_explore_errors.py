"""Tests for explore CLI error handling and connection errors."""

import json
import subprocess
import sys

from tests.conftest import find_free_port
from tests.integration.explore_helpers import (
    COMPARISON_RULES,
    PROJECT_ROOT,
    TEST_API_SPEC,
    create_runtime_config,
)


class TestTimeoutAndConnectionErrors:
    """Tests for timeout enforcement and connection error handling."""

    def test_connection_error_when_server_down(self, tmp_path, cel_evaluator_exists):
        """Test that connection errors are handled gracefully when server is unreachable."""
        # Use dynamically allocated ports to avoid conflicts with other processes
        port_a = find_free_port()
        port_b = find_free_port()

        # Create config pointing to non-existent server (ports are free, no server running)
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
