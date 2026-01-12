"""Tests for explore CLI validation mode and configuration errors."""

import subprocess
import sys

from tests.integration.explore_helpers import (
    PROJECT_ROOT,
    TEST_API_SPEC,
    create_runtime_config,
)


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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
        )

        assert result.returncode == 1
        assert "Error" in result.stderr
