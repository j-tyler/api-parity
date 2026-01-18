"""Unit tests for Comparator status code comparison and comparison order."""

from api_parity.models import BodyRules, FieldRule, MismatchType, OperationRules
from tests.conftest import make_response_case

# Import shared fixtures
pytest_plugins = ["tests.comparator_fixtures"]


class TestStatusCodeComparison:
    """Tests for status code comparison."""

    def test_exact_match_no_rule(self, comparator):
        """Default behavior is exact match."""
        response_a = make_response_case(status_code=200)
        response_b = make_response_case(status_code=200)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True
        assert result.details["status_code"].match is True

    def test_mismatch_no_rule(self, comparator):
        """Different status codes fail with default rules."""
        response_a = make_response_case(status_code=200)
        response_b = make_response_case(status_code=201)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.STATUS_CODE
        assert "200 vs 201" in result.summary

    def test_with_custom_rule(self, comparator, mock_cel):
        """Status code comparison uses custom rule when provided."""
        response_a = make_response_case(status_code=200)
        response_b = make_response_case(status_code=201)
        rules = OperationRules(
            status_code=FieldRule(predefined="ignore"),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is True
        mock_cel.evaluate.assert_called()

    def test_rule_returns_false(self, comparator, mock_cel):
        """Status code mismatch when rule returns False."""
        response_a = make_response_case(status_code=200)
        response_b = make_response_case(status_code=201)
        rules = OperationRules(
            status_code=FieldRule(predefined="exact_match"),
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.STATUS_CODE


class TestComparisonOrder:
    """Tests verifying the order of comparison phases."""

    def test_status_checked_before_headers(self, comparator, mock_cel):
        """Status code is checked before headers."""
        response_a = make_response_case(status_code=200, headers={"x-test": ["a"]})
        response_b = make_response_case(status_code=500, headers={"x-test": ["b"]})
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
        response_a = make_response_case(
            status_code=200,
            headers={"content-type": ["a"]},
            body={"id": 1},
        )
        response_b = make_response_case(
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
