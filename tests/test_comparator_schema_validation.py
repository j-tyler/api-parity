"""Tests for Comparator integration with schema validation.

Tests the OpenAPI Spec as Field Authority feature in the Comparator:
- Phase 0: Schema validation before comparison
- Extra fields comparison for flexible schemas
- SCHEMA_VIOLATION mismatch type
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from api_parity.comparator import Comparator
from api_parity.executor import Executor
from api_parity.models import (
    ComparisonLibrary,
    MismatchType,
    OperationRules,
    PredefinedComparison,
    RequestCase,
    ResponseCase,
    TargetConfig,
)
from api_parity.schema_validator import SchemaValidator

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMA_VALIDATION_SPEC = FIXTURES_DIR / "test_api_schema_validation.yaml"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_cel():
    """Mock CEL evaluator."""
    cel = MagicMock()
    cel.evaluate.return_value = True
    return cel


@pytest.fixture
def comparison_library():
    """Minimal comparison library."""
    return ComparisonLibrary(
        library_version="1",
        description="Test library",
        predefined={
            "exact_match": PredefinedComparison(
                description="Exact equality",
                params=[],
                expr="a == b",
            ),
        },
    )


@pytest.fixture
def schema_validator():
    """Schema validator with test spec."""
    return SchemaValidator(SCHEMA_VALIDATION_SPEC)


@pytest.fixture
def comparator(mock_cel, comparison_library, schema_validator):
    """Comparator with schema validation enabled."""
    return Comparator(mock_cel, comparison_library, schema_validator)


@pytest.fixture
def comparator_no_schema(mock_cel, comparison_library):
    """Comparator without schema validation."""
    return Comparator(mock_cel, comparison_library)


def make_response(status_code=200, body=None, headers=None):
    """Helper to create ResponseCase for testing."""
    return ResponseCase(
        status_code=status_code,
        headers=headers or {},
        body=body,
        elapsed_ms=10.0,
    )


# =============================================================================
# Schema Validation Integration Tests
# =============================================================================


class TestSchemaValidationPhase:
    """Tests for schema validation phase in comparison."""

    def test_valid_responses_pass_schema_phase(self, comparator):
        """Valid responses pass schema validation phase."""
        body_a = {"widgets": [{"id": "1", "name": "W", "price": 10.0}], "total": 1}
        body_b = {"widgets": [{"id": "2", "name": "X", "price": 20.0}], "total": 1}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules, "listStrictWidgets")

        # Should not have schema violations
        assert result.mismatch_type != MismatchType.SCHEMA_VIOLATION

    def test_schema_violation_in_response_a(self, comparator):
        """Schema violation in response A returns SCHEMA_VIOLATION mismatch."""
        body_a = {"widgets": [], "total": 0, "extra": "bad"}  # Extra field
        body_b = {"widgets": [], "total": 0}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules, "listStrictWidgets")

        assert result.match is False
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION
        assert "schema" in result.details

    def test_schema_violation_in_response_b(self, comparator):
        """Schema violation in response B returns SCHEMA_VIOLATION mismatch."""
        body_a = {"widgets": [], "total": 0}
        body_b = {"widgets": [], "total": 0, "extra": "bad"}  # Extra field

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules, "listStrictWidgets")

        assert result.match is False
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION

    def test_both_responses_have_violations(self, comparator):
        """Both responses with violations report all violations."""
        body_a = {"widgets": [], "total": 0, "extra_a": "bad"}
        body_b = {"widgets": [], "total": 0, "extra_b": "also bad"}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(response_a, response_b, rules, "listStrictWidgets")

        assert result.match is False
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION
        # Should have violations from both responses
        schema_details = result.details.get("schema")
        assert schema_details is not None
        assert len(schema_details.differences) >= 2


class TestSchemaValidationSkipping:
    """Tests for when schema validation is skipped."""

    def test_no_schema_validator_skips_validation(self, comparator_no_schema):
        """Comparison without schema_validator skips schema phase."""
        body = {"anything": "goes"}

        response_a = make_response(body=body)
        response_b = make_response(body=body)
        rules = OperationRules()

        result = comparator_no_schema.compare(
            response_a, response_b, rules, "listStrictWidgets"
        )

        # Should not have schema key in details
        assert "schema" not in result.details

    def test_no_operation_id_skips_validation(self, comparator):
        """Comparison without operation_id skips schema phase."""
        body = {"extra": "allowed when not validated"}

        response_a = make_response(body=body)
        response_b = make_response(body=body)
        rules = OperationRules()

        # No operation_id passed
        result = comparator.compare(response_a, response_b, rules)

        # Should not have schema key in details
        assert "schema" not in result.details

    def test_unknown_operation_passes_validation(self, comparator):
        """Unknown operation (no schema) passes schema validation."""
        body = {"any_field": "value"}

        response_a = make_response(body=body)
        response_b = make_response(body=body)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "unknownOperation"
        )

        # Should pass (no schema to validate against)
        assert result.mismatch_type != MismatchType.SCHEMA_VIOLATION


class TestExtraFieldsComparison:
    """Tests for extra fields comparison in flexible schemas."""

    @pytest.mark.parametrize("wrapped", [False, True], ids=["root-array", "closed-wrapper"])
    def test_item_extra_fields_follow_the_item_schema(self, tmp_path, mock_cel, comparison_library, wrapped):
        item = {"type": "object", "properties": {"id": {"type": "integer"}}}
        rows = {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}
        schema = ({"type": "object", "additionalProperties": False, "properties": {"rows": rows}}
                  if wrapped else rows)
        spec = {"openapi": "3.0.3", "components": {"schemas": {"Item": item}},
                "paths": {"/rows": {"get": {"operationId": "rows", "responses": {
                    "200": {"content": {"application/json": {"schema": schema}}}}}}}}
        validator = SchemaValidator(tmp_path / "rows.json", source_bytes=json.dumps(spec).encode())
        compare = Comparator(mock_cel, comparison_library, validator)
        row_a = {"id": 2, "enabled": True}
        row_b = {"id": 2, "enabled": False}
        body_a = [{"id": 1}, row_a]
        body_b = [{"id": 1}, row_b]
        if wrapped:
            body_a, body_b = {"rows": body_a}, {"rows": body_b}
        path = "$.rows[1].enabled" if wrapped else "$[1].enabled"
        assert validator.get_extra_fields(body_a, "rows", 200) == [path]

        def difference():
            return compare.compare(make_response(body=body_a), make_response(body=body_b), OperationRules(), "rows")

        result = difference()
        assert not result.match
        diff, = result.details["extra_fields"].differences
        assert (diff.path, diff.target_a, diff.target_b, diff.rule) == (path, True, False, "extra_field_equality")
        del row_b["enabled"]
        diff, = difference().details["extra_fields"].differences
        assert (diff.path, diff.rule) == (path, "extra_field_presence")
        row_b["enabled"] = True
        assert difference().match

    def test_extra_fields_compared_when_allowed(self, comparator):
        """Extra fields in flexible schemas are compared for equality."""
        body_a = {"widgets": [{"id": "1", "name": "W"}], "extra": "value_a"}
        body_b = {"widgets": [{"id": "1", "name": "W"}], "extra": "value_b"}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listFlexibleWidgets"
        )

        # Should mismatch due to extra field difference
        assert result.match is False
        # Should have extra_fields in details
        assert "extra_fields" in result.details

    @pytest.mark.parametrize(("left", "right"), [(True, 1), (2**53 + 1, float(2**53))])
    def test_distinct_extra_field_values(self, comparator, left, right):
        def response(value):
            return make_response(body={"widgets": [], "extra": {"values": [value]}})

        result = comparator.compare(response(left), response(right), OperationRules(), "listFlexibleWidgets")
        assert not result.details["extra_fields"].match

    def test_same_extra_fields_match(self, comparator):
        """Same extra field values in flexible schemas match."""
        body_a = {"widgets": [{"id": "1", "name": "W"}], "extra": "same"}
        body_b = {"widgets": [{"id": "1", "name": "W"}], "extra": "same"}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listFlexibleWidgets"
        )

        # Extra fields with same values should not cause mismatch
        if "extra_fields" in result.details:
            assert result.details["extra_fields"].match is True

    def test_extra_field_missing_in_one_response(self, comparator):
        """Extra field present in one response but not other is mismatch."""
        body_a = {"widgets": [{"id": "1", "name": "W"}], "extra": "exists"}
        body_b = {"widgets": [{"id": "1", "name": "W"}]}  # No extra field

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listFlexibleWidgets"
        )

        # Should mismatch due to presence difference
        assert result.match is False
        assert "extra_fields" in result.details


class TestSchemaValidationPrecedence:
    """Tests that schema validation happens before other comparisons."""

    def test_schema_violation_before_status_code(self, comparator):
        """Schema violation reported before status code comparison."""
        body_a = {"widgets": [], "total": 0, "extra": "bad"}
        body_b = {"widgets": [], "total": 0}

        response_a = make_response(status_code=200, body=body_a)
        response_b = make_response(status_code=201, body=body_b)  # Different status
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listStrictWidgets"
        )

        # Schema violation should be detected first
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION

    def test_schema_violation_before_body_comparison(self, comparator):
        """Schema violation reported before body comparison."""
        body_a = {"widgets": [{"id": "1", "name": "A", "price": 10.0}], "total": 1, "extra": "bad"}
        body_b = {"widgets": [{"id": "2", "name": "B", "price": 20.0}], "total": 1}  # Different values

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listStrictWidgets"
        )

        # Schema violation should be detected first
        assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION


class TestMismatchTypeSerialization:
    """Tests that SCHEMA_VIOLATION mismatch type serializes correctly."""

    def test_schema_violation_enum_value(self):
        """SCHEMA_VIOLATION has correct string value."""
        assert MismatchType.SCHEMA_VIOLATION.value == "schema_violation"

    def test_schema_violation_in_result_json(self, comparator):
        """SCHEMA_VIOLATION mismatch type appears in result JSON."""
        body_a = {"widgets": [], "total": 0, "extra": "bad"}
        body_b = {"widgets": [], "total": 0}

        response_a = make_response(body=body_a)
        response_b = make_response(body=body_b)
        rules = OperationRules()

        result = comparator.compare(
            response_a, response_b, rules, "listStrictWidgets"
        )

        result_dict = result.model_dump()
        assert result_dict["mismatch_type"] == "schema_violation"


@pytest.fixture
def list_comparator(tmp_path, mock_cel):
    from api_parity.config_loader import load_comparison_library

    schema = {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    spec = {"paths": {"/rows": {"get": {"operationId": "rows", "responses": {
        "200": {"content": {"application/json": {"schema": schema}}}}}}}}
    validator = SchemaValidator(tmp_path / "spec.json", source_bytes=json.dumps(spec).encode())
    return Comparator(mock_cel, load_comparison_library(), validator)


def test_extra_field_groups_use_literal_locations_and_stable_patterns(list_comparator, monkeypatch):
    from api_parity.mismatch_classifier import is_same_mismatch, mismatch_dedup_key

    def unexpected_compile(*args):
        pytest.fail("a discovered extra-field location invoked the JSONPath compiler")

    monkeypatch.setattr(list_comparator, "_compile_jsonpath", unexpected_compile)
    def compare(n, key):
        a = [{key: True, "meta": {"etag": 0}} for _ in range(n)]
        b = [{key: False, "meta": {"etag": 0}} for _ in range(n)]
        return list_comparator.compare(make_response(body=a), make_response(body=b), OperationRules(), "rows")

    initial = compare(2, "meta.etag").model_dump(mode="json")
    later = compare(30, "meta.etag")
    diff, = later.details["extra_fields"].differences
    assert (diff.path, diff.path_pattern, diff.occurrences) == ("$[0]['meta.etag']", "$[*]['meta.etag']", 30)
    assert (diff.target_a, diff.target_b) == (True, False)
    assert is_same_mismatch(initial, later)
    assert mismatch_dedup_key("rows", initial) == mismatch_dedup_key("rows", later.model_dump(mode="json"))
    assert not is_same_mismatch(initial, compare(30, "etag"))


def test_extra_field_rules_cover_only_selected_locations(list_comparator):
    def compare(a, b, rules):
        return list_comparator.compare(make_response(body=a), make_response(body=b),
                                      OperationRules(body={"field_rules": rules}), "rows")

    a = [{"stamp": 1, "meta": {"a.b": 1, "sibling": 1}}]
    b = [{"stamp": 2, "meta": {"a.b": 2, "sibling": 1}}]
    rules = {"$[*].stamp": {"predefined": "ignore"}, "$[*].meta['a.b']": {"predefined": "ignore"}}
    assert compare(a, b, rules).match
    b[0]["meta"]["sibling"] = 2
    assert not compare(a, b, rules).match
    del b[0]["meta"]["sibling"]
    assert not compare(a, b, rules).match
    assert compare(a, b, {"$[*]": {"predefined": "ignore"}}).match
    assert not compare(a, b, {"$[*]": {"presence": "required"}}).match


def test_grouping_preserves_presence_and_value_evidence(list_comparator):
    a = [{"etag": None}, {"etag": "A"}, {"etag": None}, {"etag": "C"}]
    b = [{}, {"etag": "B"}, {}, {"etag": "D"}]
    r = list_comparator.compare(make_response(body=a), make_response(body=b), OperationRules(), "rows")
    equality, presence = r.details["extra_fields"].differences
    assert (equality.path, equality.occurrences, equality.rule) == ("$[1].etag", 2, "extra_field_equality")
    assert (presence.path, presence.occurrences, presence.rule) == ("$[0].etag", 2, "extra_field_presence")
    assert presence.target_a is None and presence.target_b == "<missing>"


@pytest.fixture
def compare_media(tmp_path, monkeypatch, comparison_library, mock_cel):
    def run(declared, schema, served, first, second, rules=None):
        definition = {"paths": {"/item": {"get": {"operationId": "item", "responses": {
            "200": {"content": {declared: {"schema": {"$ref": "#/components/schemas/Item"}}}},
        }}}}, "components": {"schemas": {"Item": schema}}}
        path = tmp_path / "openapi.json"
        path.write_text(json.dumps(definition))

        def respond(request):
            return httpx.Response(200, headers={"Content-Type": served},
                                  content=first if request.url.host == "a" else second)
        monkeypatch.setattr(Executor, "_build_client_kwargs", lambda self, target, timeout: {
            "base_url": target.base_url, "transport": httpx.MockTransport(respond),
        })
        case = RequestCase(case_id="read", operation_id="item", method="GET",
                           path_template="/item", rendered_path="/item")
        with Executor(TargetConfig(base_url="http://a"), TargetConfig(base_url="http://b")) as executor:
            a, b = executor.execute(case)
        comparator = Comparator(mock_cel, comparison_library, SchemaValidator(path))
        return comparator.compare(a, b, rules or OperationRules(), "item"), (a.body, b.body)
    return run


@pytest.mark.parametrize("served", [
    "application/problem+json; charset=UTF-8", "application/vnd.api+json", "text/json", "text/plain",
])
def test_unmatched_response_preserves_json_schema_checks(compare_media, served):
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
    result, _ = compare_media("application/json", schema, served, b'{"id":1}', b'{"id":"one"}')
    assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION
    assert {d.path for d in result.details["schema"].differences} == ({"$"} if served == "text/plain" else {"$.id"})


def test_unmatched_json_preserves_extra_field_comparison(compare_media):
    result, _ = compare_media("application/json", {"type": "object"}, "application/hal+json",
                              b'{"extra":"A"}', b'{"extra":"B"}')
    assert result.mismatch_type == MismatchType.BODY
    assert result.details["extra_fields"].differences[0].path == "$.extra"


@pytest.mark.parametrize(("schema", "good", "bad"), [
    ({"type": "integer", "minimum": 10}, "42", "9"),
    ({"oneOf": [{"type": "boolean"}, {"type": "number", "minimum": 0}]}, "true", "-1"),
    ({"type": "boolean"}, "false", "yes"),
    ({"enum": [4.5, 6]}, "4.5", "6.5"),
    ({"type": "string", "enum": ["042"]}, "042", "42"),
])
def test_declared_text_checks_scalar_constraints_and_keeps_wire_text(compare_media, schema, good, bad):
    result, bodies = compare_media("text/plain", schema, "text/plain", good.encode(), bad.encode())
    assert bodies == (good, bad)
    assert result.mismatch_type == MismatchType.SCHEMA_VIOLATION
    difference, = result.details["schema"].differences
    assert difference.target_b.startswith("<violation:")


@pytest.mark.parametrize("media", ["application/xml", "text/xml", "application/vnd.example+xml"])
def test_xml_field_rules_see_the_captured_tree(compare_media, media):
    schema = {"type": "object", "xml": {"name": "Item"}, "required": ["count"],
              "properties": {"count": {"type": "integer"}}}
    rules = OperationRules(body={"field_rules": {"$.Item.count": {"predefined": "exact_match"}}})
    result, bodies = compare_media(media, schema, media, b"<Item><count>3</count></Item>",
                                   b"<Item><count>4</count></Item>", rules)
    assert bodies == ({"Item": {"count": "3"}}, {"Item": {"count": "4"}})
    assert result.mismatch_type == MismatchType.BODY
    assert result.details["body"].differences[0].path == "$.Item.count"
