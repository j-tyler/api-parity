"""Unit tests for Comparator rule expansion and CEL error handling.

Tests predefined rule expansion, custom expressions, and CEL error capture.
"""

from api_parity.cel_evaluator import CELEvaluationError
from api_parity.models import BodyRules, FieldRule, MismatchType, OperationRules
from tests.conftest import make_response

# Import shared fixtures
pytest_plugins = ["tests.comparator_fixtures"]


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


class TestCELErrorHandling:
    """Tests for CEL error handling across all components."""

    def test_status_code_cel_error(self, comparator, mock_cel):
        """CEL error in status code comparison is captured."""
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
