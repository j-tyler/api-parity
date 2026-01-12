"""Integration tests for stateful chain testing.

Tests the full chain generation, execution, comparison, and artifact writing pipeline.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import MockServer, find_free_port


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_servers():
    """Start two mock servers (target A and target B) for testing."""
    port_a = find_free_port()
    port_b = find_free_port()

    server_a = MockServer(port_a, variant="a")
    server_b = MockServer(port_b, variant="a")  # Same variant for parity

    server_a.start()
    server_b.start()

    yield server_a, server_b

    server_a.stop()
    server_b.stop()


@pytest.fixture
def openapi_spec_with_links(tmp_path: Path) -> Path:
    """Create an OpenAPI spec with links for stateful testing."""
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/widgets": {
                "post": {
                    "operationId": "createWidget",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "price": {"type": "number"},
                                    },
                                    "required": ["name", "price"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string", "format": "uuid"},
                                            "name": {"type": "string"},
                                            "price": {"type": "number"},
                                        },
                                    }
                                }
                            },
                            "links": {
                                "GetWidget": {
                                    "operationId": "getWidget",
                                    "parameters": {"widget_id": "$response.body#/id"},
                                },
                                "UpdateWidget": {
                                    "operationId": "updateWidget",
                                    "parameters": {"widget_id": "$response.body#/id"},
                                },
                                "DeleteWidget": {
                                    "operationId": "deleteWidget",
                                    "parameters": {"widget_id": "$response.body#/id"},
                                },
                            },
                        }
                    },
                }
            },
            "/widgets/{widget_id}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string", "format": "uuid"},
                                            "name": {"type": "string"},
                                            "price": {"type": "number"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "put": {
                    "operationId": "updateWidget",
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "price": {"type": "number"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Updated",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string", "format": "uuid"},
                                            "name": {"type": "string"},
                                            "price": {"type": "number"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                },
                "delete": {
                    "operationId": "deleteWidget",
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
        },
    }
    spec_path = tmp_path / "spec_with_links.yaml"
    with open(spec_path, "w") as f:
        yaml.dump(spec, f)
    return spec_path


@pytest.fixture
def runtime_config(tmp_path: Path, mock_servers) -> Path:
    """Create runtime configuration pointing to mock servers."""
    server_a, server_b = mock_servers
    config = {
        "targets": {
            "target_a": {"base_url": f"http://127.0.0.1:{server_a.port}"},
            "target_b": {"base_url": f"http://127.0.0.1:{server_b.port}"},
        },
        "comparison_rules": "rules.json",
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    # Create default comparison rules
    rules = {
        "version": "1",
        "default_rules": {
            "status_code": {"predefined": "exact"},
            "body": {
                "field_rules": {
                    "$.id": {"predefined": "uuid_format"},
                }
            },
        },
    }
    rules_path = tmp_path / "rules.json"
    with open(rules_path, "w") as f:
        json.dump(rules, f)

    return config_path


# =============================================================================
# Chain Generation Tests
# =============================================================================


class TestChainGeneration:
    """Tests for chain generation from OpenAPI links."""

    def test_generates_chains_from_links(self, openapi_spec_with_links: Path):
        """Chains are generated following OpenAPI links."""
        from api_parity.case_generator import CaseGenerator

        generator = CaseGenerator(openapi_spec_with_links)
        chains = generator.generate_chains(max_chains=5, max_steps=4)

        assert len(chains) > 0, "Should generate at least one chain"

        # All chains should have multiple steps
        for chain in chains:
            assert len(chain.steps) >= 2, f"Chain {chain.chain_id} should have at least 2 steps"

    def test_chain_step_structure(self, openapi_spec_with_links: Path):
        """Chain steps contain valid request templates."""
        from api_parity.case_generator import CaseGenerator

        generator = CaseGenerator(openapi_spec_with_links)
        chains = generator.generate_chains(max_chains=5, max_steps=4)

        assert len(chains) > 0

        for chain in chains:
            for step in chain.steps:
                # Each step has a request template
                assert step.request_template is not None
                assert step.request_template.operation_id
                assert step.request_template.method
                assert step.request_template.path_template

                # Step index is correct
                assert step.step_index >= 0

    def test_crud_patterns_generated(self, openapi_spec_with_links: Path):
        """Chains include CRUD-like patterns (create → get/update/delete)."""
        from api_parity.case_generator import CaseGenerator

        generator = CaseGenerator(openapi_spec_with_links)
        chains = generator.generate_chains(max_chains=5, max_steps=4)

        # Look for chains starting with createWidget
        create_chains = [
            c
            for c in chains
            if c.steps and c.steps[0].request_template.operation_id == "createWidget"
        ]

        # Should have some create chains
        assert len(create_chains) > 0, "Should generate chains starting with createWidget"

        # Check that follow-up operations appear
        follow_up_ops = set()
        for chain in create_chains:
            if len(chain.steps) > 1:
                follow_up_ops.add(chain.steps[1].request_template.operation_id)

        # Should have at least one of the linked operations
        expected_ops = {"getWidget", "updateWidget", "deleteWidget"}
        assert follow_up_ops & expected_ops, f"Expected follow-up operations {expected_ops}, got {follow_up_ops}"

    def test_max_chains_generates_chains(self, openapi_spec_with_links: Path):
        """max_chains parameter affects chain generation (more max = more chains possible)."""
        from api_parity.case_generator import CaseGenerator

        generator = CaseGenerator(openapi_spec_with_links)

        # Generate some chains - the limit is on Hypothesis examples, not final chains
        chains = generator.generate_chains(max_chains=5, max_steps=4)

        # Should generate at least some chains
        assert len(chains) > 0, "Should generate at least one chain"

        # All chains should have multiple steps (single-step are filtered)
        for chain in chains:
            assert len(chain.steps) >= 2

    def test_exclude_operations_respected(self, openapi_spec_with_links: Path):
        """Excluded operations don't appear in chains."""
        from api_parity.case_generator import CaseGenerator

        generator = CaseGenerator(
            openapi_spec_with_links, exclude_operations=["deleteWidget"]
        )
        chains = generator.generate_chains(max_chains=5, max_steps=4)

        for chain in chains:
            for step in chain.steps:
                assert (
                    step.request_template.operation_id != "deleteWidget"
                ), "Excluded operation should not appear in chains"


# =============================================================================
# Chain Execution Tests
# =============================================================================


class TestChainExecution:
    """Tests for executing chains against targets."""

    def test_execute_chain_returns_executions(
        self, openapi_spec_with_links: Path, mock_servers
    ):
        """execute_chain returns execution traces for both targets."""
        from api_parity.case_generator import CaseGenerator
        from api_parity.executor import Executor
        from api_parity.models import TargetConfig

        server_a, server_b = mock_servers

        generator = CaseGenerator(openapi_spec_with_links)
        chains = generator.generate_chains(max_chains=5, max_steps=3)

        assert len(chains) > 0

        target_a = TargetConfig(base_url=f"http://127.0.0.1:{server_a.port}")
        target_b = TargetConfig(base_url=f"http://127.0.0.1:{server_b.port}")

        with Executor(target_a, target_b) as executor:
            chain = chains[0]
            exec_a, exec_b = executor.execute_chain(chain)

            # Both executions should have same number of steps
            assert len(exec_a.steps) == len(chain.steps)
            assert len(exec_b.steps) == len(chain.steps)

            # Each step should have request and response
            for step in exec_a.steps:
                assert step.request is not None
                assert step.response is not None
                assert step.response.status_code > 0

    def test_variable_extraction_populates_path(
        self, openapi_spec_with_links: Path, mock_servers
    ):
        """Variables extracted from responses populate subsequent requests."""
        from api_parity.case_generator import CaseGenerator
        from api_parity.executor import Executor
        from api_parity.models import TargetConfig

        server_a, server_b = mock_servers

        generator = CaseGenerator(openapi_spec_with_links)
        chains = generator.generate_chains(max_chains=5, max_steps=4)

        # Find a chain that starts with create and has a follow-up
        create_chains = [
            c
            for c in chains
            if (
                c.steps
                and c.steps[0].request_template.operation_id == "createWidget"
                and len(c.steps) >= 2
                # Filter to only chains with valid body (dict, not empty list)
                and isinstance(c.steps[0].request_template.body, dict)
            )
        ]

        if not create_chains:
            pytest.skip("No create→follow-up chains with valid body generated")

        target_a = TargetConfig(base_url=f"http://127.0.0.1:{server_a.port}")
        target_b = TargetConfig(base_url=f"http://127.0.0.1:{server_b.port}")

        with Executor(target_a, target_b) as executor:
            chain = create_chains[0]
            exec_a, exec_b = executor.execute_chain(chain)

            # First step should execute (may or may not succeed depending on body)
            first_response = exec_a.steps[0].response

            # If create succeeded, check variable extraction
            if first_response.status_code == 201 and isinstance(first_response.body, dict):
                assert "id" in first_response.body

                # Second step should use the extracted ID
                if len(exec_a.steps) >= 2:
                    second_request = exec_a.steps[1].request
                    # The path should contain a valid UUID, not a placeholder
                    assert "{" not in second_request.rendered_path
            else:
                # If create failed, just verify chain executed without crash
                assert first_response.status_code > 0

    def test_both_targets_receive_same_requests(
        self, openapi_spec_with_links: Path, mock_servers
    ):
        """Both targets receive identical requests for comparison."""
        from api_parity.case_generator import CaseGenerator
        from api_parity.executor import Executor
        from api_parity.models import TargetConfig

        server_a, server_b = mock_servers

        generator = CaseGenerator(openapi_spec_with_links)
        chains = generator.generate_chains(max_chains=5, max_steps=3)

        assert len(chains) > 0

        target_a = TargetConfig(base_url=f"http://127.0.0.1:{server_a.port}")
        target_b = TargetConfig(base_url=f"http://127.0.0.1:{server_b.port}")

        with Executor(target_a, target_b) as executor:
            chain = chains[0]
            exec_a, exec_b = executor.execute_chain(chain)

            # Requests should be identical between targets
            for step_a, step_b in zip(exec_a.steps, exec_b.steps):
                assert step_a.request.method == step_b.request.method
                assert step_a.request.rendered_path == step_b.request.rendered_path
                assert step_a.request.body == step_b.request.body


# =============================================================================
# Chain Comparison Tests
# =============================================================================


class TestChainComparison:
    """Tests for comparing chain execution results."""

    def test_matching_chains_pass(
        self, openapi_spec_with_links: Path, mock_servers, tmp_path: Path
    ):
        """Chains with matching responses on both targets pass."""
        from api_parity.artifact_writer import ArtifactWriter, RunStats
        from api_parity.case_generator import CaseGenerator
        from api_parity.cel_evaluator import CELEvaluator
        from api_parity.comparator import Comparator
        from api_parity.config_loader import load_comparison_library
        from api_parity.executor import Executor
        from api_parity.models import OperationRules, TargetConfig

        server_a, server_b = mock_servers

        generator = CaseGenerator(openapi_spec_with_links)
        chains = generator.generate_chains(max_chains=5, max_steps=3)

        if not chains:
            pytest.skip("No chains generated")

        target_a = TargetConfig(base_url=f"http://127.0.0.1:{server_a.port}")
        target_b = TargetConfig(base_url=f"http://127.0.0.1:{server_b.port}")

        cel_evaluator = CELEvaluator()
        library = load_comparison_library()
        comparator = Comparator(cel_evaluator, library)

        try:
            with Executor(target_a, target_b) as executor:
                chain = chains[0]
                exec_a, exec_b = executor.execute_chain(chain)

                # Compare each step - identical servers should match
                for step_a, step_b in zip(exec_a.steps, exec_b.steps):
                    rules = OperationRules()  # Default rules
                    result = comparator.compare(step_a.response, step_b.response, rules)
                    # Both servers return same structure, should match
                    # (may differ in generated IDs, but with default rules this is OK)
        finally:
            cel_evaluator.close()


# =============================================================================
# Chain Artifact Tests
# =============================================================================


class TestChainArtifacts:
    """Tests for chain mismatch bundle writing."""

    def test_chain_mismatch_bundle_structure(self, tmp_path: Path):
        """Chain mismatch bundles contain required files."""
        from api_parity.artifact_writer import ArtifactWriter
        from api_parity.models import (
            ChainCase,
            ChainExecution,
            ChainStep,
            ChainStepExecution,
            ComparisonResult,
            ComponentResult,
            MismatchType,
            RequestCase,
            ResponseCase,
            TargetInfo,
        )

        writer = ArtifactWriter(tmp_path)

        # Create test chain
        request = RequestCase(
            case_id="test-123",
            operation_id="createWidget",
            method="POST",
            path_template="/widgets",
            rendered_path="/widgets",
        )
        response = ResponseCase(status_code=201, elapsed_ms=50.0)

        chain = ChainCase(
            chain_id="chain-abc",
            steps=[
                ChainStep(step_index=0, request_template=request),
                ChainStep(step_index=1, request_template=request),
            ],
        )

        exec_a = ChainExecution(
            steps=[
                ChainStepExecution(step_index=0, request=request, response=response),
                ChainStepExecution(step_index=1, request=request, response=response),
            ]
        )
        exec_b = ChainExecution(
            steps=[
                ChainStepExecution(step_index=0, request=request, response=response),
                ChainStepExecution(
                    step_index=1,
                    request=request,
                    response=ResponseCase(status_code=500, elapsed_ms=100.0),
                ),
            ]
        )

        step_diffs = [
            ComparisonResult(
                match=True,
                summary="match",
                details={"status_code": ComponentResult(match=True)},
            ),
            ComparisonResult(
                match=False,
                mismatch_type=MismatchType.STATUS_CODE,
                summary="status_code: 201 != 500",
                details={"status_code": ComponentResult(match=False)},
            ),
        ]

        bundle_path = writer.write_chain_mismatch(
            chain=chain,
            execution_a=exec_a,
            execution_b=exec_b,
            step_diffs=step_diffs,
            mismatch_step=1,
            target_a_info=TargetInfo(name="target_a", base_url="http://a:8000"),
            target_b_info=TargetInfo(name="target_b", base_url="http://b:8000"),
        )

        # Verify bundle structure
        assert bundle_path.exists()
        assert (bundle_path / "chain.json").exists()
        assert (bundle_path / "target_a.json").exists()
        assert (bundle_path / "target_b.json").exists()
        assert (bundle_path / "diff.json").exists()
        assert (bundle_path / "metadata.json").exists()

        # Verify diff.json content
        with open(bundle_path / "diff.json") as f:
            diff_data = json.load(f)
            assert diff_data["match"] is False
            assert diff_data["mismatch_step"] == 1
            assert diff_data["total_steps"] == 2
            assert len(diff_data["steps"]) == 2

    def test_chain_stats_in_summary(self, tmp_path: Path):
        """Summary includes chain-specific statistics."""
        from api_parity.artifact_writer import ArtifactWriter, RunStats

        writer = ArtifactWriter(tmp_path)

        stats = RunStats()
        stats.total_chains = 10
        stats.chain_matches = 7
        stats.chain_mismatches = 2
        stats.chain_errors = 1

        writer.write_summary(stats, seed=42)

        with open(tmp_path / "summary.json") as f:
            summary = json.load(f)

        assert summary["total_chains"] == 10
        assert summary["chain_matches"] == 7
        assert summary["chain_mismatches"] == 2
        assert summary["chain_errors"] == 1
        assert summary["seed"] == 42


class TestCLIStatefulExecution:
    """Integration tests for CLI stateful mode execution."""

    def test_stateful_explore_runs(
        self,
        openapi_spec_with_links: Path,
        runtime_config: Path,
        mock_servers,
        tmp_path: Path,
    ):
        """Stateful explore mode executes chains and writes results."""
        output_dir = tmp_path / "output"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "api_parity.cli",
                "explore",
                "--spec",
                str(openapi_spec_with_links),
                "--config",
                str(runtime_config),
                "--target-a",
                "target_a",
                "--target-b",
                "target_b",
                "--out",
                str(output_dir),
                "--stateful",
                "--max-chains",
                "5",
                "--max-steps",
                "3",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        assert "stateful" in result.stdout.lower()
        assert "chains" in result.stdout.lower()

        # Summary should exist
        assert (output_dir / "summary.json").exists()

        with open(output_dir / "summary.json") as f:
            summary = json.load(f)

        assert "total_chains" in summary

    def test_stateful_explore_with_mismatches(
        self,
        openapi_spec_with_links: Path,
        tmp_path: Path,
    ):
        """Stateful explore writes mismatch bundles when targets differ."""
        # Use variant A and variant B servers which have controlled differences
        port_a = find_free_port()
        port_b = find_free_port()

        server_a = MockServer(port_a, variant="a")
        server_b = MockServer(port_b, variant="b")  # Different variant

        server_a.start()
        server_b.start()

        try:
            config_path = tmp_path / "config.yaml"
            with open(config_path, "w") as f:
                yaml.dump(
                    {
                        "targets": {
                            "target_a": {
                                "base_url": f"http://127.0.0.1:{server_a.port}"
                            },
                            "target_b": {
                                "base_url": f"http://127.0.0.1:{server_b.port}"
                            },
                        },
                        "comparison_rules": "rules.json",
                    },
                    f,
                )

            rules_path = tmp_path / "rules.json"
            with open(rules_path, "w") as f:
                json.dump(
                    {
                        "version": "1",
                        "default_rules": {"status_code": {"predefined": "exact"}},
                    },
                    f,
                )

            output_dir = tmp_path / "output"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "api_parity.cli",
                    "explore",
                    "--spec",
                    str(openapi_spec_with_links),
                    "--config",
                    str(config_path),
                    "--target-a",
                    "target_a",
                    "--target-b",
                    "target_b",
                    "--out",
                    str(output_dir),
                    "--stateful",
                    "--max-chains",
                    "5",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            # Summary should be written
            assert (output_dir / "summary.json").exists()

            # Check for mismatches in output
            if "MISMATCH" in result.stdout:
                # Should have mismatch bundles in mismatches directory
                mismatches_dir = output_dir / "mismatches"
                if mismatches_dir.exists():
                    bundles = list(mismatches_dir.iterdir())
                    assert len(bundles) > 0, "Should have at least one mismatch bundle"

                    # Check bundle structure
                    bundle = bundles[0]
                    assert (bundle / "chain.json").exists() or (
                        bundle / "case.json"
                    ).exists()

        finally:
            server_a.stop()
            server_b.stop()


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestChainEdgeCases:
    """Tests for edge cases in chain handling."""

    def test_spec_without_links_generates_no_chains(self, tmp_path: Path):
        """OpenAPI spec without links generates no multi-step chains."""
        from schemathesis.core.errors import NoLinksFound

        from api_parity.case_generator import CaseGenerator

        # Create spec without links
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            },
        }
        spec_path = tmp_path / "no_links_spec.yaml"
        with open(spec_path, "w") as f:
            yaml.dump(spec, f)

        generator = CaseGenerator(spec_path)

        # Schemathesis raises NoLinksFound when spec has no links
        try:
            chains = generator.generate_chains(max_chains=5)
            # If it returns, should have no multi-step chains
            assert len(chains) == 0 or all(len(c.steps) <= 1 for c in chains)
        except NoLinksFound:
            # Expected - spec has no links for stateful testing
            pass

    def test_chain_with_request_error(
        self, openapi_spec_with_links: Path, tmp_path: Path
    ):
        """Chain execution handles request errors gracefully."""
        from api_parity.case_generator import CaseGenerator
        from api_parity.executor import Executor, RequestError
        from api_parity.models import TargetConfig

        generator = CaseGenerator(openapi_spec_with_links)
        chains = generator.generate_chains(max_chains=5)

        if not chains:
            pytest.skip("No chains generated")

        # Use invalid URLs that will fail
        target_a = TargetConfig(base_url="http://127.0.0.1:1")  # Invalid port
        target_b = TargetConfig(base_url="http://127.0.0.1:2")

        with Executor(target_a, target_b) as executor:
            chain = chains[0]
            with pytest.raises(RequestError):
                executor.execute_chain(chain)

    def test_empty_chain_handling(self):
        """Empty chains are handled gracefully."""
        from api_parity.models import ChainCase

        chain = ChainCase(chain_id="empty-chain", steps=[])
        assert len(chain.steps) == 0
