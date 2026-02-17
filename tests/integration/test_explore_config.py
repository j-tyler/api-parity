"""Tests for explore CLI configuration features."""

import json
import shutil

from tests.integration.cli_runner import run_cli
from tests.integration.explore_helpers import (
    COMPARISON_RULES,
    TEST_API_SPEC,
    create_runtime_config,
)


class TestConfigFeatures:
    """Tests for configuration features."""

    def test_config_features_comprehensive(self, monkeypatch, fixture_dual_mock_servers, tmp_path, fixture_cel_evaluator_path):
        """Test comprehensive configuration features.

        Combined test verifying:
        - Environment variable substitution works
        - Target headers are accepted
        - Relative comparison_rules paths work
        """
        # Test 1: Environment variable substitution
        monkeypatch.setenv("TEST_PORT_A", str(fixture_dual_mock_servers["a"].port))
        monkeypatch.setenv("TEST_PORT_B", str(fixture_dual_mock_servers["b"].port))

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:${{TEST_PORT_A}}"
    headers:
      X-Custom-Header: "test-value-a"
  server_b:
    base_url: "http://127.0.0.1:${{TEST_PORT_B}}"
    headers:
      X-Custom-Header: "test-value-b"
comparison_rules: {COMPARISON_RULES}
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
            "--validate",
        )

        assert result.returncode == 0
        # Verify the substituted URLs appear in output
        assert str(fixture_dual_mock_servers["a"].port) in result.stdout
        assert str(fixture_dual_mock_servers["b"].port) in result.stdout

        # Test 2: Relative comparison_rules path
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        shutil.copy(COMPARISON_RULES, rules_dir / "rules.json")

        config = f"""
targets:
  server_a:
    base_url: "http://127.0.0.1:{fixture_dual_mock_servers['a'].port}"
    headers: {{}}
  server_b:
    base_url: "http://127.0.0.1:{fixture_dual_mock_servers['b'].port}"
    headers: {{}}
comparison_rules: ../rules/rules.json
"""
        config_path = config_dir / "config.yaml"
        config_path.write_text(config)

        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(tmp_path / "artifacts2"),
            "--validate",
        )

        assert result.returncode == 0
        assert "Validation successful" in result.stdout

    def test_config_error_handling(self, fixture_dual_mock_servers, tmp_path):
        """Test error handling for configuration problems.

        Combined test verifying:
        - Same target for both A and B is rejected
        - Malformed comparison rules are rejected
        """
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )

        # Test same target rejected
        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_a",  # Same as target-a
            "--out", str(tmp_path / "out1"),
        )

        assert result.returncode == 1
        assert "different" in result.stderr.lower() or "same" in result.stderr.lower()

        # Test malformed comparison rules
        rules_path = tmp_path / "bad_rules.json"
        rules_path.write_text("{ this is not valid json }")

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
        bad_config_path = tmp_path / "bad_config.yaml"
        bad_config_path.write_text(config)

        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(bad_config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(tmp_path / "out2"),
        )

        assert result.returncode == 1
        assert "Error" in result.stderr
