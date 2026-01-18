"""Unit tests for Comparator JSONPath handling.

Tests wildcard paths, recursive descent, caching, and error handling.
"""

from api_parity.models import BodyRules, FieldRule, OperationRules
from tests.conftest import make_response_case

# Import shared fixtures
pytest_plugins = ["tests.comparator_fixtures"]


class TestWildcardPaths:
    """Tests for JSONPath wildcard handling."""

    def test_wildcard_same_length(self, comparator, mock_cel):
        """Wildcard paths with same array length compare elements."""
        response_a = make_response_case(body={"items": [{"id": 1}, {"id": 2}]})
        response_b = make_response_case(body={"items": [{"id": 1}, {"id": 2}]})
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
        response_a = make_response_case(body={"items": [{"id": 1}, {"id": 2}]})
        response_b = make_response_case(body={"items": [{"id": 1}]})
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
        response_a = make_response_case(body={"items": [{"id": 1}, {"id": 2}]})
        response_b = make_response_case(body={"items": [{"id": 1}, {"id": 99}]})
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
        response_a = make_response_case(body={"items": []})
        response_b = make_response_case(body={"items": []})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.items[*].id": FieldRule(predefined="exact_match")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        # Both have 0 matches, which is equal
        assert result.details["body"].match is True


class TestJSONPathErrors:
    """Tests for JSONPath error handling."""

    def test_invalid_jsonpath_syntax(self, comparator):
        """Invalid JSONPath syntax is handled gracefully."""
        response_a = make_response_case(body={"value": 1})
        response_b = make_response_case(body={"value": 1})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$[invalid": FieldRule(predefined="exact_match")}  # Malformed
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "jsonpath_error" in result.details["body"].differences[0].rule


class TestJSONPathCaching:
    """Tests for JSONPath expression caching."""

    def test_same_path_reused(self, comparator, mock_cel):
        """Same JSONPath is parsed once and reused."""
        response_a = make_response_case(body={"id": 1})
        response_b = make_response_case(body={"id": 1})
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


class TestRecursiveDescentWildcard:
    """Tests for recursive descent (..) wildcard detection."""

    def test_recursive_descent_detected_as_wildcard(self, comparator, mock_cel):
        """Recursive descent (..) is treated as wildcard."""
        response_a = make_response_case(
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
        response_b = make_response_case(
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
