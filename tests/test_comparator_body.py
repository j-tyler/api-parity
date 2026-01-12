"""Unit tests for Comparator body comparison and presence modes."""

from api_parity.models import BodyRules, FieldRule, MismatchType, OperationRules, PresenceMode
from tests.conftest import make_response

# Import shared fixtures
pytest_plugins = ["tests.comparator_fixtures"]


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
