"""Tests for Comparator integration with schema validation.

Tests the OpenAPI Spec as Field Authority feature in the Comparator:
- Phase 0: Schema validation before comparison
- Extra fields comparison for flexible schemas
- SCHEMA_VIOLATION mismatch type
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api_parity.comparator import Comparator
from api_parity.models import (
    ComparisonLibrary,
    MismatchType,
    OperationRules,
    PredefinedComparison,
    ResponseCase,
)
from api_parity.schema_validator import SchemaValidator

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMA_VALIDATION_SPEC = FIXTURES_DIR / "test_api_schema_validation.yaml"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_cel():
    """Mock CEL evaluator."""
    cel = MagicMock()
    cel.evaluate.return_value = True
    return cel


@pytest.fixture
def comparison_library():
    """Minimal comparison library."""
    return ComparisonLibrary(
        library_version="1",
        description="Test library",
        predefined={
            "exact_match": PredefinedComparison(
                description="Exact equality",
                params=[],
                expr="a == b",
            ),
        },
    )


@pytest.fixture
def schema_validator():
    """Schema validator with test spec."""
    return SchemaValidator(SCHEMA_VALIDATION_SPEC)


@pytest.fixture
def comparator(mock_cel, comparison_library, schema_validator):
    """Comparator with schema validation enabled."""
    return Comparator(mock_cel, comparison_library, schema_validator)


@pytest.fixture
def comparator_no_schema(mock_cel, comparison_library):
    """Comparator without schema validation."""
    return Comparator(mock_cel, comparison_library)


def make_response(status_code=200, body=None, headers=None):
    """Helper to create ResponseCase for testing."""
    return ResponseCase(
        status_code=status_code,
        headers=headers or {},
        body=body,
        elapsed_ms=10.0,
    )


# =============================================================================
# Schema Validation Integration Tests
# =============================================================================


class TestSchemaValidationPhase:
    """Tests for schema validation phase in comparison."""

    def test_valid_responses_pass_schema_phase(self, comparator):
        """Valid responses pass schema validation phase."""
        body_a = {"widgets": [{"id": "1", "name": "W", "price": 10.0}], "total": 1}
        body_b = {"widgets": [{"id": "2", "name": "X", "price": 20.0}], "total": 1}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules, "listStrictWidgets")

        # Should not have schema violations
        assert result.mismatch_type != MismatchType.SCHEMA_VIOLATION

    def test_schema_violation_in_response_a(self, comparator):
        """Schema violation in response A returns SCHEMA_VIOLATION mismatch."""
        body_a = {"widgets": [], "total": 0, "extra": "bad"}  # Extra field
        body_b = {"widgets": [], "total": 0}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules, "listStrictWidgets")

        assert result.match is False
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION
        assert "schema" in result.details

    def test_schema_violation_in_response_b(self, comparator):
        """Schema violation in response B returns SCHEMA_VIOLATION mismatch."""
        body_a = {"widgets": [], "total": 0}
        body_b = {"widgets": [], "total": 0, "extra": "bad"}  # Extra field

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules, "listStrictWidgets")

        assert result.match is False
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION

    def test_both_responses_have_violations(self, comparator):
        """Both responses with violations report all violations."""
        body_a = {"widgets": [], "total": 0, "extra_a": "bad"}
        body_b = {"widgets": [], "total": 0, "extra_b": "also bad"}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules, "listStrictWidgets")

        assert result.match is False
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION
        # Should have violations from both responses
        schema_details = result.details.get("schema")
        assert schema_details is not None
        assert len(schema_details.differences) >= 2


class TestSchemaValidationSkipping:
    """Tests for when schema validation is skipped."""

    def test_no_schema_validator_skips_validation(self, comparator_no_schema):
        """Comparison without schema_validator skips schema phase."""
        body = {"anything": "goes"}

        response_a = make_response(body=body)
        response_b = make_response(body=body)
        rules = OperationRules()

        result = comparator_no_schema.compare(
            response_a, response_b, rules, "listStrictWidgets"
        )

        # Should not have schema key in details
        assert "schema" not in result.details

    def test_no_operation_id_skips_validation(self, comparator):
        """Comparison without operation_id skips schema phase."""
        body = {"extra": "allowed when not validated"}

        response_a = make_response(body=body)
        response_b = make_response(body=body)
        rules = OperationRules()

        # No operation_id passed
        result = comparator.compare(response_a, response_b, rules)

        # Should not have schema key in details
        assert "schema" not in result.details

    def test_unknown_operation_passes_validation(self, comparator):
        """Unknown operation (no schema) passes schema validation."""
        body = {"any_field": "value"}

        response_a = make_response(body=body)
        response_b = make_response(body=body)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "unknownOperation"
        )

        # Should pass (no schema to validate against)
        assert result.mismatch_type != MismatchType.SCHEMA_VIOLATION


class TestExtraFieldsComparison:
    """Tests for extra fields comparison in flexible schemas."""

    def test_extra_fields_compared_when_allowed(self, comparator):
        """Extra fields in flexible schemas are compared for equality."""
        body_a = {"widgets": [{"id": "1", "name": "W"}], "extra": "value_a"}
        body_b = {"widgets": [{"id": "1", "name": "W"}], "extra": "value_b"}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listFlexibleWidgets"
        )

        # Should mismatch due to extra field difference
        assert result.match is False
        # Should have extra_fields in details
        assert "extra_fields" in result.details

    def test_same_extra_fields_match(self, comparator):
        """Same extra field values in flexible schemas match."""
        body_a = {"widgets": [{"id": "1", "name": "W"}], "extra": "same"}
        body_b = {"widgets": [{"id": "1", "name": "W"}], "extra": "same"}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listFlexibleWidgets"
        )

        # Extra fields with same values should not cause mismatch
        if "extra_fields" in result.details:
            assert result.details["extra_fields"].match is True

    def test_extra_field_missing_in_one_response(self, comparator):
        """Extra field present in one response but not other is mismatch."""
        body_a = {"widgets": [{"id": "1", "name": "W"}], "extra": "exists"}
        body_b = {"widgets": [{"id": "1", "name": "W"}]}  # No extra field

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listFlexibleWidgets"
        )

        # Should mismatch due to presence difference
        assert result.match is False
        assert "extra_fields" in result.details


class TestSchemaValidationPrecedence:
    """Tests that schema validation happens before other comparisons."""

    def test_schema_violation_before_status_code(self, comparator):
        """Schema violation reported before status code comparison."""
        body_a = {"widgets": [], "total": 0, "extra": "bad"}
        body_b = {"widgets": [], "total": 0}

        response_a = make_response(status_code=200, body=body_a)
        response_b = make_response(status_code=201, body=body_b)  # Different status
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listStrictWidgets"
        )

        # Schema violation should be detected first
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION

    def test_schema_violation_before_body_comparison(self, comparator):
        """Schema violation reported before body comparison."""
        body_a = {"widgets": [{"id": "1", "name": "A", "price": 10.0}], "total": 1, "extra": "bad"}
        body_b = {"widgets": [{"id": "2", "name": "B", "price": 20.0}], "total": 1}  # Different values

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listStrictWidgets"
        )

        # Schema violation should be detected first
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION


class TestMismatchTypeSerialization:
    """Tests that SCHEMA_VIOLATION mismatch type serializes correctly."""

    def test_schema_violation_enum_value(self):
        """SCHEMA_VIOLATION has correct string value."""
        assert MismatchType.SCHEMA_VIOLATION.value == "schema_violation"

    def test_schema_violation_in_result_json(self, comparator):
        """SCHEMA_VIOLATION mismatch type appears in result JSON."""
        body_a = {"widgets": [], "total": 0, "extra": "bad"}
        body_b = {"widgets": [], "total": 0}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listStrictWidgets"
        )

        result_dict = result.model_dump()
        assert result_dict["mismatch_type"] == "schema_violation"
