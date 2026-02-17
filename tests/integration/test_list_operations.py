"""Tests for the list-operations CLI subcommand."""

import schemathesis

from tests.integration.cli_runner import run_cli
from tests.integration.explore_helpers import ALL_OPERATIONS, TEST_API_SPEC


class TestAllOperationsGuard:
    """Guard test ensuring ALL_OPERATIONS stays in sync with test_api.yaml."""

    def test_all_operations_matches_spec(self):
        """ALL_OPERATIONS must match operationIds in test_api.yaml.

        WHY: exclude_ops_except() relies on ALL_OPERATIONS to generate --exclude
        args. If this list drifts from the spec (e.g., an operation is added to
        test_api.yaml but not to ALL_OPERATIONS), tests silently run more
        operations than intended, making the suite slower.
        """
        schema = schemathesis.openapi.from_path(str(TEST_API_SPEC))
        spec_ops: set[str] = set()
        for result in schema.get_all_operations():
            op = result.ok()
            if op is not None:
                op_id = op.definition.raw.get("operationId")
                if op_id:
                    spec_ops.add(op_id)

        assert spec_ops == set(ALL_OPERATIONS), (
            f"ALL_OPERATIONS in explore_helpers.py is out of sync with test_api.yaml.\n"
            f"  In spec but not in ALL_OPERATIONS: {spec_ops - set(ALL_OPERATIONS)}\n"
            f"  In ALL_OPERATIONS but not in spec: {set(ALL_OPERATIONS) - spec_ops}"
        )


class TestListOperations:
    """Tests for the list-operations subcommand."""

    def test_list_operations_basic(self):
        """Test basic list-operations output."""
        result = run_cli(
            "list-operations",
            "--spec", str(TEST_API_SPEC),
        )

        assert result.returncode == 0
        # Check for expected operations
        assert "createWidget" in result.stdout
        assert "getWidget" in result.stdout
        assert "listWidgets" in result.stdout
        assert "Total:" in result.stdout

    def test_list_operations_shows_links(self):
        """Test that list-operations shows OpenAPI links."""
        result = run_cli(
            "list-operations",
            "--spec", str(TEST_API_SPEC),
        )

        assert result.returncode == 0
        # The test API has links from createWidget to getWidget
        # Check for link-related output
        assert "Links:" in result.stdout or "→" in result.stdout
