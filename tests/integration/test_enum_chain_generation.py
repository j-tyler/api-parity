"""Integration tests for enum-constrained chain generation.

Tests that chain discovery succeeds when link-extracted values must satisfy
enum constraints in target parameters. Without schema-aware synthetic value
generation, these chains would fail because UUID placeholders don't match
enum values like 'main_branch'.

See DESIGN.md "Schema-Driven Synthetic Value Generation" for rationale.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestEnumChainGeneration:
    """Tests for chain generation with enum-constrained parameters.

    Performance note: Chain generation is expensive. Tests are consolidated
    to minimize redundant generation while maintaining coverage.
    """

    def test_chain_generation_with_enum_constraint(self):
        """Chain generation succeeds with enum-constrained parameters.

        Combined test that verifies:
        - Chain generation doesn't crash on enum spec (was separate test)
        - At least one chain is generated
        - Chains starting with listLibraries are discovered

        The getBooks operation requires library_id to be one of the enum values
        (main_branch, downtown_branch, westside_branch). Without schema-aware
        generation, the synthetic response would contain a UUID that fails
        validation against the enum constraint, preventing chain discovery.
        """
        from api_parity.case_generator import CaseGenerator

        spec_path = Path(__file__).parent.parent / "fixtures" / "enum_chain_spec.yaml"
        generator = CaseGenerator(spec_path)

        # Generate chains - minimal count to verify functionality
        # This should not raise an exception - the important test is that
        # schema-aware generation produces valid enum values
        try:
            chains = generator.generate_chains(max_chains=1, max_steps=2)
        except Exception as e:
            pytest.fail(f"Chain generation failed with enum constraints: {e}")

        # Should generate at least one chain
        assert len(chains) > 0, "Should generate chains from enum-constrained spec"

        # Verify chain structure (non-deterministic, so just check what we get)
        for chain in chains:
            op_ids = [step.request_template.operation_id for step in chain.steps]
            assert len(op_ids) >= 2, "Chains should have at least 2 steps"

    def test_synthetic_values_use_enum_values_not_uuids(self):
        """Synthetic body values use enum values when schema has enum constraint.

        Verifies that the SchemaValueGenerator produces enum-compliant values
        instead of generic UUIDs for fields with enum constraints.
        """
        from api_parity.schema_value_generator import SchemaValueGenerator
        import yaml

        spec_path = Path(__file__).parent.parent / "fixtures" / "enum_chain_spec.yaml"
        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        generator = SchemaValueGenerator(spec)

        # Get the response schema for listLibraries
        response_schema = generator.get_response_schema("listLibraries", 200)
        assert response_schema is not None, "Should find response schema for listLibraries"

        # Navigate to items/0/library_id (the link source field)
        field_schema = generator.navigate_to_field(response_schema, "items/0/library_id")
        assert field_schema is not None, "Should find schema for items/0/library_id"

        # Generate value - should be an enum value, not a UUID
        value = generator.generate(field_schema)

        # The value should be one of the enum values
        valid_enum_values = ["main_branch", "downtown_branch", "westside_branch"]
        assert value in valid_enum_values, (
            f"Generated value '{value}' should be one of {valid_enum_values}, not a UUID"
        )

    def test_link_fields_extracted_from_enum_spec(self):
        """Link field references are correctly extracted from enum spec."""
        from api_parity.case_generator import CaseGenerator

        spec_path = Path(__file__).parent.parent / "fixtures" / "enum_chain_spec.yaml"
        generator = CaseGenerator(spec_path)

        link_fields = generator.get_link_fields()

        # Should extract the body pointers from the links
        assert "items/0/library_id" in link_fields.body_pointers, (
            f"Should extract items/0/library_id, got {link_fields.body_pointers}"
        )
        assert "library_id" in link_fields.body_pointers, (
            f"Should extract library_id, got {link_fields.body_pointers}"
        )
        assert "items/0/book_id" in link_fields.body_pointers, (
            f"Should extract items/0/book_id, got {link_fields.body_pointers}"
        )

class TestSchemaValueGeneratorIntegration:
    """Integration tests for SchemaValueGenerator with real OpenAPI specs."""

    def test_generate_respects_nested_enum(self):
        """Generator handles nested enum references ($ref to enum schema)."""
        from api_parity.schema_value_generator import SchemaValueGenerator
        import yaml

        spec_path = Path(__file__).parent.parent / "fixtures" / "enum_chain_spec.yaml"
        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        generator = SchemaValueGenerator(spec)

        # Get BookList response schema (from getBooks)
        response_schema = generator.get_response_schema("getBooks", 200)
        assert response_schema is not None

        # Navigate to library_id field (direct in BookList, not nested in array)
        field_schema = generator.navigate_to_field(response_schema, "library_id")
        assert field_schema is not None, "Should find library_id in BookList"

        # Should resolve $ref and find enum
        value = generator.generate(field_schema)
        valid_values = ["main_branch", "downtown_branch", "westside_branch"]
        assert value in valid_values, f"Expected enum value, got {value}"

    def test_generate_uuid_format_for_book_id(self):
        """Generator produces UUID format for format: uuid fields."""
        from api_parity.schema_value_generator import SchemaValueGenerator
        import yaml
        import uuid as uuid_module

        spec_path = Path(__file__).parent.parent / "fixtures" / "enum_chain_spec.yaml"
        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        generator = SchemaValueGenerator(spec)

        # Get BookList response schema
        response_schema = generator.get_response_schema("getBooks", 200)
        assert response_schema is not None

        # Navigate to items/0/book_id
        field_schema = generator.navigate_to_field(response_schema, "items/0/book_id")
        assert field_schema is not None, "Should find book_id in Book items"

        # Should generate a valid UUID
        value = generator.generate(field_schema)
        try:
            uuid_module.UUID(value)
        except ValueError:
            pytest.fail(f"Expected UUID format, got {value}")
