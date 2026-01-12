"""Unit tests for Comparator header comparison."""

from api_parity.models import FieldRule, MismatchType, OperationRules, PresenceMode
from tests.conftest import make_response

# Import shared fixtures
pytest_plugins = ["tests.comparator_fixtures"]


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


class TestMultipleHeaderRules:
    """Tests for multiple header rules."""

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
