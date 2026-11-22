"""Unit tests for Comparator JSONPath handling.

Tests wildcard paths, recursive descent, caching, and error handling.
"""

import pytest

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
                field_rules={"$.items[*].id": FieldRule(expr="a == b")}
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

    @pytest.mark.parametrize(("presence", "missing_indices"), [
        ("parity", [0, 1]), ("required", [0, 1]), ("forbidden", [0, 1]), ("optional", []),
    ])
    def test_field_moves_to_another_array_element(self, comparator, presence, missing_indices):
        rule = FieldRule(presence=presence, predefined=None if presence == "forbidden" else "exact_match")
        rules = OperationRules(body=BodyRules(field_rules={"$.items[*].id": rule}))
        result = comparator.compare(make_response_case(body={"items": [{"id": None}, {}]}),
                                    make_response_case(body={"items": [{}, {"id": None}]}), rules)
        assert result.match == (not missing_indices)
        assert [d.path for d in result.details["body"].differences] == [
            f"items.[{i}].id" for i in missing_indices
        ]

    def test_optional_compares_shared_locations_when_counts_differ(self, comparator):
        rule = FieldRule(predefined="exact_match", presence="optional")
        rules = OperationRules(body=BodyRules(field_rules={"$.groups[*].items[*].id": rule}))
        def response(items):
            return make_response_case(body={"groups": [{"items": items}]})

        a = response([{"id": 3}, {}, {"id": 5}])
        assert comparator.compare(a, response([{}, {}, {"id": 5}]), rules).match
        result = comparator.compare(a, response([{}, {}, {"id": 6}]), rules)
        diff, = result.details["body"].differences
        assert (diff.path, diff.target_a, diff.target_b) == ("groups.[0].items.[2].id", 5, 6)

    def test_required_checks_selected_locations(self, comparator):
        rule = FieldRule(presence="required")
        rules = OperationRules(body=BodyRules(field_rules={"$.items[*].record.id": rule}))
        a = make_response_case(body={"items": [{"record": {"id": 1}}, {}, {}]})
        b = make_response_case(body={"items": [{"record": {"id": 1}}, {}, {"record": {"id": 2}}]})
        result = comparator.compare(a, b, rules)
        assert [(d.path, d.target_a, d.target_b) for d in result.details["body"].differences
                if d.rule == "presence:required"] == [
            ("items.[2].record.id", "<missing>", 2),
        ]

        assert comparator.compare(a, a, rules).match
        missing = make_response_case(body={"items": [{}]})
        assert not comparator.compare(missing, missing, rules).match

    @pytest.mark.parametrize("presence", ["parity", "required", "optional"])
    def test_length_only_failure_is_compact_and_stable(self, comparator, presence):
        query = "$.items[*].id"
        rules = OperationRules(body=BodyRules(field_rules={query: FieldRule(presence=presence)}))
        def response(size):
            return make_response_case(body={"items": [{"id": i} for i in range(size)]})

        for size in (6, 500):
            result = comparator.compare(response(5), response(size), rules)
            diff, = result.details["body"].differences
            assert (diff.path, diff.rule) == (query, "wildcard_count_mismatch")
            assert (diff.target_a, diff.target_b) == ("<5 matches>", f"<{size} matches>")

    @pytest.mark.parametrize("query", ["$..id", "$.items[0:2].id", "$.items.*.id"])
    def test_selectors_pair_by_location(self, comparator, query):
        a, b = [{"id": 1}, {}], [{}, {"id": 1}]
        if query == "$.items.*.id":
            a, b = dict(zip(("x", "y"), a)), dict(zip(("x", "y"), b))
        rules = OperationRules(body=BodyRules(field_rules={query: FieldRule(predefined="exact_match")}))
        result = comparator.compare(make_response_case(body={"items": a}),
                                    make_response_case(body={"items": b}), rules)
        diffs = result.details["body"].differences
        assert len(diffs) == 2 and diffs[0].path != diffs[1].path
        assert [(d.target_a, d.target_b) for d in diffs] == [(1, "<missing>"), ("<missing>", 1)]

    def test_same_single_location_keeps_query_path(self, comparator):
        query = "$.items[*].id"
        rules = OperationRules(body=BodyRules(field_rules={query: FieldRule(predefined="exact_match")}))
        result = comparator.compare(make_response_case(body={"items": [{"id": 1}]}),
                                    make_response_case(body={"items": [{"id": 2}]}), rules)
        diff, = result.details["body"].differences
        assert (diff.path, diff.target_a, diff.target_b) == (query, 1, 2)

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
                field_rules={"$..value": FieldRule(expr="a == b")}
            ),
        )
        mock_cel.evaluate.return_value = True

        result = comparator.compare(response_a, response_b, rules)

        # Should find all "value" fields at any depth
        assert result.details["body"].match is True
        # Should call evaluate for each matched value
        assert mock_cel.evaluate.call_count >= 2
