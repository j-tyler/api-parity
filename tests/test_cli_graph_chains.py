"""Tests for graph-chains subcommand."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api_parity.cli import (
    GraphChainsArgs,
    _extract_link_graph,
    _format_mermaid_graph,
    _format_mermaid_node,
    parse_args,
    parse_graph_chains_args,
    run_graph_chains,
)


class TestGraphChainsArgs:
    def test_basic_graph_chains(self):
        """Test graph-chains with required --spec."""
        args = parse_args(["graph-chains", "--spec", "openapi.yaml"])
        assert isinstance(args, GraphChainsArgs)
        assert args.spec == Path("openapi.yaml")
        assert args.exclude == []

    def test_graph_chains_with_exclude(self):
        """Test graph-chains with --exclude."""
        args = parse_args([
            "graph-chains",
            "--spec", "openapi.yaml",
            "--exclude", "healthCheck",
        ])
        assert isinstance(args, GraphChainsArgs)
        assert args.exclude == ["healthCheck"]

    def test_graph_chains_with_multiple_excludes(self):
        """Test graph-chains with multiple --exclude options."""
        args = parse_args([
            "graph-chains",
            "--spec", "openapi.yaml",
            "--exclude", "healthCheck",
            "--exclude", "listWidgets",
        ])
        assert isinstance(args, GraphChainsArgs)
        assert args.exclude == ["healthCheck", "listWidgets"]

    def test_graph_chains_missing_spec(self):
        """Test graph-chains fails without --spec."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["graph-chains"])
        assert exc_info.value.code == 2

    def test_parse_graph_chains_args_converts_correctly(self):
        """Test parse_graph_chains_args converts namespace to dataclass."""
        import argparse
        namespace = argparse.Namespace(
            command="graph-chains",
            spec=Path("spec.yaml"),
            exclude=["op1", "op2"],
        )
        args = parse_graph_chains_args(namespace)
        assert isinstance(args, GraphChainsArgs)
        assert args.spec == Path("spec.yaml")
        assert args.exclude == ["op1", "op2"]

    def test_parse_graph_chains_args_empty_exclude(self):
        """Test parse_graph_chains_args handles None exclude."""
        import argparse
        namespace = argparse.Namespace(
            command="graph-chains",
            spec=Path("spec.yaml"),
            exclude=None,
        )
        args = parse_graph_chains_args(namespace)
        assert args.exclude == []


class TestFormatMermaidNode:
    def test_simple_path(self):
        """Test node formatting with simple path."""
        node = _format_mermaid_node("createWidget", "POST", "/widgets")
        assert node == "createWidget[POST /widgets]"

    def test_path_with_param(self):
        """Test node formatting with path parameter."""
        node = _format_mermaid_node("getWidget", "GET", "/widgets/{widget_id}")
        assert node == "getWidget[GET /widgets/widget_id]"

    def test_path_with_multiple_params(self):
        """Test node formatting with multiple path parameters."""
        node = _format_mermaid_node("getItem", "GET", "/users/{user_id}/items/{item_id}")
        assert node == "getItem[GET /users/user_id/items/item_id]"


class TestFormatMermaidGraph:
    def test_simple_graph(self):
        """Test Mermaid graph generation with simple links."""
        operations = {
            "createWidget": ("POST", "/widgets"),
            "getWidget": ("GET", "/widgets/{id}"),
        }
        edges = [
            ("createWidget", "201", "getWidget"),
        ]
        mermaid = _format_mermaid_graph(operations, edges)
        assert "flowchart LR" in mermaid
        assert "createWidget[POST /widgets] -->|201| getWidget[GET /widgets/id]" in mermaid

    def test_graph_with_orphans(self):
        """Test Mermaid graph with orphan operations."""
        operations = {
            "createWidget": ("POST", "/widgets"),
            "getWidget": ("GET", "/widgets/{id}"),
            "healthCheck": ("GET", "/health"),
        }
        edges = [
            ("createWidget", "201", "getWidget"),
        ]
        mermaid = _format_mermaid_graph(operations, edges)
        assert "subgraph orphans[ORPHANS - no links]" in mermaid
        assert "healthCheck[GET /health]" in mermaid
        assert "end" in mermaid

    def test_graph_no_orphans(self):
        """Test Mermaid graph when all operations have links."""
        operations = {
            "createWidget": ("POST", "/widgets"),
            "getWidget": ("GET", "/widgets/{id}"),
        }
        edges = [
            ("createWidget", "201", "getWidget"),
            ("getWidget", "200", "createWidget"),
        ]
        mermaid = _format_mermaid_graph(operations, edges)
        assert "subgraph orphans" not in mermaid

    def test_empty_graph(self):
        """Test Mermaid graph with no operations."""
        operations = {}
        edges = []
        mermaid = _format_mermaid_graph(operations, edges)
        assert mermaid == "flowchart LR"

    def test_graph_edges_to_missing_operations_skipped(self):
        """Test that edges to operations not in the operations dict are skipped."""
        operations = {
            "createWidget": ("POST", "/widgets"),
        }
        edges = [
            ("createWidget", "201", "unknownOp"),
        ]
        mermaid = _format_mermaid_graph(operations, edges)
        # The edge should be skipped since unknownOp is not in operations
        assert "-->" not in mermaid


class TestExtractLinkGraph:
    def test_extract_from_mock_schema(self):
        """Test link extraction with mocked schema."""
        # Create mock operation
        mock_op = MagicMock()
        mock_op.method = "post"
        mock_op.path = "/widgets"
        mock_op.definition.raw = {
            "operationId": "createWidget",
            "responses": {
                "201": {
                    "links": {
                        "GetWidget": {
                            "operationId": "getWidget",
                        }
                    }
                }
            }
        }

        mock_op2 = MagicMock()
        mock_op2.method = "get"
        mock_op2.path = "/widgets/{id}"
        mock_op2.definition.raw = {
            "operationId": "getWidget",
            "responses": {
                "200": {}
            }
        }

        # Create mock results
        mock_result1 = MagicMock()
        mock_result1.ok.return_value = mock_op
        mock_result2 = MagicMock()
        mock_result2.ok.return_value = mock_op2

        # Create mock schema
        mock_schema = MagicMock()
        mock_schema.get_all_operations.return_value = [mock_result1, mock_result2]

        operations, edges = _extract_link_graph(mock_schema, exclude=[])

        assert "createWidget" in operations
        assert "getWidget" in operations
        assert operations["createWidget"] == ("POST", "/widgets")
        assert operations["getWidget"] == ("GET", "/widgets/{id}")
        assert ("createWidget", "201", "getWidget") in edges

    def test_exclude_filtering(self):
        """Test that excluded operations are filtered out."""
        mock_op = MagicMock()
        mock_op.method = "post"
        mock_op.path = "/widgets"
        mock_op.definition.raw = {
            "operationId": "createWidget",
            "responses": {
                "201": {
                    "links": {
                        "GetWidget": {
                            "operationId": "getWidget",
                        }
                    }
                }
            }
        }

        mock_result = MagicMock()
        mock_result.ok.return_value = mock_op

        mock_schema = MagicMock()
        mock_schema.get_all_operations.return_value = [mock_result]

        # Exclude createWidget
        operations, edges = _extract_link_graph(mock_schema, exclude=["createWidget"])

        assert "createWidget" not in operations
        assert len(edges) == 0

    def test_exclude_target_operation(self):
        """Test that links to excluded operations are not included."""
        mock_op = MagicMock()
        mock_op.method = "post"
        mock_op.path = "/widgets"
        mock_op.definition.raw = {
            "operationId": "createWidget",
            "responses": {
                "201": {
                    "links": {
                        "GetWidget": {
                            "operationId": "getWidget",
                        }
                    }
                }
            }
        }

        mock_result = MagicMock()
        mock_result.ok.return_value = mock_op

        mock_schema = MagicMock()
        mock_schema.get_all_operations.return_value = [mock_result]

        # Exclude getWidget (the target of the link)
        operations, edges = _extract_link_graph(mock_schema, exclude=["getWidget"])

        assert "createWidget" in operations
        # Edge should not be included since target is excluded
        assert len(edges) == 0

    def test_operations_without_operationId_skipped(self):
        """Test that operations without operationId are skipped."""
        mock_op = MagicMock()
        mock_op.method = "get"
        mock_op.path = "/health"
        mock_op.definition.raw = {
            "responses": {"200": {}}
        }

        mock_result = MagicMock()
        mock_result.ok.return_value = mock_op

        mock_schema = MagicMock()
        mock_schema.get_all_operations.return_value = [mock_result]

        operations, edges = _extract_link_graph(mock_schema, exclude=[])

        assert len(operations) == 0


class TestRunGraphChains:
    def test_run_with_test_fixture(self, tmp_path):
        """Test run_graph_chains with actual test fixture."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        args = GraphChainsArgs(spec=spec_path, exclude=[])
        result = run_graph_chains(args)
        assert result == 0

    def test_run_with_invalid_spec(self, tmp_path):
        """Test run_graph_chains with invalid spec path."""
        args = GraphChainsArgs(spec=tmp_path / "nonexistent.yaml", exclude=[])
        result = run_graph_chains(args)
        assert result == 1

    def test_run_with_exclude(self, tmp_path):
        """Test run_graph_chains excludes operations correctly."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        args = GraphChainsArgs(spec=spec_path, exclude=["healthCheck"])
        result = run_graph_chains(args)
        assert result == 0
