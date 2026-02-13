"""Unit tests for Comparator body comparison and presence modes."""

from api_parity.models import BodyRules, FieldRule, MismatchType, OperationRules, PresenceMode
from tests.conftest import make_response_case

# Import shared fixtures
pytest_plugins = ["tests.comparator_fixtures"]


class TestPresenceModes:
    """Tests for field presence checking."""

    def test_parity_both_present(self, comparator, mock_cel):
        """PARITY: both present passes."""
        response_a = make_response_case(body={"name": "Alice"})
        response_b = make_response_case(body={"name": "Bob"})
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
        response_a = make_response_case(body={"foo": 1})
        response_b = make_response_case(body={"bar": 2})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.name": FieldRule(presence=PresenceMode.PARITY)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_parity_one_present_fails(self, comparator):
        """PARITY: one present, one absent fails."""
        response_a = make_response_case(body={"name": "Alice"})
        response_b = make_response_case(body={})
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
        response_a = make_response_case(body={"id": 1})
        response_b = make_response_case(body={"id": 2})
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
        response_a = make_response_case(body={"id": 1})
        response_b = make_response_case(body={})
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
        response_a = make_response_case(body={})
        response_b = make_response_case(body={})
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
        response_a = make_response_case(body={"foo": 1})
        response_b = make_response_case(body={"bar": 2})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.secret": FieldRule(presence=PresenceMode.FORBIDDEN)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_forbidden_one_present_fails(self, comparator):
        """FORBIDDEN: one present fails."""
        response_a = make_response_case(body={"secret": "value"})
        response_b = make_response_case(body={})
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
        response_a = make_response_case(body={"secret": "a"})
        response_b = make_response_case(body={"secret": "b"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.secret": FieldRule(presence=PresenceMode.FORBIDDEN)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False

    def test_optional_both_present(self, comparator, mock_cel):
        """OPTIONAL: both present, compares values."""
        response_a = make_response_case(body={"nickname": "Al"})
        response_b = make_response_case(body={"nickname": "Bob"})
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
        response_a = make_response_case(body={"nickname": "Al"})
        response_b = make_response_case(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.nickname": FieldRule(presence=PresenceMode.OPTIONAL, predefined="exact_match")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True  # Optional allows missing

    def test_optional_both_absent(self, comparator):
        """OPTIONAL: both absent passes."""
        response_a = make_response_case(body={})
        response_b = make_response_case(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.nickname": FieldRule(presence=PresenceMode.OPTIONAL, predefined="exact_match")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_presence_only_rule(self, comparator):
        """Presence-only rule (no predefined/expr) only checks presence."""
        response_a = make_response_case(body={"id": 1})
        response_b = make_response_case(body={"id": 999})  # Different value
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.id": FieldRule(presence=PresenceMode.PARITY)}  # No predefined/expr
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        # Should pass because we only check presence, not values
        assert result.details["body"].match is True


class TestIgnorePresenceDefault:
    """Tests for 'ignore' predefined defaulting presence to OPTIONAL.

    When predefined='ignore' is used without explicit presence, presence
    defaults to OPTIONAL so the field is completely skipped.
    """

    def test_ignore_body_field_one_missing_passes(self, comparator, mock_cel):
        """Body field with ignore passes when one target lacks it."""
        response_a = make_response_case(body={"request_id": "abc-123"})
        response_b = make_response_case(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.request_id": FieldRule(predefined="ignore")}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True
        mock_cel.evaluate.assert_not_called()

    def test_ignore_body_field_both_present_passes(self, comparator, mock_cel):
        """Body field with ignore passes when both present."""
        response_a = make_response_case(body={"request_id": "abc-123"})
        response_b = make_response_case(body={"request_id": "xyz-789"})
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.request_id": FieldRule(predefined="ignore")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_ignore_with_explicit_parity_checks_presence(self, comparator):
        """Explicit presence=parity + ignore still enforces presence parity."""
        response_a = make_response_case(body={"request_id": "abc-123"})
        response_b = make_response_case(body={})
        rules = OperationRules(
            body=BodyRules(
                field_rules={
                    "$.request_id": FieldRule(
                        presence=PresenceMode.PARITY, predefined="ignore"
                    )
                }
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "presence:parity" in result.details["body"].differences[0].rule


class TestBodyComparison:
    """Tests for body comparison."""

    def test_both_bodies_none(self, comparator):
        """Both None bodies match."""
        response_a = make_response_case(body=None)
        response_b = make_response_case(body=None)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_one_body_none_mismatch(self, comparator):
        """One body None, one not is a mismatch."""
        response_a = make_response_case(body={"id": 1})
        response_b = make_response_case(body=None)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY
        assert "body_presence" in result.details["body"].differences[0].rule

    def test_no_body_rules(self, comparator):
        """No body rules means body passes."""
        response_a = make_response_case(body={"different": 1})
        response_b = make_response_case(body={"values": 2})
        rules = OperationRules()  # No body rules

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["body"].match is True

    def test_simple_field_match(self, comparator, mock_cel):
        """Simple field comparison works."""
        response_a = make_response_case(body={"name": "Alice"})
        response_b = make_response_case(body={"name": "Alice"})
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
        response_a = make_response_case(body={"user": {"profile": {"name": "Alice"}}})
        response_b = make_response_case(body={"user": {"profile": {"name": "Alice"}}})
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
        response_a = make_response_case(body={"items": [{"id": 1}, {"id": 2}]})
        response_b = make_response_case(body={"items": [{"id": 1}, {"id": 2}]})
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
        response_a = make_response_case(body={"id": 1, "name": "Alice", "age": 30})
        response_b = make_response_case(body={"id": 1, "name": "Alice", "age": 30})
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
        response_a = make_response_case(body={"id": 1, "name": "Alice"})
        response_b = make_response_case(body={"id": 1, "name": "Bob"})
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


class TestNullValues:
    """Tests for JSON null value handling."""

    def test_null_vs_null(self, comparator, mock_cel):
        """null == null comparison."""
        response_a = make_response_case(body={"value": None})
        response_b = make_response_case(body={"value": None})
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
        response_a = make_response_case(body={"value": None})
        response_b = make_response_case(body={"value": 42})
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
        response_a = make_response_case(body={"value": None})  # Field present, value is null
        response_b = make_response_case(body={})  # Field missing
        rules = OperationRules(
            body=BodyRules(
                field_rules={"$.value": FieldRule(presence=PresenceMode.PARITY)}
            ),
        )

        result = comparator.compare(response_a, response_b, rules)

        # null is a present value, missing is absent - parity fails
        assert result.match is False
        assert "presence:parity" in result.details["body"].differences[0].rule


class TestBinaryBodyComparison:
    """Tests for binary body (body_base64) comparison."""

    def test_both_no_binary(self, comparator):
        """Neither response has binary body - match."""
        response_a = make_response_case(body={"id": 1})
        response_b = make_response_case(body={"id": 1})
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["binary_body"].match is True
        assert result.details["binary_body"].differences == []

    def test_one_has_binary_other_empty(self, comparator):
        """One response has binary content, other has nothing - mismatch."""
        # Both have body=None (neither is JSON), but only one has binary content
        response_a = make_response_case(body=None, body_base64="SGVsbG8=")  # "Hello" in base64
        response_b = make_response_case(body=None)  # No content at all
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY
        assert "binary_presence" in result.details["binary_body"].differences[0].rule

    def test_one_json_one_binary_caught_in_body_phase(self, comparator):
        """One response is JSON, other is binary - caught in body comparison phase."""
        response_a = make_response_case(body=None, body_base64="SGVsbG8=")  # Binary
        response_b = make_response_case(body={"id": 1})  # JSON
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        )

        result = comparator.compare(response_a, response_b, rules)

        # This is caught in body comparison (one has body, one doesn't)
        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY
        assert "body_presence" in result.details["body"].differences[0].rule

    def test_no_binary_rule_both_have_binary(self, comparator):
        """Both have binary but no rule specified - match (not compared)."""
        response_a = make_response_case(body=None, body_base64="SGVsbG8=")
        response_b = make_response_case(body=None, body_base64="V29ybGQ=")  # Different content
        rules = OperationRules()  # No binary_rule specified

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["binary_body"].match is True  # Not compared, default match

    def test_binary_exact_match_same(self, comparator, mock_cel):
        """Binary exact match with identical content."""
        response_a = make_response_case(body=None, body_base64="SGVsbG8=")
        response_b = make_response_case(body=None, body_base64="SGVsbG8=")
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["binary_body"].match is True
        mock_cel.evaluate.assert_called_with("a == b", {"a": "SGVsbG8=", "b": "SGVsbG8="})

    def test_binary_exact_match_different(self, comparator, mock_cel):
        """Binary exact match with different content - mismatch."""
        response_a = make_response_case(body=None, body_base64="SGVsbG8=")
        response_b = make_response_case(body=None, body_base64="V29ybGQ=")
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert result.mismatch_type == MismatchType.BODY
        assert result.details["binary_body"].differences[0].rule == "exact_match"

    def test_binary_length_match(self, comparator, mock_cel):
        """Binary length match (CEL-based)."""
        # Both 8 chars in base64
        response_a = make_response_case(body=None, body_base64="SGVsbG8=")
        response_b = make_response_case(body=None, body_base64="V29ybGQ=")
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="binary_length_match"))
        )
        mock_cel.evaluate.return_value = True  # size(a) == size(b)

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["binary_body"].match is True

    def test_binary_custom_cel(self, comparator, mock_cel):
        """Binary comparison with custom CEL expression."""
        response_a = make_response_case(body=None, body_base64="SGVsbG8=")
        response_b = make_response_case(body=None, body_base64="SGVsbG9Xb3JsZA==")
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(expr='a.startsWith("SGVs")'))
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["binary_body"].match is True

    def test_binary_summary_format(self, comparator, mock_cel):
        """Summary format includes rule name."""
        response_a = make_response_case(body=None, body_base64="SGVsbG8=")
        response_b = make_response_case(body=None, body_base64="V29ybGQ=")
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        )
        mock_cel.evaluate.return_value = False

        result = comparator.compare(response_a, response_b, rules)

        assert "Binary body mismatch" in result.summary

    def test_empty_base64_vs_empty_base64(self, comparator, mock_cel):
        """Empty base64 strings ('') are compared, not treated as missing."""
        response_a = make_response_case(body=None, body_base64="")
        response_b = make_response_case(body=None, body_base64="")
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        assert result.details["binary_body"].match is True
        # CEL should be called with empty strings, not skipped
        mock_cel.evaluate.assert_called_with("a == b", {"a": "", "b": ""})

    def test_empty_base64_vs_none(self, comparator):
        """Empty string is distinct from None - presence mismatch."""
        response_a = make_response_case(body=None, body_base64="")
        response_b = make_response_case(body=None, body_base64=None)
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        )

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False
        assert "binary_presence" in result.details["binary_body"].differences[0].rule

    def test_binary_nonempty_with_empty_string(self, comparator, mock_cel):
        """binary_nonempty fails for empty strings (size('') == 0)."""
        response_a = make_response_case(body=None, body_base64="")
        response_b = make_response_case(body=None, body_base64="")
        rules = OperationRules(
            body=BodyRules(binary_rule=FieldRule(predefined="binary_nonempty"))
        )
        mock_cel.evaluate.return_value = False  # size(a) > 0 && size(b) > 0 is False

        result = comparator.compare(response_a, response_b, rules)

        assert result.match is False


class TestBinaryRuleValidation:
    """Tests for binary_rule validation in BodyRules."""

    def test_binary_rule_rejects_non_parity_presence(self):
        """binary_rule only allows presence=parity."""
        import pytest

        # REQUIRED should be rejected
        with pytest.raises(ValueError, match="only supports presence=parity"):
            BodyRules(binary_rule=FieldRule(presence=PresenceMode.REQUIRED, predefined="exact_match"))

        # FORBIDDEN should be rejected
        with pytest.raises(ValueError, match="only supports presence=parity"):
            BodyRules(binary_rule=FieldRule(presence=PresenceMode.FORBIDDEN))

        # OPTIONAL should be rejected
        with pytest.raises(ValueError, match="only supports presence=parity"):
            BodyRules(binary_rule=FieldRule(presence=PresenceMode.OPTIONAL, predefined="exact_match"))

    def test_binary_rule_allows_parity(self):
        """binary_rule accepts default parity mode (explicit or implicit)."""
        # Explicit PARITY should work
        rules = BodyRules(binary_rule=FieldRule(presence=PresenceMode.PARITY, predefined="exact_match"))
        assert rules.binary_rule.presence == PresenceMode.PARITY

        # Implicit PARITY (default) should work
        rules = BodyRules(binary_rule=FieldRule(predefined="exact_match"))
        assert rules.binary_rule.presence == PresenceMode.PARITY
