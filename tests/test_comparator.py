"""Unit tests for the Comparator.

These tests mock the CELEvaluator to test the Comparator logic in isolation.
Integration tests with the real CEL runtime are in tests/integration/test_comparator_cel.py.
"""

import pytest
from unittest.mock import MagicMock

from api_parity.comparator import (
    Comparator,
    ComparatorConfigError,
    JSONPathError,
    NOT_FOUND,
    PresenceResult,
    _NotFound,
)
from api_parity.models import (
    BodyRules,
    ComparisonLibrary,
    FieldRule,
    MismatchType,
    OperationRules,
    PredefinedComparison,
    PresenceMode,
)
from tests.conftest import make_response


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_cel():
    """Create a mock CEL evaluator."""
    cel = MagicMock()
    cel.evaluate = MagicMock(return_value=True)
    return cel


@pytest.fixture
def comparison_library():
    """Create a minimal comparison library for testing."""
    return ComparisonLibrary(
        library_version="1",
        description="Test library",
        predefined={
            "exact_match": PredefinedComparison(
                description="Exact equality",
                params=[],
                expr="a == b",
            ),
            "ignore": PredefinedComparison(
                description="Always passes",
                params=[],
                expr="true",
            ),
            "numeric_tolerance": PredefinedComparison(
                description="Within tolerance",
                params=["tolerance"],
                expr="(a - b) <= tolerance && (b - a) <= tolerance",
            ),
            "both_match_regex": PredefinedComparison(
                description="Both match pattern",
                params=["pattern"],
                expr="a.matches(pattern) && b.matches(pattern)",
            ),
            "string_prefix": PredefinedComparison(
                description="First N chars match",
                params=["length"],
                expr="a.substring(0, length) == b.substring(0, length)",
            ),
        },
    )


@pytest.fixture
def comparator(mock_cel, comparison_library):
    """Create a Comparator with mocked CEL evaluator."""
    return Comparator(mock_cel, comparison_library)


# =============================================================================
# NOT_FOUND Sentinel Tests
# =============================================================================


class TestNotFoundSentinel:
    """Tests for the NOT_FOUND sentinel."""

    def test_singleton(self):
        """NOT_FOUND is a singleton."""
        sentinel1 = _NotFound()
        sentinel2 = _NotFound()
        assert sentinel1 is sentinel2
        assert sentinel1 is NOT_FOUND

    def test_distinct_from_none(self):
        """NOT_FOUND is distinct from None."""
        assert NOT_FOUND is not None
        assert NOT_FOUND != None  # noqa: E711

    def test_repr(self):
        """NOT_FOUND has a useful repr."""
        assert repr(NOT_FOUND) == "<NOT_FOUND>"


# =============================================================================
# Status Code Comparison Tests
# =============================================================================


class TestStatusCodeComparison:
    """Tests for status code comparison."""

    def test_exact_match_no_rule(self, comparator):
        """Default behavior is exact match."""
        response_a = make_response(status_code=200)
        response_b = make_response(status_code=200)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True
        assert result.details["status_code"].match is True

    def test_mismatch_no_rule(self, comparator):
        """Different status codes fail with default rules."""
        response_a = make_response(status_code=200)
        response_b = make_response(status_code=201)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.STATUS_CODE
        assert "200 vs 201" in result.summary

    def test_with_custom_rule(self, comparator, mock_cel):
        """Status code comparison uses custom rule when provided."""
        response_a = make_response(status_code=200)
        response_b = make_response(status_code=201)
        rules = OperationRules(
            status_code=FieldRule(predefined="ignore"),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True
        mock_cel.evaluate.assert_called()

    def test_rule_returns_false(self, comparator, mock_cel):
        """Status code mismatch when rule returns False."""
        response_a = make_response(status_code=200)
        response_b = make_response(status_code=201)
        rules = OperationRules(
            status_code=FieldRule(predefined="exact_match"),
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.STATUS_CODE


# =============================================================================
# Header Comparison Tests
# =============================================================================


class TestHeaderComparison:
    """Tests for header comparison."""

    def test_no_header_rules(self, comparator):
        """No header rules means headers pass."""
        response_a = make_response(headers={"content-type": ["application/json"]})
        response_b = make_response(headers={"content-type": ["text/plain"]})
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["headers"].match is True

    def test_header_exact_match(self, comparator, mock_cel):
        """Headers match when values are equal."""
        response_a = make_response(headers={"content-type": ["application/json"]})
        response_b = make_response(headers={"content-type": ["application/json"]})
        rules = OperationRules(
            headers={"content-type": FieldRule(predefined="exact_match")},
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["headers"].match is True

    def test_header_mismatch(self, comparator, mock_cel):
        """Headers don't match when values differ."""
        response_a = make_response(headers={"content-type": ["application/json"]})
        response_b = make_response(headers={"content-type": ["text/plain"]})
        rules = OperationRules(
            headers={"content-type": FieldRule(predefined="exact_match")},
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.HEADERS

    def test_header_case_insensitive(self, comparator, mock_cel):
        """Header names are case-insensitive."""
        response_a = make_response(headers={"Content-Type": ["application/json"]})
        response_b = make_response(headers={"content-type": ["application/json"]})
        rules = OperationRules(
            headers={"CONTENT-TYPE": FieldRule(predefined="exact_match")},
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["headers"].match is True

    def test_header_multi_value_uses_first(self, comparator, mock_cel):
        """Multi-value headers use only the first value."""
        response_a = make_response(headers={"x-custom": ["first", "second"]})
        response_b = make_response(headers={"x-custom": ["first", "different"]})
        rules = OperationRules(
            headers={"x-custom": FieldRule(predefined="exact_match")},
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        # Should compare "first" vs "first"
        mock_cel.evaluate.assert_called_with("a == b", {"a": "first", "b": "first"})


# =============================================================================
# Presence Mode Tests
# =============================================================================


class TestPresenceModes:
    """Tests for field presence checking."""

    def test_parity_both_present(self, comparator, mock_cel):
        """PARITY: both present passes."""
        response_a = make_response(body={"name": "Alice"})
        response_b = make_response(body={"name": "Bob"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.name": FieldRule(presence=PresenceMode.PARITY, predefined="ignore")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_parity_both_absent(self, comparator):
        """PARITY: both absent passes."""
        response_a = make_response(body={"foo": 1})
        response_b = make_response(body={"bar": 2})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.name": FieldRule(presence=PresenceMode.PARITY)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_parity_one_present_fails(self, comparator):
        """PARITY: one present, one absent fails."""
        response_a = make_response(body={"name": "Alice"})
        response_b = make_response(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.name": FieldRule(presence=PresenceMode.PARITY)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY
        assert "presence:parity" in result.details["body"].differences[0].rule

    def test_required_both_present(self, comparator, mock_cel):
        """REQUIRED: both present passes."""
        response_a = make_response(body={"id": 1})
        response_b = make_response(body={"id": 2})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(presence=PresenceMode.REQUIRED, predefined="ignore")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_required_one_absent_fails(self, comparator):
        """REQUIRED: one absent fails."""
        response_a = make_response(body={"id": 1})
        response_b = make_response(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(presence=PresenceMode.REQUIRED)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "presence:required" in result.details["body"].differences[0].rule

    def test_required_both_absent_fails(self, comparator):
        """REQUIRED: both absent fails."""
        response_a = make_response(body={})
        response_b = make_response(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(presence=PresenceMode.REQUIRED)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "presence:required" in result.details["body"].differences[0].rule

    def test_forbidden_both_absent(self, comparator):
        """FORBIDDEN: both absent passes."""
        response_a = make_response(body={"foo": 1})
        response_b = make_response(body={"bar": 2})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.secret": FieldRule(presence=PresenceMode.FORBIDDEN)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_forbidden_one_present_fails(self, comparator):
        """FORBIDDEN: one present fails."""
        response_a = make_response(body={"secret": "value"})
        response_b = make_response(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.secret": FieldRule(presence=PresenceMode.FORBIDDEN)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "presence:forbidden" in result.details["body"].differences[0].rule

    def test_forbidden_both_present_fails(self, comparator):
        """FORBIDDEN: both present fails."""
        response_a = make_response(body={"secret": "a"})
        response_b = make_response(body={"secret": "b"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.secret": FieldRule(presence=PresenceMode.FORBIDDEN)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_optional_both_present(self, comparator, mock_cel):
        """OPTIONAL: both present, compares values."""
        response_a = make_response(body={"nickname": "Al"})
        response_b = make_response(body={"nickname": "Bob"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.nickname": FieldRule(presence=PresenceMode.OPTIONAL, predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = False  # Values differ

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False  # Value comparison failed

    def test_optional_one_absent(self, comparator):
        """OPTIONAL: one absent passes without value comparison."""
        response_a = make_response(body={"nickname": "Al"})
        response_b = make_response(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.nickname": FieldRule(presence=PresenceMode.OPTIONAL, predefined="exact_match")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True  # Optional allows missing

    def test_optional_both_absent(self, comparator):
        """OPTIONAL: both absent passes."""
        response_a = make_response(body={})
        response_b = make_response(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.nickname": FieldRule(presence=PresenceMode.OPTIONAL, predefined="exact_match")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_presence_only_rule(self, comparator):
        """Presence-only rule (no predefined/expr) only checks presence."""
        response_a = make_response(body={"id": 1})
        response_b = make_response(body={"id": 999})  # Different value
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(presence=PresenceMode.PARITY)}  # No predefined/expr
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        # Should pass because we only check presence, not values
        assert result.details["body"].match is True


# =============================================================================
# Body Comparison Tests
# =============================================================================


class TestBodyComparison:
    """Tests for body comparison."""

    def test_both_bodies_none(self, comparator):
        """Both None bodies match."""
        response_a = make_response(body=None)
        response_b = make_response(body=None)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_one_body_none_mismatch(self, comparator):
        """One body None, one not is a mismatch."""
        response_a = make_response(body={"id": 1})
        response_b = make_response(body=None)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY
        assert "body_presence" in result.details["body"].differences[0].rule

    def test_no_body_rules(self, comparator):
        """No body rules means body passes."""
        response_a = make_response(body={"different": 1})
        response_b = make_response(body={"values": 2})
        rules = OperationRules()  # No body rules

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_simple_field_match(self, comparator, mock_cel):
        """Simple field comparison works."""
        response_a = make_response(body={"name": "Alice"})
        response_b = make_response(body={"name": "Alice"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.name": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True
        mock_cel.evaluate.assert_called_with("a == b", {"a": "Alice", "b": "Alice"})

    def test_nested_field_path(self, comparator, mock_cel):
        """Nested JSONPath works."""
        response_a = make_response(body={"user": {"profile": {"name": "Alice"}}})
        response_b = make_response(body={"user": {"profile": {"name": "Alice"}}})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.user.profile.name": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_array_index_path(self, comparator, mock_cel):
        """Array index path works."""
        response_a = make_response(body={"items": [{"id": 1}, {"id": 2}]})
        response_b = make_response(body={"items": [{"id": 1}, {"id": 2}]})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.items[0].id": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_multiple_field_rules(self, comparator, mock_cel):
        """Multiple field rules all checked."""
        response_a = make_response(body={"id": 1, "name": "Alice", "age": 30})
        response_b = make_response(body={"id": 1, "name": "Alice", "age": 30})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.id": FieldRule(predefined="exact_match"),
                    "$.name": FieldRule(predefined="exact_match"),
                    "$.age": FieldRule(predefined="exact_match"),
                }
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True
        assert mock_cel.evaluate.call_count == 3

    def test_one_field_mismatch(self, comparator, mock_cel):
        """One field mismatch causes overall mismatch."""
        response_a = make_response(body={"id": 1, "name": "Alice"})
        response_b = make_response(body={"id": 1, "name": "Bob"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.id": FieldRule(predefined="exact_match"),
                    "$.name": FieldRule(predefined="exact_match"),
                }
            ),
        )
        # First call (id) returns True, second (name) returns False
        mock_cel.evaluate.side_effect = [True, False]

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY
        # Should have one difference for name
        assert len(result.details["body"].differences) == 1
        assert result.details["body"].differences[0].path == "$.name"


# =============================================================================
# Wildcard Path Tests
# =============================================================================


class TestWildcardPaths:
    """Tests for JSONPath wildcard handling."""

    def test_wildcard_same_length(self, comparator, mock_cel):
        """Wildcard paths with same array length compare elements."""
        response_a = make_response(body={"items": [{"id": 1}, {"id": 2}]})
        response_b = make_response(body={"items": [{"id": 1}, {"id": 2}]})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.items[*].id": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True
        # Should be called twice (once per element)
        assert mock_cel.evaluate.call_count == 2

    def test_wildcard_different_length(self, comparator):
        """Wildcard paths with different array lengths mismatch."""
        response_a = make_response(body={"items": [{"id": 1}, {"id": 2}]})
        response_b = make_response(body={"items": [{"id": 1}]})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.items[*].id": FieldRule(predefined="exact_match")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "wildcard_count_mismatch" in result.details["body"].differences[0].rule

    def test_wildcard_element_mismatch(self, comparator, mock_cel):
        """Wildcard paths report which element mismatched."""
        response_a = make_response(body={"items": [{"id": 1}, {"id": 2}]})
        response_b = make_response(body={"items": [{"id": 1}, {"id": 99}]})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.items[*].id": FieldRule(predefined="exact_match")}
            ),
        )
        # First element matches, second doesn't
        mock_cel.evaluate.side_effect = [True, False]

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        # Should report the concrete path, not the wildcard
        diff = result.details["body"].differences[0]
        # jsonpath-ng uses "items.[1].id" format
        assert "[1]" in diff.path and "id" in diff.path

    def test_empty_arrays_match(self, comparator):
        """Empty arrays in both responses match."""
        response_a = make_response(body={"items": []})
        response_b = make_response(body={"items": []})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.items[*].id": FieldRule(predefined="exact_match")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        # Both have 0 matches, which is equal
        assert result.details["body"].match is True


# =============================================================================
# Predefined Expansion Tests
# =============================================================================


class TestPredefinedExpansion:
    """Tests for predefined rule expansion."""

    def test_no_params(self, comparator, mock_cel):
        """Predefined with no params expands correctly."""
        response_a = make_response(body={"value": 1})
        response_b = make_response(body={"value": 1})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        comparator.compare(response_a, response_b, rules)

        mock_cel.evaluate.assert_called_with("a == b", {"a": 1, "b": 1})

    def test_numeric_param(self, comparator, mock_cel):
        """Numeric parameter substituted correctly."""
        response_a = make_response(body={"value": 1.0})
        response_b = make_response(body={"value": 1.005})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(predefined="numeric_tolerance", tolerance=0.01)}
            ),
        )
        mock_cel.evaluate.return_value = True

        comparator.compare(response_a, response_b, rules)

        # Check the expression has the substituted value
        call_args = mock_cel.evaluate.call_args
        expr = call_args[0][0]
        assert "0.01" in expr
        assert "tolerance" not in expr

    def test_string_param_escaped(self, comparator, mock_cel):
        """String parameters are properly escaped."""
        response_a = make_response(body={"id": "abc-123"})
        response_b = make_response(body={"id": "xyz-456"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(predefined="both_match_regex", pattern="^[a-z]+-\\d+$")}
            ),
        )
        mock_cel.evaluate.return_value = True

        comparator.compare(response_a, response_b, rules)

        call_args = mock_cel.evaluate.call_args
        expr = call_args[0][0]
        # Pattern should be quoted and backslash escaped
        assert '"^[a-z]+-\\\\d+$"' in expr or '"^[a-z]+-\\d+$"' in expr

    def test_string_param_with_quotes_escaped(self, comparator, mock_cel):
        """String parameters with quotes are escaped."""
        response_a = make_response(body={"value": 'say "hello"'})
        response_b = make_response(body={"value": 'say "hi"'})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(predefined="both_match_regex", pattern='say ".*"')}
            ),
        )
        mock_cel.evaluate.return_value = True

        comparator.compare(response_a, response_b, rules)

        call_args = mock_cel.evaluate.call_args
        expr = call_args[0][0]
        # Quotes in pattern should be escaped
        assert '\\"' in expr

    def test_unknown_predefined_error(self, comparator):
        """Unknown predefined raises ComparatorConfigError."""
        response_a = make_response(body={"value": 1})
        response_b = make_response(body={"value": 1})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(predefined="nonexistent_rule")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        # Error is captured in differences
        assert result.match is False
        assert "error" in result.details["body"].differences[0].rule.lower()

    def test_missing_required_param(self, comparator):
        """Missing required parameter raises error."""
        response_a = make_response(body={"value": 1.0})
        response_b = make_response(body={"value": 1.0})
        rules = OperationRules(
            body=BodyRules(
                # numeric_tolerance requires 'tolerance' param
                field_rules={"$.value": FieldRule(predefined="numeric_tolerance")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        # Error is captured in differences
        assert result.match is False
        assert "error" in result.details["body"].differences[0].rule.lower()


# =============================================================================
# Custom CEL Expression Tests
# =============================================================================


class TestCustomExpressions:
    """Tests for custom CEL expressions."""

    def test_custom_expr_used(self, comparator, mock_cel):
        """Custom expression is passed directly to CEL."""
        response_a = make_response(body={"value": 10})
        response_b = make_response(body={"value": 20})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(expr="a < b")}
            ),
        )
        mock_cel.evaluate.return_value = True

        comparator.compare(response_a, response_b, rules)

        mock_cel.evaluate.assert_called_with("a < b", {"a": 10, "b": 20})

    def test_custom_expr_reports_as_custom(self, comparator, mock_cel):
        """Custom expression failures report as 'custom'."""
        response_a = make_response(body={"value": 10})
        response_b = make_response(body={"value": 5})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(expr="a < b")}
            ),
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.details["body"].differences[0].rule == "custom"


# =============================================================================
# JSONPath Error Handling Tests
# =============================================================================


class TestJSONPathErrors:
    """Tests for JSONPath error handling."""

    def test_invalid_jsonpath_syntax(self, comparator):
        """Invalid JSONPath syntax is handled gracefully."""
        response_a = make_response(body={"value": 1})
        response_b = make_response(body={"value": 1})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$[invalid": FieldRule(predefined="exact_match")}  # Malformed
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "jsonpath_error" in result.details["body"].differences[0].rule


# =============================================================================
# NULL Value Handling Tests
# =============================================================================


class TestNullValues:
    """Tests for JSON null value handling."""

    def test_null_vs_null(self, comparator, mock_cel):
        """null == null comparison."""
        response_a = make_response(body={"value": None})
        response_b = make_response(body={"value": None})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True
        # Should pass None values to CEL
        mock_cel.evaluate.assert_called_with("a == b", {"a": None, "b": None})

    def test_null_vs_value(self, comparator, mock_cel):
        """null vs non-null comparison."""
        response_a = make_response(body={"value": None})
        response_b = make_response(body={"value": 42})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_null_vs_missing_with_parity(self, comparator):
        """null (present with null value) vs missing field."""
        response_a = make_response(body={"value": None})  # Field present, value is null
        response_b = make_response(body={})  # Field missing
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(presence=PresenceMode.PARITY)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        # null is a present value, missing is absent - parity fails
        assert result.match is False
        assert "presence:parity" in result.details["body"].differences[0].rule


# =============================================================================
# Result Structure Tests
# =============================================================================


class TestResultStructure:
    """Tests for ComparisonResult structure."""

    def test_match_result_structure(self, comparator):
        """Matching result has correct structure."""
        response_a = make_response(status_code=200)
        response_b = make_response(status_code=200)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True
        assert result.mismatch_type is None
        assert result.summary == "Responses match"
        assert "status_code" in result.details
        assert "headers" in result.details
        assert "body" in result.details

    def test_mismatch_result_structure(self, comparator):
        """Mismatching result has correct structure."""
        response_a = make_response(status_code=200)
        response_b = make_response(status_code=500)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.STATUS_CODE
        assert "200" in result.summary and "500" in result.summary
        # Differences list populated
        assert len(result.details["status_code"].differences) == 1

    def test_field_difference_values(self, comparator, mock_cel):
        """FieldDifference contains actual values."""
        response_a = make_response(body={"id": 123})
        response_b = make_response(body={"id": 456})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        diff = result.details["body"].differences[0]
        assert diff.path == "$.id"
        assert diff.target_a == 123
        assert diff.target_b == 456
        assert diff.rule == "exact_match"


# =============================================================================
# Header Presence Tests
# =============================================================================


class TestHeaderPresence:
    """Tests for header presence modes."""

    def test_header_required_missing_in_a(self, comparator):
        """Header REQUIRED but missing in A fails."""
        response_a = make_response(headers={})
        response_b = make_response(headers={"x-request-id": ["abc"]})
        rules = OperationRules(
            headers={"x-request-id": FieldRule(presence=PresenceMode.REQUIRED)},
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.HEADERS
        assert "presence:required" in result.details["headers"].differences[0].rule

    def test_header_optional_missing_in_b(self, comparator):
        """Header OPTIONAL and missing in B passes."""
        response_a = make_response(headers={"x-optional": ["value"]})
        response_b = make_response(headers={})
        rules = OperationRules(
            headers={"x-optional": FieldRule(presence=PresenceMode.OPTIONAL)},
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["headers"].match is True

    def test_header_forbidden_present_fails(self, comparator):
        """Header FORBIDDEN but present fails."""
        response_a = make_response(headers={"x-internal": ["secret"]})
        response_b = make_response(headers={})
        rules = OperationRules(
            headers={"x-internal": FieldRule(presence=PresenceMode.FORBIDDEN)},
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "presence:forbidden" in result.details["headers"].differences[0].rule


# =============================================================================
# Comparison Order Tests
# =============================================================================


class TestComparisonOrder:
    """Tests verifying the order of comparison phases."""

    def test_status_checked_before_headers(self, comparator, mock_cel):
        """Status code is checked before headers."""
        response_a = make_response(status_code=200, headers={"x-test": ["a"]})
        response_b = make_response(status_code=500, headers={"x-test": ["b"]})
        rules = OperationRules(
            headers={"x-test": FieldRule(predefined="exact_match")},
        )

        result = comparator.compare(response_a, response_b, rules)

        # Should stop at status code
        assert result.mismatch_type == MismatchType.STATUS_CODE
        # Header comparison should not have been done
        mock_cel.evaluate.assert_not_called()

    def test_headers_checked_before_body(self, comparator, mock_cel):
        """Headers are checked before body."""
        response_a = make_response(
            status_code=200,
            headers={"content-type": ["a"]},
            body={"id": 1},
        )
        response_b = make_response(
            status_code=200,
            headers={"content-type": ["b"]},
            body={"id": 2},
        )
        rules = OperationRules(
            headers={"content-type": FieldRule(predefined="exact_match")},
            body=BodyRules(
                field_rules={"$.id": FieldRule(predefined="exact_match")}
            ),
        )
        # Header check fails
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        # Should stop at headers
        assert result.mismatch_type == MismatchType.HEADERS
        # Body should not be compared (only one CEL call for header)
        assert mock_cel.evaluate.call_count == 1


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_body_objects(self, comparator):
        """Empty body objects match."""
        response_a = make_response(body={})
        response_b = make_response(body={})
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_deeply_nested_path(self, comparator, mock_cel):
        """Deeply nested paths work."""
        response_a = make_response(body={"a": {"b": {"c": {"d": {"e": 1}}}}})
        response_b = make_response(body={"a": {"b": {"c": {"d": {"e": 1}}}}})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.a.b.c.d.e": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_unicode_values(self, comparator, mock_cel):
        """Unicode values are handled correctly."""
        response_a = make_response(body={"name": "日本語"})
        response_b = make_response(body={"name": "日本語"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.name": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True
        mock_cel.evaluate.assert_called_with("a == b", {"a": "日本語", "b": "日本語"})

    def test_boolean_values(self, comparator, mock_cel):
        """Boolean values are handled correctly."""
        response_a = make_response(body={"active": True})
        response_b = make_response(body={"active": False})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.active": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        mock_cel.evaluate.assert_called_with("a == b", {"a": True, "b": False})

    def test_array_as_field_value(self, comparator, mock_cel):
        """Array values can be compared."""
        response_a = make_response(body={"tags": ["a", "b", "c"]})
        response_b = make_response(body={"tags": ["a", "b", "c"]})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.tags": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True
        mock_cel.evaluate.assert_called_with("a == b", {"a": ["a", "b", "c"], "b": ["a", "b", "c"]})

    def test_object_as_field_value(self, comparator, mock_cel):
        """Nested object values can be compared."""
        response_a = make_response(body={"user": {"id": 1, "name": "Alice"}})
        response_b = make_response(body={"user": {"id": 1, "name": "Alice"}})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.user": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True


# =============================================================================
# CEL Error Handling Tests
# =============================================================================


class TestCELErrorHandling:
    """Tests for CEL error handling across all components."""

    def test_status_code_cel_error(self, comparator, mock_cel):
        """CEL error in status code comparison is captured."""
        from api_parity.cel_evaluator import CELEvaluationError

        response_a = make_response(status_code=200)
        response_b = make_response(status_code=201)
        rules = OperationRules(
            status_code=FieldRule(expr="invalid_expression"),
        )
        mock_cel.evaluate.side_effect = CELEvaluationError("syntax error")

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "error" in result.details["status_code"].differences[0].rule.lower()

    def test_header_cel_error(self, comparator, mock_cel):
        """CEL error in header comparison is captured."""
        from api_parity.cel_evaluator import CELEvaluationError

        response_a = make_response(headers={"x-test": ["value"]})
        response_b = make_response(headers={"x-test": ["value"]})
        rules = OperationRules(
            headers={"x-test": FieldRule(expr="broken")},
        )
        mock_cel.evaluate.side_effect = CELEvaluationError("unknown variable")

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "error" in result.details["headers"].differences[0].rule.lower()

    def test_body_cel_error(self, comparator, mock_cel):
        """CEL error in body comparison is captured."""
        from api_parity.cel_evaluator import CELEvaluationError

        response_a = make_response(body={"value": 1})
        response_b = make_response(body={"value": 2})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(expr="a.nonexistent()")}
            ),
        )
        mock_cel.evaluate.side_effect = CELEvaluationError("method not found")

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "error" in result.details["body"].differences[0].rule.lower()


# =============================================================================
# Multiple Rules Tests
# =============================================================================


class TestMultipleRules:
    """Tests for multiple rules in headers and body."""

    def test_multiple_header_rules_all_pass(self, comparator, mock_cel):
        """Multiple header rules all passing."""
        response_a = make_response(
            headers={
                "content-type": ["application/json"],
                "x-request-id": ["abc"],
                "x-version": ["1.0"],
            }
        )
        response_b = make_response(
            headers={
                "content-type": ["application/json"],
                "x-request-id": ["xyz"],
                "x-version": ["1.0"],
            }
        )
        rules = OperationRules(
            headers={
                "content-type": FieldRule(predefined="exact_match"),
                "x-request-id": FieldRule(predefined="ignore"),
                "x-version": FieldRule(predefined="exact_match"),
            },
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["headers"].match is True
        assert mock_cel.evaluate.call_count == 3

    def test_multiple_header_rules_one_fails(self, comparator, mock_cel):
        """Multiple header rules with one failing."""
        response_a = make_response(
            headers={
                "content-type": ["application/json"],
                "x-version": ["1.0"],
            }
        )
        response_b = make_response(
            headers={
                "content-type": ["application/json"],
                "x-version": ["2.0"],
            }
        )
        rules = OperationRules(
            headers={
                "content-type": FieldRule(predefined="exact_match"),
                "x-version": FieldRule(predefined="exact_match"),
            },
        )
        # content-type passes, x-version fails
        mock_cel.evaluate.side_effect = [True, False]

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.HEADERS
        assert len(result.details["headers"].differences) == 1
        assert "x-version" in result.details["headers"].differences[0].path

    def test_multiple_header_rules_mixed_presence(self, comparator):
        """Multiple header rules with mixed presence modes."""
        response_a = make_response(
            headers={
                "x-required": ["present"],
                # x-optional missing
                # x-forbidden missing
            }
        )
        response_b = make_response(
            headers={
                "x-required": ["present"],
                "x-optional": ["extra"],
                # x-forbidden missing
            }
        )
        rules = OperationRules(
            headers={
                "x-required": FieldRule(presence=PresenceMode.REQUIRED),
                "x-optional": FieldRule(presence=PresenceMode.OPTIONAL),
                "x-forbidden": FieldRule(presence=PresenceMode.FORBIDDEN),
            },
        )

        result = comparator.compare(response_a, response_b, rules)

        # All should pass: required present in both, optional okay if missing,
        # forbidden absent in both
        assert result.details["headers"].match is True


# =============================================================================
# JSONPath Caching Tests
# =============================================================================


class TestJSONPathCaching:
    """Tests for JSONPath expression caching."""

    def test_same_path_reused(self, comparator, mock_cel):
        """Same JSONPath is parsed once and reused."""
        response_a = make_response(body={"id": 1})
        response_b = make_response(body={"id": 1})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        # First comparison
        comparator.compare(response_a, response_b, rules)

        # Second comparison with same path
        comparator.compare(response_a, response_b, rules)

        # Path should be cached
        assert "$.id" in comparator._jsonpath_cache


# =============================================================================
# Recursive Descent Wildcard Tests
# =============================================================================


class TestRecursiveDescentWildcard:
    """Tests for recursive descent (..) wildcard detection."""

    def test_recursive_descent_detected_as_wildcard(self, comparator, mock_cel):
        """Recursive descent (..) is treated as wildcard."""
        response_a = make_response(
            body={
                "level1": {
                    "level2": {
                        "value": 1
                    }
                },
                "other": {
                    "value": 2
                }
            }
        )
        response_b = make_response(
            body={
                "level1": {
                    "level2": {
                        "value": 1
                    }
                },
                "other": {
                    "value": 2
                }
            }
        )
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$..value": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        # Should find all "value" fields at any depth
        assert result.details["body"].match is True
        # Should call evaluate for each matched value
        assert mock_cel.evaluate.call_count >= 2


# =============================================================================
# Summary Formatting Tests
# =============================================================================


class TestSummaryFormatting:
    """Tests for result summary formatting."""

    def test_single_header_difference_summary(self, comparator, mock_cel):
        """Single header difference has specific summary."""
        response_a = make_response(headers={"x-test": ["a"]})
        response_b = make_response(headers={"x-test": ["b"]})
        rules = OperationRules(
            headers={"x-test": FieldRule(predefined="exact_match")},
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert "Header mismatch:" in result.summary
        assert "x-test" in result.summary

    def test_multiple_header_differences_summary(self, comparator, mock_cel):
        """Multiple header differences have count summary."""
        response_a = make_response(headers={"x-one": ["a"], "x-two": ["b"]})
        response_b = make_response(headers={"x-one": ["c"], "x-two": ["d"]})
        rules = OperationRules(
            headers={
                "x-one": FieldRule(predefined="exact_match"),
                "x-two": FieldRule(predefined="exact_match"),
            },
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert "2 differences" in result.summary

    def test_single_body_difference_summary(self, comparator, mock_cel):
        """Single body difference has path in summary."""
        response_a = make_response(body={"id": 1})
        response_b = make_response(body={"id": 2})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(predefined="exact_match")}
            ),
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert "Body mismatch at" in result.summary
        assert "$.id" in result.summary

    def test_multiple_body_differences_summary(self, comparator, mock_cel):
        """Multiple body differences have count summary."""
        response_a = make_response(body={"id": 1, "name": "a"})
        response_b = make_response(body={"id": 2, "name": "b"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.id": FieldRule(predefined="exact_match"),
                    "$.name": FieldRule(predefined="exact_match"),
                }
            ),
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert "2 differences" in result.summary


# =============================================================================
# Empty/Minimal Input Tests
# =============================================================================


class TestEmptyInputs:
    """Tests for empty or minimal inputs."""

    def test_empty_headers_dict(self, comparator):
        """Empty headers dicts match."""
        response_a = make_response(headers={})
        response_b = make_response(headers={})
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["headers"].match is True

    def test_empty_body_rules(self, comparator):
        """Empty body rules means body passes."""
        response_a = make_response(body={"any": "content"})
        response_b = make_response(body={"different": "stuff"})
        rules = OperationRules(
            body=BodyRules(field_rules={}),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_body_rules_none(self, comparator):
        """None body rules means body passes."""
        response_a = make_response(body={"any": "content"})
        response_b = make_response(body={"different": "stuff"})
        rules = OperationRules(body=None)

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
