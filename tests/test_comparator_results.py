"""Unit tests for Comparator result structure, formatting, and edge cases."""

from api_parity.models import BodyRules, FieldRule, MismatchType, OperationRules
from tests.conftest import make_response

# Import shared fixtures
pytest_plugins = ["tests.comparator_fixtures"]


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
