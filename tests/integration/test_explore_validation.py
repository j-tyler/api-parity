"""Tests for explore CLI validation mode and configuration errors."""

from tests.integration.cli_runner import run_cli
from tests.integration.explore_helpers import (
    TEST_API_SPEC,
    create_runtime_config,
)


class TestExploreValidateMode:
    """Tests for --validate mode (no execution, just config validation)."""

    def test_validate_mode_comprehensive(self, fixture_dual_mock_servers, tmp_path):
        """Test --validate mode functionality comprehensively.

        Combined test verifying:
        - Validation succeeds with valid config
        - Operations are listed in output
        - --exclude flag is respected
        """
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )
        out_dir = tmp_path / "artifacts"

        # Test basic validation
        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(out_dir),
            "--validate",
        )

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert "Validation successful" in result.stdout
        # Operations should be listed
        assert "listWidgets" in result.stdout or "createWidget" in result.stdout

        # Test --exclude flag
        result_exclude = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(out_dir),
            "--exclude", "healthCheck",
            "--validate",
        )

        assert result_exclude.returncode == 0
        assert "Excluding: healthCheck" in result_exclude.stdout


class TestExploreConfigErrors:
    """Tests for configuration error handling."""

    def test_config_error_handling(self, fixture_dual_mock_servers, tmp_path):
        """Test error handling for various configuration problems.

        Combined test verifying:
        - Missing config file is rejected
        - Invalid target name is rejected
        - Missing spec file is rejected
        """
        config_path = create_runtime_config(
            fixture_dual_mock_servers["a"].port,
            fixture_dual_mock_servers["b"].port,
            tmp_path,
        )

        # Test missing config file
        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(tmp_path / "nonexistent.yaml"),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(tmp_path / "out"),
        )
        assert result.returncode == 1
        assert "Error loading config" in result.stderr or "not found" in result.stderr

        # Test invalid target name
        result = run_cli(
            "explore",
            "--spec", str(TEST_API_SPEC),
            "--config", str(config_path),
            "--target-a", "nonexistent",
            "--target-b", "server_b",
            "--out", str(tmp_path / "out2"),
        )
        assert result.returncode == 1
        assert "not found" in result.stderr

        # Test missing spec file
        result = run_cli(
            "explore",
            "--spec", str(tmp_path / "nonexistent.yaml"),
            "--config", str(config_path),
            "--target-a", "server_a",
            "--target-b", "server_b",
            "--out", str(tmp_path / "out3"),
        )
        assert result.returncode == 1
        assert "Error" in result.stderr
