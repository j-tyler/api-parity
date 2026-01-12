"""Tests for explore CLI configuration features."""

import json
import os
import shutil
import subprocess
import sys

from tests.integration.explore_helpers import (
    COMPARISON_RULES,
    PROJECT_ROOT,
    TEST_API_SPEC,
    create_runtime_config,
)


class TestConfigFeatures:
    """Tests for configuration features."""

    def test_environment_variable_substitution(self, monkeypatch, dual_servers, tmp_path, cel_evaluator_exists):
        """Test that ${ENV_VAR} patterns are substituted in config."""
        # Use monkeypatch for safe environment variable manipulation
        monkeypatch.setenv("TEST_PORT_A", str(dual_servers["a"].port))
        monkeypatch.setenv("TEST_PORT_B", str(dual_servers["b"].port))

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
