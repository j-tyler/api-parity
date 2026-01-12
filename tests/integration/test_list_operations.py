"""Tests for the list-operations CLI subcommand."""

import subprocess
import sys

from tests.integration.explore_helpers import PROJECT_ROOT, TEST_API_SPEC


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
            timeout=30,
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
            timeout=30,
        )

        assert result.returncode == 0
        # The test API has links from createWidget to getWidget
        # Check for link-related output
        assert "Links:" in result.stdout or "→" in result.stdout
