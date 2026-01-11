"""Integration tests for Comparator with real CEL runtime.

These tests verify the Comparator works correctly with the actual Go CEL
subprocess, testing the full comparison pipeline including predefined
expression expansion and CEL evaluation.
"""

import json
from pathlib import Path

import pytest

from api_parity.cel_evaluator import CELEvaluator
from api_parity.comparator import Comparator
from api_parity.models import (
    BodyRules,
    ComparisonLibrary,
    FieldRule,
    MismatchType,
    OperationRules,
    PresenceMode,
)
from tests.conftest import make_response


# =============================================================================
# Fixtures
# =============================================================================


PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def cel_evaluator():
    """Create a real CEL evaluator that persists across the module."""
    evaluator = CELEvaluator()
    yield evaluator
    evaluator.close()


@pytest.fixture(scope="module")
def comparison_library():
    """Load the real comparison library."""
    library_path = PROJECT_ROOT / "prototype" / "comparison-rules" / "comparison_library.json"
    with open(library_path) as f:
        data = json.load(f)
    return ComparisonLibrary.model_validate(data)


@pytest.fixture(scope="module")
def comparator(cel_evaluator, comparison_library):
    """Create a Comparator with real CEL evaluator and library."""
    return Comparator(cel_evaluator, comparison_library)


# =============================================================================
# Predefined: exact_match
# =============================================================================


class TestExactMatch:
    """Tests for exact_match predefined."""

    def test_integers_equal(self, comparator):
        """Integers that are equal pass."""
        response_a = make_response(body={"value": 42})
        response_b = make_response(body={"value": 42})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.value": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_integers_different(self, comparator):
        """Integers that differ fail."""
        response_a = make_response(body={"value": 42})
        response_b = make_response(body={"value": 43})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.value": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY

    def test_strings_equal(self, comparator):
        """Strings that are equal pass."""
        response_a = make_response(body={"name": "Alice"})
        response_b = make_response(body={"name": "Alice"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.name": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_strings_different(self, comparator):
        """Strings that differ fail."""
        response_a = make_response(body={"name": "Alice"})
        response_b = make_response(body={"name": "Bob"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.name": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_arrays_equal(self, comparator):
        """Arrays that are exactly equal pass."""
        response_a = make_response(body={"items": [1, 2, 3]})
        response_b = make_response(body={"items": [1, 2, 3]})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.items": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_arrays_different_order_fails(self, comparator):
        """Arrays with different order fail exact_match."""
        response_a = make_response(body={"items": [1, 2, 3]})
        response_b = make_response(body={"items": [3, 2, 1]})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.items": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_nulls_equal(self, comparator):
        """null == null passes."""
        response_a = make_response(body={"value": None})
        response_b = make_response(body={"value": None})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.value": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


# =============================================================================
# Predefined: ignore
# =============================================================================


class TestIgnore:
    """Tests for ignore predefined (always passes)."""

    def test_different_values_pass(self, comparator):
        """Different values pass with ignore."""
        response_a = make_response(body={"id": "abc-123"})
        response_b = make_response(body={"id": "xyz-789"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.id": FieldRule(predefined="ignore")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_different_types_pass(self, comparator):
        """Different types pass with ignore."""
        response_a = make_response(body={"value": 123})
        response_b = make_response(body={"value": "string"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.value": FieldRule(predefined="ignore")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


# =============================================================================
# Predefined: numeric_tolerance
# =============================================================================


class TestNumericTolerance:
    """Tests for numeric_tolerance predefined."""

    def test_within_tolerance(self, comparator):
        """Values within tolerance pass."""
        response_a = make_response(body={"price": 10.00})
        response_b = make_response(body={"price": 10.005})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.price": FieldRule(predefined="numeric_tolerance", tolerance=0.01)}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_outside_tolerance(self, comparator):
        """Values outside tolerance fail."""
        response_a = make_response(body={"price": 10.00})
        response_b = make_response(body={"price": 10.02})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.price": FieldRule(predefined="numeric_tolerance", tolerance=0.01)}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_exact_at_tolerance_boundary(self, comparator):
        """Values exactly at tolerance boundary pass."""
        response_a = make_response(body={"value": 100})
        response_b = make_response(body={"value": 101})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(predefined="numeric_tolerance", tolerance=1)}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_negative_values(self, comparator):
        """Works with negative values."""
        response_a = make_response(body={"temp": -5.0})
        response_b = make_response(body={"temp": -5.001})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.temp": FieldRule(predefined="numeric_tolerance", tolerance=0.01)}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


# =============================================================================
# Predefined: unordered_array
# =============================================================================


class TestUnorderedArray:
    """Tests for unordered_array predefined."""

    def test_same_elements_different_order(self, comparator):
        """Same elements in different order pass."""
        response_a = make_response(body={"tags": ["a", "b", "c"]})
        response_b = make_response(body={"tags": ["c", "a", "b"]})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.tags": FieldRule(predefined="unordered_array")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_different_elements_fail(self, comparator):
        """Different elements fail."""
        response_a = make_response(body={"tags": ["a", "b", "c"]})
        response_b = make_response(body={"tags": ["a", "b", "d"]})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.tags": FieldRule(predefined="unordered_array")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_different_sizes_fail(self, comparator):
        """Different array sizes fail."""
        response_a = make_response(body={"tags": ["a", "b"]})
        response_b = make_response(body={"tags": ["a", "b", "c"]})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.tags": FieldRule(predefined="unordered_array")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_empty_arrays(self, comparator):
        """Empty arrays pass."""
        response_a = make_response(body={"tags": []})
        response_b = make_response(body={"tags": []})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.tags": FieldRule(predefined="unordered_array")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


# =============================================================================
# Predefined: array_length
# =============================================================================


class TestArrayLength:
    """Tests for array_length predefined."""

    def test_same_length(self, comparator):
        """Arrays with same length pass."""
        response_a = make_response(body={"items": [1, 2, 3]})
        response_b = make_response(body={"items": ["a", "b", "c"]})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.items": FieldRule(predefined="array_length")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_different_length(self, comparator):
        """Arrays with different length fail."""
        response_a = make_response(body={"items": [1, 2]})
        response_b = make_response(body={"items": [1, 2, 3]})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.items": FieldRule(predefined="array_length")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False


# =============================================================================
# Predefined: string_nonempty
# =============================================================================


class TestStringNonempty:
    """Tests for string_nonempty predefined."""

    def test_both_nonempty(self, comparator):
        """Both non-empty strings pass."""
        response_a = make_response(body={"name": "Alice"})
        response_b = make_response(body={"name": "Bob"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.name": FieldRule(predefined="string_nonempty")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_one_empty_fails(self, comparator):
        """One empty string fails."""
        response_a = make_response(body={"name": "Alice"})
        response_b = make_response(body={"name": ""})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.name": FieldRule(predefined="string_nonempty")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_both_empty_fails(self, comparator):
        """Both empty strings fail."""
        response_a = make_response(body={"name": ""})
        response_b = make_response(body={"name": ""})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.name": FieldRule(predefined="string_nonempty")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False


# =============================================================================
# Predefined: both_match_regex
# =============================================================================


class TestBothMatchRegex:
    """Tests for both_match_regex predefined."""

    def test_both_match_pattern(self, comparator):
        """Both values matching pattern pass."""
        response_a = make_response(body={"id": "user-123"})
        response_b = make_response(body={"id": "user-456"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(predefined="both_match_regex", pattern="^user-\\d+$")}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_one_doesnt_match(self, comparator):
        """One value not matching pattern fails."""
        response_a = make_response(body={"id": "user-123"})
        response_b = make_response(body={"id": "admin-456"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(predefined="both_match_regex", pattern="^user-\\d+$")}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False


# =============================================================================
# Predefined: uuid_format
# =============================================================================


class TestUUIDFormat:
    """Tests for uuid_format predefined."""

    def test_both_valid_uuids(self, comparator):
        """Both valid UUIDs pass."""
        response_a = make_response(body={"id": "550e8400-e29b-41d4-a716-446655440000"})
        response_b = make_response(body={"id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.id": FieldRule(predefined="uuid_format")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_invalid_uuid_fails(self, comparator):
        """Invalid UUID fails."""
        response_a = make_response(body={"id": "550e8400-e29b-41d4-a716-446655440000"})
        response_b = make_response(body={"id": "not-a-uuid"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.id": FieldRule(predefined="uuid_format")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False


# =============================================================================
# Predefined: type_match
# =============================================================================


class TestTypeMatch:
    """Tests for type_match predefined."""

    def test_same_types(self, comparator):
        """Same types pass."""
        response_a = make_response(body={"value": 123})
        response_b = make_response(body={"value": 456})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.value": FieldRule(predefined="type_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_different_types(self, comparator):
        """Different types fail."""
        response_a = make_response(body={"value": 123})
        response_b = make_response(body={"value": "123"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.value": FieldRule(predefined="type_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False


# =============================================================================
# Predefined: both_positive
# =============================================================================


class TestBothPositive:
    """Tests for both_positive predefined."""

    def test_both_positive(self, comparator):
        """Both positive values pass."""
        response_a = make_response(body={"count": 5})
        response_b = make_response(body={"count": 100})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.count": FieldRule(predefined="both_positive")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_one_negative(self, comparator):
        """One negative value fails."""
        response_a = make_response(body={"count": 5})
        response_b = make_response(body={"count": -1})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.count": FieldRule(predefined="both_positive")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_zero_fails(self, comparator):
        """Zero is not positive."""
        response_a = make_response(body={"count": 0})
        response_b = make_response(body={"count": 5})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.count": FieldRule(predefined="both_positive")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False


# =============================================================================
# Predefined: both_in_range
# =============================================================================


class TestBothInRange:
    """Tests for both_in_range predefined."""

    def test_both_in_range(self, comparator):
        """Both values in range pass."""
        response_a = make_response(body={"score": 0.5})
        response_b = make_response(body={"score": 0.8})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.score": FieldRule(predefined="both_in_range", min=0.0, max=1.0)}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_one_out_of_range(self, comparator):
        """One value out of range fails."""
        response_a = make_response(body={"score": 0.5})
        response_b = make_response(body={"score": 1.5})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.score": FieldRule(predefined="both_in_range", min=0.0, max=1.0)}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_at_boundaries(self, comparator):
        """Values at boundaries pass (inclusive)."""
        response_a = make_response(body={"value": 0})
        response_b = make_response(body={"value": 100})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(predefined="both_in_range", min=0, max=100)}
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


# =============================================================================
# Custom CEL Expressions
# =============================================================================


class TestCustomCELExpressions:
    """Tests for custom CEL expressions."""

    def test_less_than(self, comparator):
        """Custom a < b expression."""
        response_a = make_response(body={"value": 10})
        response_b = make_response(body={"value": 20})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.value": FieldRule(expr="a < b")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_string_length_comparison(self, comparator):
        """Custom expression comparing string lengths."""
        response_a = make_response(body={"name": "Alice"})
        response_b = make_response(body={"name": "Bob"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.name": FieldRule(expr="size(a) >= size(b)")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True  # len("Alice") >= len("Bob")

    def test_complex_expression(self, comparator):
        """Complex custom expression."""
        response_a = make_response(body={"values": [1, 2, 3, 4, 5]})
        response_b = make_response(body={"values": [5, 4, 3, 2, 1]})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.values": FieldRule(
                        expr="size(a) == size(b) && a[0] + a[size(a)-1] == b[0] + b[size(b)-1]"
                    )
                }
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        # 1+5 == 5+1
        assert result.match is True


# =============================================================================
# Wildcard Path Integration Tests
# =============================================================================


class TestWildcardPathsIntegration:
    """Integration tests for wildcard JSONPath handling."""

    def test_wildcard_all_elements_match(self, comparator):
        """All wildcard-matched elements pass."""
        response_a = make_response(
            body={"users": [{"id": 1}, {"id": 2}, {"id": 3}]}
        )
        response_b = make_response(
            body={"users": [{"id": 1}, {"id": 2}, {"id": 3}]}
        )
        rules = OperationRules(
            body=BodyRules(field_rules={"$.users[*].id": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_wildcard_one_element_differs(self, comparator):
        """One differing element in wildcard path fails."""
        response_a = make_response(
            body={"users": [{"id": 1}, {"id": 2}, {"id": 3}]}
        )
        response_b = make_response(
            body={"users": [{"id": 1}, {"id": 99}, {"id": 3}]}
        )
        rules = OperationRules(
            body=BodyRules(field_rules={"$.users[*].id": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        # Should report which element failed (jsonpath-ng uses "users.[1].id" format)
        diff = result.details["body"].differences[0]
        assert "[1]" in diff.path and "id" in diff.path

    def test_wildcard_with_tolerance(self, comparator):
        """Wildcard elements compared with tolerance."""
        response_a = make_response(
            body={"prices": [{"value": 10.00}, {"value": 20.00}]}
        )
        response_b = make_response(
            body={"prices": [{"value": 10.005}, {"value": 20.001}]}
        )
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.prices[*].value": FieldRule(predefined="numeric_tolerance", tolerance=0.01)
                }
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


# =============================================================================
# Full Response Comparison Tests
# =============================================================================


class TestFullResponseComparison:
    """Tests for complete response comparison flows."""

    def test_full_match(self, comparator):
        """Complete matching responses pass."""
        response_a = make_response(
            status_code=200,
            headers={"content-type": ["application/json"]},
            body={
                "id": "user-123",
                "name": "Alice",
                "score": 95.5,
                "tags": ["admin", "active"],
            },
        )
        response_b = make_response(
            status_code=200,
            headers={"content-type": ["application/json"]},
            body={
                "id": "user-456",  # Different but ignored
                "name": "Alice",
                "score": 95.6,  # Within tolerance
                "tags": ["active", "admin"],  # Different order
            },
        )
        rules = OperationRules(
            headers={"content-type": FieldRule(predefined="exact_match")},
            body=BodyRules(
                field_rules={
                    "$.id": FieldRule(predefined="both_match_regex", pattern="^user-\\d+$"),
                    "$.name": FieldRule(predefined="exact_match"),
                    "$.score": FieldRule(predefined="numeric_tolerance", tolerance=0.5),
                    "$.tags": FieldRule(predefined="unordered_array"),
                }
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True
        assert result.summary == "Responses match"

    def test_status_mismatch_short_circuits(self, comparator):
        """Status code mismatch stops further comparison."""
        response_a = make_response(status_code=200, body={"id": 1})
        response_b = make_response(status_code=404, body={"error": "not found"})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.id": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.STATUS_CODE
        # Body comparison result still exists but wasn't the cause
        assert "status_code" in result.details

    def test_multiple_rules_all_checked(self, comparator):
        """Multiple rules are all evaluated."""
        response_a = make_response(
            body={
                "id": 1,
                "name": "Test",
                "active": True,
                "score": 100,
            }
        )
        response_b = make_response(
            body={
                "id": 1,
                "name": "Test",
                "active": True,
                "score": 100,
            }
        )
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.id": FieldRule(predefined="exact_match"),
                    "$.name": FieldRule(predefined="exact_match"),
                    "$.active": FieldRule(predefined="exact_match"),
                    "$.score": FieldRule(predefined="both_positive"),
                }
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


# =============================================================================
# Presence Mode Integration Tests
# =============================================================================


class TestPresenceModesIntegration:
    """Integration tests for presence modes with CEL."""

    def test_optional_with_comparison(self, comparator):
        """OPTIONAL field present in both - comparison applies."""
        response_a = make_response(body={"nickname": "Al"})
        response_b = make_response(body={"nickname": "Al"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.nickname": FieldRule(
                        presence=PresenceMode.OPTIONAL, predefined="exact_match"
                    )
                }
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_required_field_with_tolerance(self, comparator):
        """REQUIRED field with tolerance comparison."""
        response_a = make_response(body={"timestamp": 1000})
        response_b = make_response(body={"timestamp": 1002})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.timestamp": FieldRule(
                        presence=PresenceMode.REQUIRED,
                        predefined="numeric_tolerance",
                        tolerance=5,
                    )
                }
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCasesIntegration:
    """Integration tests for edge cases."""

    def test_deeply_nested_wildcard(self, comparator):
        """Deeply nested wildcard path works."""
        response_a = make_response(
            body={
                "data": {
                    "users": [
                        {"profile": {"settings": {"theme": "dark"}}},
                        {"profile": {"settings": {"theme": "light"}}},
                    ]
                }
            }
        )
        response_b = make_response(
            body={
                "data": {
                    "users": [
                        {"profile": {"settings": {"theme": "dark"}}},
                        {"profile": {"settings": {"theme": "light"}}},
                    ]
                }
            }
        )
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.data.users[*].profile.settings.theme": FieldRule(predefined="exact_match")
                }
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_unicode_in_regex(self, comparator):
        """Unicode characters in regex pattern."""
        response_a = make_response(body={"greeting": "こんにちは世界"})
        response_b = make_response(body={"greeting": "こんにちは友人"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.greeting": FieldRule(predefined="both_match_regex", pattern="^こんにちは")
                }
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_large_numeric_values(self, comparator):
        """Large numeric values compare correctly."""
        response_a = make_response(body={"big": 9999999999999999})
        response_b = make_response(body={"big": 9999999999999999})
        rules = OperationRules(
            body=BodyRules(field_rules={"$.big": FieldRule(predefined="exact_match")})
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True

    def test_floating_point_precision(self, comparator):
        """Floating point precision handled with tolerance."""
        response_a = make_response(body={"value": 0.1 + 0.2})  # 0.30000000000000004
        response_b = make_response(body={"value": 0.3})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.value": FieldRule(predefined="numeric_tolerance", tolerance=0.0001)
                }
            )
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
