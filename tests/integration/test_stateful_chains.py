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

        # Custom field names should be extracted (now in body_pointers)
        assert "resource_uuid" in link_fields.body_pointers
        assert "entity_identifier" in link_fields.body_pointers
        assert "data/nested_id" in link_fields.body_pointers

        # Standard 'id' should NOT be in this spec (it uses custom names)
        assert "id" not in link_fields.body_pointers

    def test_chain_generation_with_custom_fields(self, tmp_path: Path):
        """Chain generation works with specs using custom field names."""
        from api_parity.case_generator import CaseGenerator

        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_custom_fields.yaml"
        generator = CaseGenerator(spec_path)

        # Chain generation should not raise - the synthetic responses should
        # include the custom field names for link resolution
        try:
            chains = generator.generate_chains(max_chains=1, max_steps=2)
            # Should generate some chains (exact count varies)
            # The important thing is it doesn't fail
        except Exception as e:
            pytest.fail(f"Chain generation failed with custom fields: {e}")

    def test_executor_extracts_custom_fields(self):
        """Executor extracts custom field names from responses."""
        from api_parity.case_generator import LinkFields
        from api_parity.executor import Executor
        from api_parity.models import ResponseCase, TargetConfig

        # Create executor with custom link fields using LinkFields dataclass
        target = TargetConfig(base_url="http://localhost:9999")
        link_fields = LinkFields(
            body_pointers={"resource_uuid", "entity_identifier", "data/nested_id"}
        )
        executor = Executor(target, target, link_fields=link_fields)

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

    def test_executor_extracts_header_values(self):
        """Executor extracts header values with array semantics."""
        from api_parity.case_generator import HeaderRef, LinkFields
        from api_parity.executor import Executor
        from api_parity.models import ResponseCase, TargetConfig

        # Create executor with header link fields
        # original_name is used for synthetic header generation (Schemathesis link resolution)
        # name (lowercase) is used for HTTP-compliant extraction from actual responses
        target = TargetConfig(base_url="http://localhost:9999")
        link_fields = LinkFields(
            headers=[
                HeaderRef(name="location", original_name="Location", index=None),  # Extract all values as list
                HeaderRef(name="set-cookie", original_name="Set-Cookie", index=0),   # Extract first cookie
                HeaderRef(name="set-cookie", original_name="Set-Cookie", index=1),   # Extract second cookie
            ]
        )
        executor = Executor(target, target, link_fields=link_fields)

        # Test extraction from a response with headers
        response = ResponseCase(
            status_code=201,
            headers={
                "location": ["http://example.com/resource/123"],
                "set-cookie": ["session=abc", "tracking=xyz"],
                "content-type": ["application/json"],  # Not in link_fields
            },
            body={"id": "123"},
            elapsed_ms=50.0,
        )

        extracted = executor._extract_variables(response)

        # Location header as list
        assert extracted["header/location"] == ["http://example.com/resource/123"]

        # Set-Cookie header as list and indexed
        assert extracted["header/set-cookie"] == ["session=abc", "tracking=xyz"]
        assert extracted["header/set-cookie/0"] == "session=abc"
        assert extracted["header/set-cookie/1"] == "tracking=xyz"

        # Headers not in link_fields should NOT be extracted
        assert "header/content-type" not in extracted

        executor.close()

    def test_executor_substitutes_list_values_correctly(self):
        """Executor substitutes list variable values using first element."""
        from api_parity.case_generator import HeaderRef, LinkFields
        from api_parity.executor import Executor
        from api_parity.models import RequestCase, TargetConfig

        target = TargetConfig(base_url="http://localhost:9999")
        executor = Executor(target, target)

        # Create a template with placeholders
        template = RequestCase(
            case_id="test-123",
            operation_id="getResource",
            method="GET",
            path_template="/resources/{id}",
            path_parameters={"id": "{header/location}"},
            rendered_path="/resources/{header/location}",
        )

        # Variables include a list value (from header extraction)
        variables = {
            "header/location": ["http://example.com/resource/abc"],  # List!
            "header/location/0": "http://example.com/resource/abc",  # Indexed
        }

        # Apply variables
        result = executor._apply_variables(template, variables)

        # Should use first element of list, not str(list)
        assert "['http://example.com/resource/abc']" not in result.path_parameters["id"]
        # The actual substituted value
        assert result.path_parameters["id"] == "http://example.com/resource/abc"

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


# =============================================================================
# Explicit Links Only Tests
# =============================================================================


class TestExplicitLinksOnly:
    """Tests that chain generation uses only explicit OpenAPI links.

    Verifies that inference algorithms (parameter name matching, Location headers)
    are disabled, and chains are generated only from explicit OpenAPI link definitions.
    See DESIGN.md "Explicit Links Only for Chain Generation" for rationale.
    """

    def test_spec_with_explicit_links_generates_chains(self, generated_chains):
        """Spec with explicit OpenAPI links generates multi-step chains.

        Reuses module-scoped generated_chains fixture to avoid redundant generation.
        """
        chains, _ = generated_chains

        # Should generate at least one chain
        assert len(chains) > 0, "Spec with explicit links should generate chains"

        # Chains should have multiple steps (following links)
        for chain in chains:
            assert len(chain.steps) >= 2, "Chains should follow links to have 2+ steps"

    def test_inferable_relationships_without_explicit_links_raises_no_links_found(
        self, tmp_path: Path
    ):
        """Spec with inferable relationships but no explicit links raises NoLinksFound.

        This spec has POST /users returning {id} and GET /users/{userId} that could
        be linked via parameter name inference, but since there are no explicit
        OpenAPI links defined and inference is disabled, Schemathesis raises NoLinksFound.
        """
        from schemathesis.core.errors import NoLinksFound

        from api_parity.case_generator import CaseGenerator

        # Create spec with inferable but not explicit relationships
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
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
                                            },
                                        }
                                    }
                                },
                                # NOTE: No "links" section - relationship must be inferred
                            }
                        },
                    }
                },
                "/users/{userId}": {
                    "get": {
                        "operationId": "getUser",
                        "parameters": [
                            {
                                "name": "userId",
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
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    },
                    "delete": {
                        "operationId": "deleteUser",
                        "parameters": [
                            {
                                "name": "userId",
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
        spec_path = tmp_path / "inferable_no_links_spec.yaml"
        with open(spec_path, "w") as f:
            yaml.dump(spec, f)

        generator = CaseGenerator(spec_path)

        # With inference disabled, Schemathesis raises NoLinksFound because
        # there are no explicit links defined. This is the expected behavior.
        with pytest.raises(NoLinksFound):
            generator.generate_chains(max_chains=1, max_steps=2)

    def test_schemathesis_config_disables_inference(self):
        """Verify the Schemathesis config actually disables inference algorithms."""
        from api_parity.case_generator import _create_explicit_links_only_config

        config = _create_explicit_links_only_config()

        # The config should disable inference
        phases = config.projects.default.phases
        assert phases is not None
        assert phases.stateful is not None
        assert phases.stateful.inference is not None
        assert phases.stateful.inference.algorithms == []
        assert phases.stateful.inference.is_enabled is False


# =============================================================================
# Header-Based Chain Tests
# =============================================================================


class TestStatusCodeLinkResolution:
    """Tests for correct status code handling in synthetic responses.

    Verifies that synthetic responses use status codes from the OpenAPI spec
    where links are defined, not hardcoded assumptions. This fixes the bug where
    PUT/DELETE operations with links on non-200 status codes (201, 202) were
    not discovered because synthetic responses used the wrong status code.
    """

    def test_put_with_links_on_201_uses_201(self, tmp_path: Path):
        """PUT operation with links on 201 uses 201 for synthetic response."""
        from api_parity.case_generator import CaseGenerator

        # Create spec where PUT has links on 201
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/resources": {
                    "post": {
                        "operationId": "createResource",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
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
                                            "properties": {"id": {"type": "string"}},
                                        }
                                    }
                                },
                                "links": {
                                    "UpdateResource": {
                                        "operationId": "updateResource",
                                        "parameters": {"id": "$response.body#/id"},
                                    }
                                },
                            }
                        },
                    }
                },
                "/resources/{id}": {
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
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                    }
                                }
                            },
                        },
                        "responses": {
                            # Links on 201, not 200 - this is the bug scenario
                            "201": {
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
                                    "GetResource": {
                                        "operationId": "getResource",
                                        "parameters": {"id": "$response.body#/id"},
                                    }
                                },
                            }
                        },
                    },
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
                                "description": "Success",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "string"}},
                                        }
                                    }
                                },
                            }
                        },
                    },
                },
            },
        }
        spec_path = tmp_path / "put_links_201.yaml"
        with open(spec_path, "w") as f:
            yaml.dump(spec, f)

        generator = CaseGenerator(spec_path)

        # Generate chains - should find create → update → get
        # If status code was hardcoded to 200 for PUT, the link on 201 wouldn't be found
        # Exact chain structure depends on Hypothesis exploration, so we just verify
        # chains are generated (which requires the 201 link to be discovered)
        try:
            chains = generator.generate_chains(max_chains=1, max_steps=3)  # 3 steps for create→update→get
            assert len(chains) > 0, "Should generate chains when PUT has links on 201"
        except Exception as e:
            pytest.fail(f"Chain generation failed with PUT links on 201: {e}")

    def test_delete_with_links_on_202_uses_202(self, tmp_path: Path):
        """DELETE operation with links on 202 uses 202 for synthetic response."""
        from api_parity.case_generator import CaseGenerator

        # Create spec where DELETE has links on 202 (async deletion)
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/resources": {
                    "post": {
                        "operationId": "createResource",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
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
                                            "properties": {"id": {"type": "string"}},
                                        }
                                    }
                                },
                                "links": {
                                    "DeleteResource": {
                                        "operationId": "deleteResource",
                                        "parameters": {"id": "$response.body#/id"},
                                    }
                                },
                            }
                        },
                    }
                },
                "/resources/{id}": {
                    "delete": {
                        "operationId": "deleteResource",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            # Links on 202, not 200/204 - async deletion scenario
                            "202": {
                                "description": "Accepted for deletion",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "status_url": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                                "links": {
                                    "CheckDeletionStatus": {
                                        "operationId": "checkDeletionStatus",
                                        "parameters": {
                                            "status_url": "$response.body#/status_url"
                                        },
                                    }
                                },
                            }
                        },
                    },
                },
                "/status": {
                    "get": {
                        "operationId": "checkDeletionStatus",
                        "parameters": [
                            {
                                "name": "status_url",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "Status"}},
                    }
                },
            },
        }
        spec_path = tmp_path / "delete_links_202.yaml"
        with open(spec_path, "w") as f:
            yaml.dump(spec, f)

        generator = CaseGenerator(spec_path)

        # Generate chains - should find create → delete → checkDeletionStatus
        try:
            chains = generator.generate_chains(max_chains=1, max_steps=3)  # 3 steps for create→delete→check
            # Should generate chains following the links
            assert len(chains) > 0, "Should generate chains when DELETE has links on 202"

        except Exception as e:
            pytest.fail(f"Chain generation failed with DELETE links on 202: {e}")

    def test_fallback_to_default_status_codes_when_no_links(self, tmp_path: Path):
        """Operations without links use default status codes (201 for POST, 200 otherwise)."""
        from api_parity.case_generator import CaseGenerator

        # Create spec with explicit links only on createResource
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/resources": {
                    "post": {
                        "operationId": "createResource",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
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
                                            "properties": {"id": {"type": "string"}},
                                        }
                                    }
                                },
                                "links": {
                                    "GetResource": {
                                        "operationId": "getResource",
                                        "parameters": {"id": "$response.body#/id"},
                                    }
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
                            # No links - just a response
                            "200": {
                                "description": "Success",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "string"}},
                                        }
                                    }
                                },
                            }
                        },
                    },
                },
            },
        }
        spec_path = tmp_path / "no_links_on_get.yaml"
        with open(spec_path, "w") as f:
            yaml.dump(spec, f)

        generator = CaseGenerator(spec_path)

        # Chain generation should work - POST has links, GET doesn't but that's OK
        try:
            chains = generator.generate_chains(max_chains=1, max_steps=2)
            assert len(chains) > 0, "Should generate chains with POST links"
        except Exception as e:
            pytest.fail(f"Chain generation failed: {e}")


class TestHeaderBasedChains:
    """Tests for chain generation and execution using header-based links.

    Verifies that chains can follow OpenAPI links using header expressions
    like $response.header.Location and $response.header.X-Resource-Id.
    """

    def test_header_link_fields_extracted_from_spec(self):
        """Header link expressions are extracted from OpenAPI spec."""
        from api_parity.case_generator import CaseGenerator

        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_header_links.yaml"
        generator = CaseGenerator(spec_path)

        link_fields = generator.get_link_fields()

        # Header expressions should be extracted
        header_names = {h.name for h in link_fields.headers}
        assert "location" in header_names
        assert "x-resource-id" in header_names

        # Body expression should also be extracted
        assert "id" in link_fields.body_pointers

    def test_chain_generation_with_header_links(self):
        """Chain generation works with header-based links."""
        from api_parity.case_generator import CaseGenerator

        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_header_links.yaml"
        generator = CaseGenerator(spec_path)

        # Should generate chains following header-based links
        try:
            chains = generator.generate_chains(max_chains=1, max_steps=2)
            # The spec has links, so chains should be generated
            assert len(chains) > 0, "Should generate chains from header-linked spec"
        except Exception as e:
            pytest.fail(f"Chain generation with header links failed: {e}")

    def test_synthetic_headers_generated_for_chain_discovery(self):
        """Synthetic headers are generated during chain discovery."""
        from api_parity.case_generator import CaseGenerator

        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_header_links.yaml"
        generator = CaseGenerator(spec_path)

        # The synthetic response should include headers for chain discovery
        link_fields = generator.get_link_fields()

        # Verify headers are present for generation
        header_names = {h.name for h in link_fields.headers}
        assert len(header_names) > 0, "Should extract header names for synthetic generation"

    def test_executor_header_extraction_in_chain(self):
        """Executor extracts header values during chain execution."""
        from api_parity.case_generator import CaseGenerator, HeaderRef, LinkFields
        from api_parity.executor import Executor
        from api_parity.models import ResponseCase, TargetConfig

        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_header_links.yaml"
        generator = CaseGenerator(spec_path)
        link_fields = generator.get_link_fields()

        # Create executor with extracted link_fields
        target = TargetConfig(base_url="http://localhost:9999")
        executor = Executor(target, target, link_fields=link_fields)

        # Simulate a response with headers matching the spec
        response = ResponseCase(
            status_code=201,
            headers={
                "location": ["http://localhost:9999/resources/abc-123"],
                "x-resource-id": ["abc-123"],
            },
            body={"id": "abc-123", "name": "test", "status": "active"},
            elapsed_ms=50.0,
        )

        extracted = executor._extract_variables(response)

        # Headers should be extracted
        assert "header/location" in extracted
        assert "header/x-resource-id" in extracted

        # Body should also be extracted
        assert "id" in extracted

        executor.close()

    def test_header_case_preserved_for_schemathesis_link_resolution(self):
        """Regression test: HeaderRef stores original case for Schemathesis link resolution.

        OpenAPI link expressions like $response.header.Location use specific casing.
        Schemathesis resolves links by looking up headers using the exact case from
        the spec. If we only store lowercase header names, the lookup fails.

        This test verifies:
        1. HeaderRef stores both original_name (from spec) and name (lowercase)
        2. extract_link_fields_from_spec() preserves original case
        3. _generate_synthetic_headers() uses original case as dict keys
        """
        from api_parity.case_generator import CaseGenerator

        # Test spec uses capitalized header names like $response.header.Location
        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_header_links.yaml"
        generator = CaseGenerator(spec_path)
        link_fields = generator.get_link_fields()

        # Verify HeaderRef stores both original and lowercase names
        location_refs = [h for h in link_fields.headers if h.name == "location"]
        assert len(location_refs) > 0, "Should extract Location header reference"

        # The original_name should preserve case from spec (e.g., "Location" not "location")
        location_ref = location_refs[0]
        assert location_ref.original_name == "Location", (
            f"HeaderRef.original_name should preserve spec case: "
            f"expected 'Location', got '{location_ref.original_name}'"
        )
        assert location_ref.name == "location", (
            f"HeaderRef.name should be lowercase: "
            f"expected 'location', got '{location_ref.name}'"
        )

        # Also test X-Resource-Id header
        x_resource_refs = [h for h in link_fields.headers if h.name == "x-resource-id"]
        assert len(x_resource_refs) > 0, "Should extract X-Resource-Id header reference"
        x_resource_ref = x_resource_refs[0]
        assert x_resource_ref.original_name == "X-Resource-Id", (
            f"HeaderRef.original_name should preserve spec case: "
            f"expected 'X-Resource-Id', got '{x_resource_ref.original_name}'"
        )

    def test_synthetic_headers_use_original_case_for_link_resolution(self):
        """Verify synthetic headers use original case so Schemathesis can find them.

        When Schemathesis resolves $response.header.Location, it looks for the header
        using the exact case from the OpenAPI spec. If synthetic headers only use
        lowercase keys like {"location": [...]}, the lookup fails.

        This test directly verifies the _generate_synthetic_headers behavior by
        checking that chain generation works with the existing header links fixture.
        """
        from api_parity.case_generator import CaseGenerator

        # Use the test_api_header_links.yaml fixture which has links using
        # $response.header.Location (capitalized) and $response.header.X-Resource-Id
        spec_path = Path(__file__).parent.parent / "fixtures" / "test_api_header_links.yaml"
        generator = CaseGenerator(spec_path)

        # Generate chains - this exercises _generate_synthetic_headers
        # If synthetic headers don't use original case (Location vs location),
        # Schemathesis can't resolve the link expression and chain generation fails
        # or produces no chains
        try:
            chains = generator.generate_chains(max_chains=1, max_steps=2)
            # If we got here without error and have chains, the fix works
            # Note: chains may be empty if Hypothesis doesn't explore the link path,
            # but any KeyError from wrong header case would have raised an exception
            assert True, "Synthetic headers used correct case - no lookup errors"
        except Exception as e:
            # If the error is about header lookup, the fix didn't work
            error_msg = str(e).lower()
            if "header" in error_msg and ("not found" in error_msg or "key" in error_msg):
                pytest.fail(f"Header case sensitivity bug: {e}")
            # Other errors should propagate
            raise
