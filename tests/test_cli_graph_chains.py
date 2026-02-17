"""Tests for graph-chains subcommand."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api_parity.cli import (
    GraphChainsArgs,
    _extract_declared_links,
    _extract_link_graph,
    _format_mermaid_graph,
    _format_mermaid_node,
    _run_graph_chains_generated,
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
            generated=False,
            max_chains=20,
            max_steps=6,
            seed=None,
        )
        args = parse_graph_chains_args(namespace)
        assert isinstance(args, GraphChainsArgs)
        assert args.spec == Path("spec.yaml")
        assert args.exclude == ["op1", "op2"]
        assert args.generated is False
        assert args.max_chains == 20
        assert args.max_steps == 6
        assert args.seed is None

    def test_parse_graph_chains_args_empty_exclude(self):
        """Test parse_graph_chains_args handles None exclude."""
        import argparse
        namespace = argparse.Namespace(
            command="graph-chains",
            spec=Path("spec.yaml"),
            exclude=None,
            generated=False,
            max_chains=20,
            max_steps=6,
            seed=None,
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


class TestGraphChainsGeneratedArgs:
    """Tests for --generated flag and related options."""

    def test_generated_flag_parsing(self):
        """Test --generated flag is parsed correctly."""
        args = parse_args([
            "graph-chains",
            "--spec", "openapi.yaml",
            "--generated",
        ])
        assert isinstance(args, GraphChainsArgs)
        assert args.generated is True
        # Check defaults
        assert args.max_chains == 20
        assert args.max_steps == 6
        assert args.seed is None

    def test_max_chains_option(self):
        """Test --max-chains option is parsed correctly."""
        args = parse_args([
            "graph-chains",
            "--spec", "openapi.yaml",
            "--generated",
            "--max-chains", "50",
        ])
        assert isinstance(args, GraphChainsArgs)
        assert args.max_chains == 50

    def test_max_steps_option(self):
        """Test --max-steps option is parsed correctly."""
        args = parse_args([
            "graph-chains",
            "--spec", "openapi.yaml",
            "--generated",
            "--max-steps", "10",
        ])
        assert isinstance(args, GraphChainsArgs)
        assert args.max_steps == 10

    def test_seed_option(self):
        """Test --seed option is parsed correctly."""
        args = parse_args([
            "graph-chains",
            "--spec", "openapi.yaml",
            "--generated",
            "--seed", "12345",
        ])
        assert isinstance(args, GraphChainsArgs)
        assert args.seed == 12345

    def test_all_generated_options_together(self):
        """Test all generated options can be used together."""
        args = parse_args([
            "graph-chains",
            "--spec", "openapi.yaml",
            "--generated",
            "--max-chains", "30",
            "--max-steps", "8",
            "--seed", "42",
            "--exclude", "healthCheck",
        ])
        assert isinstance(args, GraphChainsArgs)
        assert args.generated is True
        assert args.max_chains == 30
        assert args.max_steps == 8
        assert args.seed == 42
        assert args.exclude == ["healthCheck"]

    def test_parse_graph_chains_args_with_generated(self):
        """Test parse_graph_chains_args handles generated options."""
        import argparse
        namespace = argparse.Namespace(
            command="graph-chains",
            spec=Path("spec.yaml"),
            exclude=["op1"],
            generated=True,
            max_chains=25,
            max_steps=5,
            seed=999,
        )
        args = parse_graph_chains_args(namespace)
        assert args.generated is True
        assert args.max_chains == 25
        assert args.max_steps == 5
        assert args.seed == 999


class TestExtractDeclaredLinks:
    """Tests for _extract_declared_links function."""

    def test_extracts_links_from_spec(self):
        """Test link extraction from a simple spec."""
        spec = {
            "paths": {
                "/widgets": {
                    "post": {
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
                },
                "/widgets/{id}": {
                    "get": {
                        "operationId": "getWidget",
                        "responses": {"200": {}}
                    }
                }
            }
        }
        links = _extract_declared_links(spec, exclude=set())
        assert len(links) == 1
        assert ("createWidget", "201", "getWidget", "GetWidget") in links

    def test_excludes_source_operation(self):
        """Test excluded source operations are skipped."""
        spec = {
            "paths": {
                "/widgets": {
                    "post": {
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
                }
            }
        }
        links = _extract_declared_links(spec, exclude={"createWidget"})
        assert len(links) == 0

    def test_excludes_target_operation(self):
        """Test links to excluded target operations are skipped."""
        spec = {
            "paths": {
                "/widgets": {
                    "post": {
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
                }
            }
        }
        links = _extract_declared_links(spec, exclude={"getWidget"})
        assert len(links) == 0

    def test_multiple_links_same_response(self):
        """Test extraction of multiple links from same response."""
        spec = {
            "paths": {
                "/widgets": {
                    "post": {
                        "operationId": "createWidget",
                        "responses": {
                            "201": {
                                "links": {
                                    "GetWidget": {"operationId": "getWidget"},
                                    "UpdateWidget": {"operationId": "updateWidget"},
                                    "DeleteWidget": {"operationId": "deleteWidget"},
                                }
                            }
                        }
                    }
                }
            }
        }
        links = _extract_declared_links(spec, exclude=set())
        assert len(links) == 3
        target_ops = {link[2] for link in links}
        assert target_ops == {"getWidget", "updateWidget", "deleteWidget"}


class TestRunGraphChainsGenerated:
    """Tests for _run_graph_chains_generated function.

    Performance note: Chain generation is expensive (~10-20s per call).
    Tests are consolidated to minimize redundant generation while
    maintaining coverage of all required behaviors.
    """

    def test_generated_comprehensive(self, capsys):
        """Comprehensive test for --generated mode verifying all output behaviors.

        Combined test that runs chain generation once and verifies:
        - Basic execution succeeds (return code 0)
        - Output format includes all expected sections
        - run_graph_chains correctly dispatches to generated mode
        - No Mermaid flowchart output when in generated mode
        - Seed is reported in output when provided
        """
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        # Use minimal chain count that still exercises the functionality
        args = GraphChainsArgs(
            spec=spec_path,
            exclude=[],
            generated=True,
            max_chains=1,  # Minimal for speed
            max_steps=2,
            seed=42,
        )
        result = run_graph_chains(args)
        assert result == 0

        captured = capsys.readouterr()

        # Should NOT contain Mermaid flowchart output (dispatch verification)
        assert "flowchart LR" not in captured.out

        # Should contain generated chains output
        assert "Generating chains" in captured.out

        # Should contain chain output or indicate no chains
        assert "Generated Chains" in captured.out or "No multi-step chains" in captured.out

        # Should contain link coverage summary with expected fields
        assert "Link Coverage Summary" in captured.out
        assert "Total declared links:" in captured.out
        assert "Links actually used:" in captured.out

        # Should contain operation coverage summary
        assert "Operation Coverage Summary" in captured.out
        assert "Total operations in spec:" in captured.out
        assert "Operations tested:" in captured.out
        assert "Operations NEVER tested:" in captured.out

        # Should report the seed used (merged from TestSeedWalkingGenerated)
        assert "seed" in captured.out.lower() or "Seed" in captured.out

    def test_generated_with_invalid_spec(self, tmp_path, capsys):
        """Test --generated mode with invalid spec path."""
        args = GraphChainsArgs(
            spec=tmp_path / "nonexistent.yaml",
            exclude=[],
            generated=True,
            max_chains=1,
            max_steps=2,
            seed=None,
        )
        result = _run_graph_chains_generated(args)
        assert result == 1

        captured = capsys.readouterr()
        assert "Error loading spec" in captured.err

    def test_generated_with_exclude(self, capsys):
        """Test --generated mode respects --exclude."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        args = GraphChainsArgs(
            spec=spec_path,
            exclude=["createWidget", "createOrder"],  # Exclude entry points
            generated=True,
            max_chains=1,  # Minimal for speed
            max_steps=2,
            seed=42,
        )
        result = _run_graph_chains_generated(args)
        # Should still succeed even if no chains generated
        assert result == 0

    def test_generated_validates_max_chains(self, tmp_path, capsys):
        """Test that max_chains < 1 returns error."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        args = GraphChainsArgs(
            spec=spec_path,
            exclude=[],
            generated=True,
            max_chains=0,  # Invalid
            max_steps=3,
            seed=None,
        )
        result = _run_graph_chains_generated(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "--max-chains must be >= 1" in captured.err

    def test_generated_validates_max_steps(self, tmp_path, capsys):
        """Test that max_steps < 1 returns error."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        args = GraphChainsArgs(
            spec=spec_path,
            exclude=[],
            generated=True,
            max_chains=1,
            max_steps=-1,  # Invalid
            seed=None,
        )
        result = _run_graph_chains_generated(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "--max-steps must be >= 1" in captured.err

    def test_generated_without_seed_no_seed_report(self, capsys):
        """Test that --generated mode without seed doesn't report seeds."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        if not spec_path.exists():
            pytest.skip("Test fixture not found")

        args = GraphChainsArgs(
            spec=spec_path,
            exclude=[],
            generated=True,
            max_chains=1,
            max_steps=2,
            seed=None,  # No seed
        )
        result = run_graph_chains(args)
        assert result == 0

        captured = capsys.readouterr()

        # Should NOT report "Used seed" or "seeds:" since no seed walking
        assert "Used seed" not in captured.out
        assert "seeds:" not in captured.out


class TestExtractDeclaredLinksOperationRef:
    """Tests for _extract_declared_links with operationRef."""

    def test_extracts_links_with_operation_ref(self):
        """Test link extraction when operationRef is used instead of operationId."""
        spec = {
            "paths": {
                "/widgets": {
                    "post": {
                        "operationId": "createWidget",
                        "responses": {
                            "201": {
                                "links": {
                                    "GetWidget": {
                                        "operationRef": "#/paths/~1widgets~1{id}/get",
                                    }
                                }
                            }
                        }
                    }
                },
                "/widgets/{id}": {
                    "get": {
                        "operationId": "getWidget",
                        "responses": {"200": {}}
                    }
                }
            }
        }
        links = _extract_declared_links(spec, exclude=set())
        assert len(links) == 1
        # operationRef is stored as-is (not resolved to operationId)
        assert links[0][0] == "createWidget"
        assert links[0][1] == "201"
        assert links[0][2] == "#/paths/~1widgets~1{id}/get"
        assert links[0][3] == "GetWidget"

    def test_extracts_links_with_mixed_ref_types(self):
        """Test link extraction with both operationId and operationRef."""
        spec = {
            "paths": {
                "/widgets": {
                    "post": {
                        "operationId": "createWidget",
                        "responses": {
                            "201": {
                                "links": {
                                    "GetById": {"operationId": "getWidget"},
                                    "GetByRef": {"operationRef": "#/paths/~1other/get"},
                                }
                            }
                        }
                    }
                }
            }
        }
        links = _extract_declared_links(spec, exclude=set())
        assert len(links) == 2
        targets = {link[2] for link in links}
        assert targets == {"getWidget", "#/paths/~1other/get"}


class TestLinkSourceAccuracy:
    """Tests that link_source correctly identifies which link was used."""

    def test_generated_output_shows_correct_link_name(self, tmp_path, capsys):
        """Test that generated chains show the correct link name from the spec."""
        import yaml

        # Create a minimal spec with a known link
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "string"}},
                                        }
                                    }
                                },
                                "links": {
                                    "GetCreatedItem": {
                                        "operationId": "getItem",
                                        "parameters": {"item_id": "$response.body#/id"},
                                    }
                                },
                            }
                        },
                    }
                },
                "/items/{item_id}": {
                    "get": {
                        "operationId": "getItem",
                        "parameters": [
                            {
                                "name": "item_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"}
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "test_spec.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        args = GraphChainsArgs(
            spec=spec_file,
            exclude=[],
            generated=True,
            max_chains=1,  # Minimal for speed
            max_steps=2,
            seed=42,
        )
        result = _run_graph_chains_generated(args)
        assert result == 0

        captured = capsys.readouterr()
        # If chains were generated with the link, the link name should appear
        if "createItem -> getItem" in captured.out:
            # The link name "GetCreatedItem" should appear in the output
            assert "GetCreatedItem" in captured.out, (
                "Link name should appear when explicit link is used"
            )


class TestLinkAttributionHistory:
    """Tests for link attribution across full chain history.

    Verifies that _find_link_between() searches all previous steps,
    not just the immediately previous operation. This fixes the bug
    where "via unknown link (not in spec)" appeared for valid transitions
    when the link source was an earlier step in the chain.
    """

    def test_link_attribution_searches_all_previous_steps(self, tmp_path):
        """Link attribution finds links from any previous step, not just the last.

        Creates a 3-step chain where step 2 gets its parameter from step 0
        (not step 1). The link from step 0 to step 2 should be correctly
        attributed even though step 1 is in between.
        """
        import yaml

        from api_parity.case_generator import CaseGenerator

        # Create a spec where:
        # - createOrder returns order_id and links to both getOrderStatus and updateOrder
        # - getOrderStatus returns status_id and does NOT link to updateOrder
        # - updateOrder uses order_id from createOrder (step 0), not status_id from getOrderStatus (step 1)
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {
                "/orders": {
                    "post": {
                        "operationId": "createOrder",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"order_id": {"type": "string"}},
                                        }
                                    }
                                },
                                "links": {
                                    "GetOrderStatus": {
                                        "operationId": "getOrderStatus",
                                        "parameters": {"order_id": "$response.body#/order_id"},
                                    },
                                    "UpdateOrder": {
                                        "operationId": "updateOrder",
                                        "parameters": {"order_id": "$response.body#/order_id"},
                                    },
                                },
                            }
                        },
                    }
                },
                "/orders/{order_id}/status": {
                    "get": {
                        "operationId": "getOrderStatus",
                        "parameters": [
                            {
                                "name": "order_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"status_id": {"type": "string"}},
                                        }
                                    }
                                },
                                # NOTE: No link to updateOrder here - the chain must
                                # use the link from createOrder (step 0)
                            }
                        },
                    }
                },
                "/orders/{order_id}": {
                    "put": {
                        "operationId": "updateOrder",
                        "parameters": [
                            {
                                "name": "order_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "responses": {
                            "200": {
                                "description": "Updated",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"}
                                    }
                                },
                            }
                        },
                    }
                },
            },
        }

        spec_file = tmp_path / "test_spec.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        generator = CaseGenerator(spec_file)

        # Generate chains with enough steps to get create -> getStatus -> update pattern
        chains = generator.generate_chains(max_chains=10, max_steps=3, seed=42)

        # Find a chain with the pattern: createOrder -> getOrderStatus -> updateOrder
        target_chain = None
        for chain in chains:
            if len(chain.steps) >= 3:
                op_ids = [s.request_template.operation_id for s in chain.steps]
                if op_ids[:3] == ["createOrder", "getOrderStatus", "updateOrder"]:
                    target_chain = chain
                    break

        # Skip if Hypothesis didn't generate the exact pattern we need
        if target_chain is None:
            pytest.skip("Target chain pattern not generated by Hypothesis with this seed")

        step_2 = target_chain.steps[2]  # updateOrder
        link_source = step_2.link_source

        # The link should be attributed to createOrder (step 0), not getOrderStatus (step 1)
        assert link_source is not None, (
            "updateOrder should have link_source (from createOrder)"
        )
        assert link_source.get("source_operation") == "createOrder", (
            f"updateOrder link should come from createOrder, not "
            f"{link_source.get('source_operation')}"
        )
        assert link_source.get("link_name") == "UpdateOrder", (
            f"Link name should be UpdateOrder, got {link_source.get('link_name')}"
        )

    def test_most_recent_link_takes_precedence(self, tmp_path):
        """When multiple steps have links to target, most recent is preferred.

        If both step 0 and step 1 have links to step 2, the link from step 1
        should be used since it's more recent.
        """
        import yaml

        from api_parity.case_generator import CaseGenerator

        # Create a spec where both createResource and updateResource link to getResource
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {
                "/resources": {
                    "post": {
                        "operationId": "createResource",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "description": "Created",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "string"}},
                                        }
                                    }
                                },
                                "links": {
                                    "GetCreatedResource": {
                                        "operationId": "getResource",
                                        "parameters": {"id": "$response.body#/id"},
                                    },
                                    "UpdateResource": {
                                        "operationId": "updateResource",
                                        "parameters": {"id": "$response.body#/id"},
                                    },
                                },
                            }
                        },
                    }
                },
                "/resources/{id}": {
                    "get": {
                        "operationId": "getResource",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"}
                                    }
                                },
                            }
                        },
                    },
                    "put": {
                        "operationId": "updateResource",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "responses": {
                            "200": {
                                "description": "Updated",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "string"}},
                                        }
                                    }
                                },
                                "links": {
                                    "GetUpdatedResource": {
                                        "operationId": "getResource",
                                        "parameters": {"id": "$response.body#/id"},
                                    },
                                },
                            }
                        },
                    },
                },
            },
        }

        spec_file = tmp_path / "test_spec.yaml"
        with open(spec_file, "w") as f:
            yaml.dump(spec, f)

        generator = CaseGenerator(spec_file)

        # Generate chains
        chains = generator.generate_chains(max_chains=10, max_steps=3, seed=42)

        # Find a chain with pattern: createResource -> updateResource -> getResource
        target_chain = None
        for chain in chains:
            if len(chain.steps) >= 3:
                op_ids = [s.request_template.operation_id for s in chain.steps]
                if op_ids[:3] == ["createResource", "updateResource", "getResource"]:
                    target_chain = chain
                    break

        # Skip if Hypothesis didn't generate the exact pattern we need
        if target_chain is None:
            pytest.skip("Target chain pattern not generated by Hypothesis with this seed")

        step_2 = target_chain.steps[2]  # getResource
        link_source = step_2.link_source

        # Both createResource and updateResource link to getResource,
        # but updateResource (step 1) is more recent so its link should be used
        assert link_source is not None, (
            "getResource should have link_source"
        )
        assert link_source.get("source_operation") == "updateResource", (
            f"getResource link should come from updateResource (most recent), not "
            f"{link_source.get('source_operation')}"
        )
        assert link_source.get("link_name") == "GetUpdatedResource", (
            f"Link name should be GetUpdatedResource, got {link_source.get('link_name')}"
        )
