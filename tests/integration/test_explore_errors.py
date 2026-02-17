"""Tests for explore CLI error handling and connection errors."""

import json

from tests.conftest import find_free_port
from tests.integration.cli_runner import run_cli
from tests.integration.explore_helpers import (
    COMPARISON_RULES,
    TEST_API_SPEC,
    create_runtime_config,
    exclude_ops_except,
)


class TestErrorHandling:
    """Tests for error handling edge cases."""

    def test_connection_and_input_errors(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test error handling for connection and input errors.

        Combined test verifying:
        - Connection errors when server is down
        - Invalid YAML config is rejected
        - Invalid OpenAPI spec is rejected
        """
        # Test 1: Connection error when server is down
        port_a = find_free_port()
        port_b = find_free_port()

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
        config_path = tmp_path / "down_config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(out_dir),
            *exclude_ops_except("healthCheck"),
        )

        # Should complete (not crash) and report errors
        assert result.returncode == 0
        assert "ERROR" in result.stdout or "connection" in result.stdout.lower()

        # Test 2: Invalid YAML config
        bad_config_path = tmp_path / "bad_config.yaml"
        bad_config_path.write_text("targets:\n  - this: is\n    bad: yaml: syntax:")

        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(bad_config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(tmp_path / "out1"),
        )

        assert result.returncode == 1
        assert "Error" in result.stderr

        # Test 3: Invalid OpenAPI spec
        spec_path = tmp_path / "bad_spec.yaml"
        spec_path.write_text("not a valid openapi spec")

        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )

        result = run_cli(
            "explore",
            "--spec", str(spec_path),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(tmp_path / "out2"),
        )

        assert result.returncode == 1
        assert "Error" in result.stderr

    def test_invalid_cel_expression_recorded_as_mismatch(self, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test that invalid CEL expressions are recorded as mismatches with error info."""
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
    base_url: "http://127.0.0.1:{fixture_dual_mock_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{fixture_dual_mock_servers['b'].port}"
    headers: {{}}
comparison_rules: {rules_path}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        out_dir = tmp_path / "artifacts"

        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(out_dir),
            "--exclude", "createWidget",
            "--exclude", "getWidget",
            "--exclude", "updateWidget",
            "--exclude", "deleteWidget",
            "--exclude", "getUserProfile",
            "--exclude", "createOrder",
            "--exclude", "getOrder",
            "--exclude", "healthCheck",
        )

        # Should complete without crashing
        assert result.returncode == 0
        # listWidgets should be recorded as mismatch (due to CEL error)
        assert "MISMATCH" in result.stdout
