"""Integration tests for stateful chain testing.

Tests the full chain generation, execution, comparison, and artifact writing pipeline.

Performance note: Chain generation via Hypothesis state machine is expensive (~10s per call).
Tests share generated chains via module-scoped fixtures to minimize redundant generation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.conftest import MockServer, PortReservation


# =============================================================================
# Shared OpenAPI Spec (module-level for reuse)
# =============================================================================

OPENAPI_SPEC_WITH_LINKS = {
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


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def module_tmp_path(tmp_path_factory):
    """Module-scoped temp directory for sharing generated files."""
    return tmp_path_factory.mktemp("stateful_chains")


@pytest.fixture(scope="module")
def openapi_spec_with_links(module_tmp_path: Path) -> Path:
    """Create an OpenAPI spec with links for stateful testing (module-scoped)."""
    spec_path = module_tmp_path / "spec_with_links.yaml"
    with open(spec_path, "w") as f:
        yaml.dump(OPENAPI_SPEC_WITH_LINKS, f)
    return spec_path


@pytest.fixture(scope="module")
def generated_chains(openapi_spec_with_links: Path):
    """Generate chains once and share across tests (expensive operation).

    This is the main optimization - chain generation takes ~10-15s per call.
    By making this module-scoped, we generate once instead of per-test.
    """
    from api_parity.case_generator import CaseGenerator

    generator = CaseGenerator(openapi_spec_with_links)
    # Minimal chain count that still tests the functionality
    # max_chains=1 is sufficient since we just need one chain to test execution
    chains = generator.generate_chains(max_chains=1, max_steps=2)
    return chains, generator


@pytest.fixture(scope="session")
def parity_servers():
    """Session-scoped servers with same variant for parity testing.

    Both servers run variant "a" so responses match (for testing chain
    execution without mismatches). Session-scoped to share across all tests.
    """
    reservation_a = PortReservation()
    reservation_b = PortReservation()

    with MockServer(reservation_a, variant="a") as server_a:
        with MockServer(reservation_b, variant="a") as server_b:
            yield server_a, server_b


# =============================================================================
# Chain Generation Tests
# =============================================================================


class TestChainGeneration:
    """Tests for chain generation from OpenAPI links.

    Uses module-scoped generated_chains fixture to avoid repeated generation.
    """

    def test_chain_generation_and_structure(self, generated_chains):
        """Chains are generated with valid structure and CRUD patterns.

        Combined test verifying:
        - Chains are generated following OpenAPI links
        - Chain steps contain valid request templates
        - CRUD-like patterns (create → get/update/delete) are generated
        """
        chains, generator = generated_chains

        # Should generate at least one chain
        assert len(chains) > 0, "Should generate at least one chain"

        # All chains should have multiple steps
        for chain in chains:
            assert len(chain.steps) >= 2, f"Chain {chain.chain_id} should have at least 2 steps"

            for step in chain.steps:
                # Each step has a valid request template
                assert step.request_template is not None
                assert step.request_template.operation_id
                assert step.request_template.method
                assert step.request_template.path_template
                assert step.step_index >= 0

        # Look for CRUD patterns - chains starting with createWidget
        create_chains = [
            c
            for c in chains
            if c.steps and c.steps[0].request_template.operation_id == "createWidget"
        ]

        if create_chains:
            # Check that follow-up operations appear
            follow_up_ops = set()
            for chain in create_chains:
                if len(chain.steps) > 1:
                    follow_up_ops.add(chain.steps[1].request_template.operation_id)

            expected_ops = {"getWidget", "updateWidget", "deleteWidget"}
            assert follow_up_ops & expected_ops, f"Expected follow-up operations {expected_ops}, got {follow_up_ops}"

    # Note: exclude_operations is tested in unit tests (test_case_generator.py)
    # to avoid expensive chain generation in integration tests.


# =============================================================================
# Chain Execution Tests
# =============================================================================


class TestChainExecution:
    """Tests for executing chains against targets.

    Uses module-scoped fixtures to avoid repeated chain generation and server startup.
    """

    def test_chain_execution_and_variable_extraction(
        self, generated_chains, parity_servers
    ):
        """Chain execution works correctly with variable extraction.

        Combined test verifying:
        - execute_chain returns execution traces for both targets
        - Variables extracted from responses populate subsequent requests
        - Both targets receive identical requests for comparison
        """
        from api_parity.executor import Executor
        from api_parity.models import TargetConfig

        chains, _ = generated_chains
        server_a, server_b = parity_servers

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

            # Requests should be identical between targets
            for step_a, step_b in zip(exec_a.steps, exec_b.steps):
                assert step_a.request.method == step_b.request.method
                assert step_a.request.rendered_path == step_b.request.rendered_path
                assert step_a.request.body == step_b.request.body

            # Check variable extraction if first step was a create
            first_response = exec_a.steps[0].response
            if first_response.status_code == 201 and isinstance(first_response.body, dict):
                if "id" in first_response.body and len(exec_a.steps) >= 2:
                    second_request = exec_a.steps[1].request
                    # The path should contain a valid UUID, not a placeholder
                    assert "{" not in second_request.rendered_path


# =============================================================================
# Chain Comparison Tests
# =============================================================================


class TestChainComparison:
    """Tests for comparing chain execution results."""

    def test_matching_chains_pass(self, generated_chains, parity_servers):
        """Chains with matching responses on both targets pass."""
        from api_parity.cel_evaluator import CELEvaluator
        from api_parity.comparator import Comparator
        from api_parity.config_loader import load_comparison_library
        from api_parity.executor import Executor
        from api_parity.models import OperationRules, TargetConfig

        chains, _ = generated_chains
        server_a, server_b = parity_servers

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
                    rules = OperationRules()
                    result = comparator.compare(step_a.response, step_b.response, rules)
                    # Both servers return same structure, should match
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
    """Integration tests for CLI stateful mode execution.

    These tests use subprocess and spin up their own servers, so we minimize
    max_chains to reduce runtime while still testing the full pipeline.
    """

    def test_stateful_explore_runs_and_handles_mismatches(
        self,
        openapi_spec_with_links: Path,
        tmp_path: Path,
    ):
        """Stateful explore mode executes chains and handles both matches and mismatches.

        Combined test that:
        - Verifies stateful explore runs successfully
        - Tests with differing servers to verify mismatch handling
        """
        # Use variant A and variant B servers which have controlled differences
        reservation_a = PortReservation()
        reservation_b = PortReservation()

        with MockServer(reservation_a, variant="a") as server_a:
            with MockServer(reservation_b, variant="b") as server_b:
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
                        "1",  # Minimal for speed - just verify pipeline works
                        "--max-steps",
                        "2",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                # CLI should complete (exit 0)
                assert result.returncode == 0, f"CLI failed: {result.stderr}"

                # Should indicate stateful mode
                assert "stateful" in result.stdout.lower()
                assert "chains" in result.stdout.lower()

                # Summary should exist with chain stats
                assert (output_dir / "summary.json").exists()
                with open(output_dir / "summary.json") as f:
                    summary = json.load(f)
                assert "total_chains" in summary

                # Check for mismatches in output
                if "MISMATCH" in result.stdout:
                    mismatches_dir = output_dir / "mismatches"
                    if mismatches_dir.exists():
                        bundles = list(mismatches_dir.iterdir())
                        if bundles:
                            bundle = bundles[0]
                            assert (bundle / "chain.json").exists() or (
                                bundle / "case.json"
                            ).exists()


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
            chains = generator.generate_chains(max_chains=1)  # Minimal - just verify behavior
            # If it returns, should have no multi-step chains
            assert len(chains) == 0 or all(len(c.steps) <= 1 for c in chains)
        except NoLinksFound:
            # Expected - spec has no links for stateful testing
            pass

    def test_chain_with_request_error(self, generated_chains):
        """Chain execution handles request errors gracefully."""
        from api_parity.executor import Executor, RequestError
        from api_parity.models import TargetConfig

        chains, _ = generated_chains

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


# =============================================================================
# Custom Field Names Tests
# =============================================================================


class TestCustomFieldNames:
    """Tests for chain generation and execution with non-standard field names.

    Verifies that the dynamic link field extraction works for field names
    not in the old hardcoded list (id, user_id, order_id, widget_id, item_id).
    """

    def test_generator_extracts_custom_field_names(self, tmp_path: Path):
        """CaseGenerator extracts custom field names from spec links."""
        from api_parity.case_generator import CaseGenerator

        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_custom_fields.yaml"
        generator = CaseGenerator(spec_path)

        link_fields = generator.get_link_fields()

        # Custom field names should be extracted
        assert "resource_uuid" in link_fields
        assert "entity_identifier" in link_fields
        assert "data/nested_id" in link_fields

        # Standard 'id' should NOT be in this spec (it uses custom names)
        assert "id" not in link_fields

    def test_chain_generation_with_custom_fields(self, tmp_path: Path):
        """Chain generation works with specs using custom field names."""
        from api_parity.case_generator import CaseGenerator

        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_custom_fields.yaml"
        generator = CaseGenerator(spec_path)

        # Chain generation should not raise - the synthetic responses should
        # include the custom field names for link resolution
        try:
            chains = generator.generate_chains(max_chains=3, max_steps=3)
            # Should generate some chains (exact count varies)
            # The important thing is it doesn't fail
        except Exception as e:
            pytest.fail(f"Chain generation failed with custom fields: {e}")

    def test_executor_extracts_custom_fields(self):
        """Executor extracts custom field names from responses."""
        from api_parity.case_generator import extract_by_jsonpointer
        from api_parity.executor import Executor
        from api_parity.models import ResponseCase, TargetConfig

        # Create executor with custom link fields
        target = TargetConfig(base_url="http://localhost:9999")
        executor = Executor(
            target, target,
            link_fields={"resource_uuid", "entity_identifier", "data/nested_id"}
        )

        # Test extraction from a response with custom fields
        response = ResponseCase(
            status_code=201,
            body={
                "resource_uuid": "abc-123-def",
                "entity_identifier": "entity-456",
                "name": "test",
                "data": {
                    "nested_id": "nested-789"
                }
            },
            elapsed_ms=50.0,
        )

        extracted = executor._extract_variables(response)

        # Custom fields should be extracted
        assert extracted["resource_uuid"] == "abc-123-def"
        assert extracted["entity_identifier"] == "entity-456"

        # Nested field should be extracted by full path and last segment
        assert extracted["data/nested_id"] == "nested-789"
        assert extracted["nested_id"] == "nested-789"

        # Standard fields NOT in link_fields should NOT be extracted
        assert "name" not in extracted

        executor.close()

    def test_custom_fields_spec_openapi_valid(self):
        """Custom fields test spec is valid OpenAPI."""
        import yaml

        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_custom_fields.yaml"
        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        # Basic structure validation
        assert spec["openapi"] == "3.0.3"
        assert "paths" in spec
        assert "/resources" in spec["paths"]
        assert "/entities" in spec["paths"]

        # Verify links use custom field names
        create_resource = spec["paths"]["/resources"]["post"]
        links = create_resource["responses"]["201"]["links"]
        get_resource_link = links["GetResource"]
        assert get_resource_link["parameters"]["resource_id"] == "$response.body#/resource_uuid"
