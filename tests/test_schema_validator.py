"""Unit tests for SchemaValidator - OpenAPI Spec as Field Authority.

Tests schema extraction, validation, additionalProperties handling,
and extra field detection.
"""

import json
from pathlib import Path

import pytest

from api_parity.schema_validator import (
    SchemaExtractionError,
    SchemaValidator,
    SchemaViolation,
    ValidationResult,
    build_operation_index,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMA_VALIDATION_SPEC = FIXTURES_DIR / "test_api_schema_validation.yaml"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def validator() -> SchemaValidator:
    """Create a SchemaValidator with the test spec."""
    return SchemaValidator(SCHEMA_VALIDATION_SPEC)


# =============================================================================
# Schema Loading Tests
# =============================================================================


class TestSchemaLoading:
    """Tests for loading and parsing OpenAPI specs."""

    def test_load_yaml_spec(self):
        """Successfully loads a YAML OpenAPI spec."""
        validator = SchemaValidator(SCHEMA_VALIDATION_SPEC)
        assert validator is not None

    def test_load_nonexistent_file(self):
        """Raises SchemaExtractionError for nonexistent file."""
        with pytest.raises(SchemaExtractionError, match="Failed to load"):
            SchemaValidator(Path("/nonexistent/spec.yaml"))

    def test_has_schema_for_defined_operation(self, validator):
        """has_schema returns True for defined operation+status_code."""
        assert validator.has_schema("listStrictWidgets", 200) is True
        assert validator.has_schema("getWidget", 200) is True
        assert validator.has_schema("getWidget", 404) is True

    def test_has_schema_for_undefined_operation(self, validator):
        """has_schema returns False for undefined operation."""
        assert validator.has_schema("nonExistentOp", 200) is False

    def test_has_schema_for_undefined_status(self, validator):
        """has_schema returns False for undefined status code."""
        assert validator.has_schema("listStrictWidgets", 404) is False


# =============================================================================
# Schema Validation Tests - Strict Mode (additionalProperties: false)
# =============================================================================


class TestStrictSchemaValidation:
    """Tests for schemas with additionalProperties: false."""

    def test_valid_response_passes(self, validator):
        """Valid response matching schema passes validation."""
        body = {
            "widgets": [
                {"id": "123", "name": "Widget A", "price": 10.0}
            ],
            "total": 1
        }
        result = validator.validate_response(body, "listStrictWidgets", 200)
        assert result.valid is True
        assert result.violations == []

    def test_extra_field_at_root_is_violation(self, validator):
        """Extra field at root level is a schema violation."""
        body = {
            "widgets": [],
            "total": 0,
            "extra_field": "not allowed"  # Not in schema
        }
        result = validator.validate_response(body, "listStrictWidgets", 200)
        assert result.valid is False
        assert len(result.violations) >= 1
        # Find the extra_field violation
        extra_violations = [v for v in result.violations if "extra_field" in v.message]
        assert len(extra_violations) >= 1

    def test_extra_field_in_nested_object_is_violation(self, validator):
        """Extra field in nested object is a schema violation."""
        body = {
            "widgets": [
                {"id": "123", "name": "Widget", "price": 10.0, "unknown": "bad"}
            ],
            "total": 1
        }
        result = validator.validate_response(body, "listStrictWidgets", 200)
        assert result.valid is False
        violations = [v for v in result.violations if v.violation_type == "extra_field"]
        assert len(violations) >= 1

    def test_missing_required_field_is_violation(self, validator):
        """Missing required field is a schema violation."""
        body = {
            "widgets": [{"id": "123"}],  # Missing name and price
            "total": 1
        }
        result = validator.validate_response(body, "listStrictWidgets", 200)
        assert result.valid is False
        # Should have violations for missing required fields
        assert any(v.violation_type == "missing_required" for v in result.violations)

    def test_wrong_type_is_violation(self, validator):
        """Wrong type is a schema violation."""
        body = {
            "widgets": [
                {"id": "123", "name": "Widget", "price": "not a number"}  # price should be number
            ],
            "total": 1
        }
        result = validator.validate_response(body, "listStrictWidgets", 200)
        assert result.valid is False
        assert any(v.violation_type == "wrong_type" for v in result.violations)

    def test_invalid_enum_is_violation(self, validator):
        """Invalid enum value is a schema violation."""
        body = {
            "widgets": [
                {"id": "123", "name": "Widget", "price": 10.0, "category": "invalid"}
            ],
            "total": 1
        }
        result = validator.validate_response(body, "listStrictWidgets", 200)
        assert result.valid is False
        assert any(v.violation_type == "invalid_enum" for v in result.violations)


# =============================================================================
# Schema Validation Tests - Flexible Mode (additionalProperties: true)
# =============================================================================


class TestFlexibleSchemaValidation:
    """Tests for schemas with additionalProperties: true."""

    def test_valid_response_passes(self, validator):
        """Valid response matching schema passes validation."""
        body = {
            "widgets": [
                {"id": "123", "name": "Widget A"}
            ]
        }
        result = validator.validate_response(body, "listFlexibleWidgets", 200)
        assert result.valid is True

    def test_extra_fields_allowed_and_tracked(self, validator):
        """Extra fields are allowed but tracked in extra_fields list."""
        body = {
            "widgets": [
                {"id": "123", "name": "Widget A"}
            ],
            "extra_field": "allowed here",
            "another_extra": 42
        }
        result = validator.validate_response(body, "listFlexibleWidgets", 200)
        assert result.valid is True  # Extra fields are allowed
        # Extra fields should be tracked
        assert "$.extra_field" in result.extra_fields
        assert "$.another_extra" in result.extra_fields

    def test_extra_fields_in_items_tracked(self, validator):
        """Extra fields in array items are tracked."""
        body = {
            "widgets": [
                {"id": "123", "name": "Widget A", "custom": "value"}
            ]
        }
        result = validator.validate_response(body, "listFlexibleWidgets", 200)
        assert result.valid is True
        # Extra field in item should be tracked
        assert any("custom" in f for f in result.extra_fields)


# =============================================================================
# Extra Fields Detection Tests
# =============================================================================


class TestExtraFieldsDetection:
    """Tests for extra fields detection using get_extra_fields()."""

    def test_get_extra_fields_in_flexible_schema(self, validator):
        """get_extra_fields() identifies fields not in schema."""
        body = {
            "widgets": [{"id": "1", "name": "W", "unknown_field": "val"}],
            "metadata": {"key": "value"}
        }
        extra = validator.get_extra_fields(body, "listFlexibleWidgets", 200)
        # Should find $.metadata and nested unknown_field
        assert "$.metadata" in extra

    def test_no_extra_fields_when_exact_match(self, validator):
        """get_extra_fields() returns empty list when response matches schema exactly."""
        body = {"widgets": [{"id": "1", "name": "Widget"}]}
        extra = validator.get_extra_fields(body, "listFlexibleWidgets", 200)
        # No extra fields at root level
        root_extras = [f for f in extra if f.startswith("$.") and "." not in f[2:]]
        assert len(root_extras) == 0

    def test_no_schema_returns_empty_extra_fields(self, validator):
        """get_extra_fields() returns empty list when no schema exists."""
        body = {"anything": "goes"}
        extra = validator.get_extra_fields(body, "nonExistentOp", 200)
        assert extra == []


# =============================================================================
# Nested Object Validation Tests
# =============================================================================


class TestNestedObjectValidation:
    """Tests for nested object validation."""

    def test_valid_nested_object(self, validator):
        """Valid nested object passes validation."""
        body = {
            "id": "user-123",
            "username": "testuser",
            "settings": {
                "theme": "dark",
                "notifications": True
            }
        }
        result = validator.validate_response(body, "getUserProfile", 200)
        assert result.valid is True

    def test_invalid_nested_enum(self, validator):
        """Invalid enum in nested object is a violation."""
        body = {
            "id": "user-123",
            "username": "testuser",
            "settings": {
                "theme": "rainbow",  # Invalid enum
            }
        }
        result = validator.validate_response(body, "getUserProfile", 200)
        assert result.valid is False

    def test_extra_field_in_nested_strict_object(self, validator):
        """Extra field in nested object with additionalProperties: false is violation."""
        body = {
            "id": "user-123",
            "username": "testuser",
            "settings": {
                "theme": "dark",
                "extra_setting": "not allowed"  # Not in schema
            }
        }
        result = validator.validate_response(body, "getUserProfile", 200)
        assert result.valid is False
        assert "$.settings.extra_setting" not in result.extra_fields
        assert "$.settings.extra_setting" in validator.get_extra_fields(body, "getUserProfile", 200)


# =============================================================================
# Edge Cases
# =============================================================================


class TestSchemaComposition:
    """Tests for schema composition handling (allOf/anyOf/oneOf).

    Regression tests for Bug 2: Schema composition not handled in _find_extra_fields.
    """

    @pytest.fixture
    def allof_spec_path(self, tmp_path):
        """Create a spec with allOf schema composition."""
        spec_content = """
openapi: "3.0.0"
info:
  title: Test API with allOf
  version: "1.0"
paths:
  /items:
    get:
      operationId: getItem
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                allOf:
                  - type: object
                    properties:
                      id:
                        type: string
                  - type: object
                    properties:
                      name:
                        type: string
                      status:
                        type: string
                        enum: [active, inactive]
"""
        spec_path = tmp_path / "allof_spec.yaml"
        spec_path.write_text(spec_content)
        return spec_path

    def test_allof_properties_recognized(self, allof_spec_path):
        """Properties from allOf branches are recognized as defined."""
        validator = SchemaValidator(allof_spec_path)

        # Response with fields defined in allOf branches
        body = {
            "id": "123",
            "name": "Test Item",
            "status": "active"
        }

        # These fields should NOT be extra - they're defined in allOf branches
        extra_fields = validator.get_extra_fields(body, "getItem", 200)
        assert "$.id" not in extra_fields
        assert "$.name" not in extra_fields
        assert "$.status" not in extra_fields

    def test_allof_extra_field_detected(self, allof_spec_path):
        """Extra fields not in any allOf branch are still detected."""
        validator = SchemaValidator(allof_spec_path)

        body = {
            "id": "123",
            "name": "Test",
            "status": "active",
            "unknown_field": "should be extra"
        }

        extra_fields = validator.get_extra_fields(body, "getItem", 200)
        assert "$.unknown_field" in extra_fields

    @pytest.fixture
    def anyof_spec_path(self, tmp_path):
        """Create a spec with anyOf schema composition."""
        spec_content = """
openapi: "3.0.0"
info:
  title: Test API with anyOf
  version: "1.0"
paths:
  /items:
    get:
      operationId: getItem
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                anyOf:
                  - type: object
                    properties:
                      type_a_field:
                        type: string
                  - type: object
                    properties:
                      type_b_field:
                        type: integer
"""
        spec_path = tmp_path / "anyof_spec.yaml"
        spec_path.write_text(spec_content)
        return spec_path

    def test_anyof_properties_recognized(self, anyof_spec_path):
        """Properties from anyOf branches are recognized as defined."""
        validator = SchemaValidator(anyof_spec_path)

        # Response could match either branch - both fields should be recognized
        body = {"type_a_field": "value"}
        extra_fields = validator.get_extra_fields(body, "getItem", 200)
        assert "$.type_a_field" not in extra_fields

        body = {"type_b_field": 42}
        extra_fields = validator.get_extra_fields(body, "getItem", 200)
        assert "$.type_b_field" not in extra_fields

    @pytest.fixture
    def oneof_spec_path(self, tmp_path):
        """Create a spec with oneOf schema composition."""
        spec_content = """
openapi: "3.0.0"
info:
  title: Test API with oneOf
  version: "1.0"
paths:
  /items:
    get:
      operationId: getItem
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                oneOf:
                  - type: object
                    properties:
                      dog_breed:
                        type: string
                  - type: object
                    properties:
                      cat_breed:
                        type: string
"""
        spec_path = tmp_path / "oneof_spec.yaml"
        spec_path.write_text(spec_content)
        return spec_path

    def test_oneof_properties_recognized(self, oneof_spec_path):
        """Properties from oneOf branches are recognized as defined."""
        validator = SchemaValidator(oneof_spec_path)

        body = {"dog_breed": "labrador"}
        extra_fields = validator.get_extra_fields(body, "getItem", 200)
        assert "$.dog_breed" not in extra_fields


class TestNullableHandling:
    """Tests for OpenAPI 3.0 nullable: true handling.

    Regression tests for: Draft4Validator does not understand the OpenAPI 3.0
    "nullable: true" keyword, causing false schema violations when a field
    is validly null per the spec.
    """

    @pytest.fixture
    def nullable_spec_path(self, tmp_path):
        """Create a spec with nullable fields in various locations."""
        spec_content = """
openapi: "3.0.3"
info:
  title: Test API with nullable fields
  version: "1.0"
paths:
  /accounts:
    get:
      operationId: getAccounts
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                type: object
                required: [id, name]
                properties:
                  id:
                    type: string
                  name:
                    type: string
                  replicationPolicy:
                    type: string
                    nullable: true
                  cacheTtlInSecond:
                    type: integer
                    nullable: true
                  migrationConfig:
                    type: object
                    nullable: true
                    properties:
                      source:
                        type: string
  /nested:
    get:
      operationId: getNested
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                type: object
                properties:
                  outer:
                    type: object
                    properties:
                      inner_nullable:
                        type: string
                        nullable: true
                      inner_required:
                        type: string
  /array-items:
    get:
      operationId: getArrayItems
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                type: object
                properties:
                  items:
                    type: array
                    items:
                      type: object
                      properties:
                        value:
                          type: number
                          nullable: true
  /composed:
    get:
      operationId: getComposed
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                allOf:
                  - type: object
                    properties:
                      base_field:
                        type: string
                  - type: object
                    properties:
                      nullable_field:
                        type: string
                        nullable: true
  /nullable-composition:
    get:
      operationId: getNullableComposition
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                type: object
                properties:
                  config:
                    nullable: true
                    allOf:
                      - type: object
                        properties:
                          key:
                            type: string
"""
        spec_path = tmp_path / "nullable_spec.yaml"
        spec_path.write_text(spec_content)
        return spec_path

    def test_null_value_in_nullable_string_field_passes(self, nullable_spec_path):
        """Null value in a nullable: true string field should not be a violation."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "id": "acc-1",
            "name": "Test",
            "replicationPolicy": None,
        }
        result = validator.validate_response(body, "getAccounts", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_null_value_in_nullable_integer_field_passes(self, nullable_spec_path):
        """Null value in a nullable: true integer field should not be a violation."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "id": "acc-1",
            "name": "Test",
            "cacheTtlInSecond": None,
        }
        result = validator.validate_response(body, "getAccounts", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_null_value_in_nullable_object_field_passes(self, nullable_spec_path):
        """Null value in a nullable: true object field should not be a violation."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "id": "acc-1",
            "name": "Test",
            "migrationConfig": None,
        }
        result = validator.validate_response(body, "getAccounts", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_all_nullable_fields_null_passes(self, nullable_spec_path):
        """All nullable fields set to null at once should pass validation."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "id": "acc-1",
            "name": "Test",
            "replicationPolicy": None,
            "cacheTtlInSecond": None,
            "migrationConfig": None,
        }
        result = validator.validate_response(body, "getAccounts", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_wrong_type_in_nullable_field_still_fails(self, nullable_spec_path):
        """A non-null wrong type in a nullable field should still be a violation."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "id": "acc-1",
            "name": "Test",
            "replicationPolicy": 12345,  # should be string or null, not int
        }
        result = validator.validate_response(body, "getAccounts", 200)
        assert result.valid is False
        assert any(v.violation_type == "wrong_type" for v in result.violations)

    def test_non_null_valid_value_in_nullable_field_passes(self, nullable_spec_path):
        """A valid non-null value in a nullable field should pass."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "id": "acc-1",
            "name": "Test",
            "replicationPolicy": "some-policy",
            "cacheTtlInSecond": 300,
        }
        result = validator.validate_response(body, "getAccounts", 200)
        assert result.valid is True

    def test_nullable_in_nested_object(self, nullable_spec_path):
        """Nullable field inside a nested object should accept null."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "outer": {
                "inner_nullable": None,
                "inner_required": "present",
            }
        }
        result = validator.validate_response(body, "getNested", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_nullable_in_array_items(self, nullable_spec_path):
        """Nullable field inside array items should accept null."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "items": [
                {"value": 1.5},
                {"value": None},
                {"value": 3.0},
            ]
        }
        result = validator.validate_response(body, "getArrayItems", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_nullable_in_allof_composition(self, nullable_spec_path):
        """Nullable field inside allOf composition should accept null."""
        validator = SchemaValidator(nullable_spec_path)
        body = {
            "base_field": "hello",
            "nullable_field": None,
        }
        result = validator.validate_response(body, "getComposed", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_nullable_composition_property_accepts_null(self, nullable_spec_path):
        """Property with nullable: true + allOf (no direct type) should accept null.

        Regression: nullable without a 'type' key (e.g., nullable wrapping a $ref
        or allOf) was not converted, causing false 'None is not of type object'.
        """
        validator = SchemaValidator(nullable_spec_path)
        body = {"config": None}
        result = validator.validate_response(body, "getNullableComposition", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_nullable_composition_property_accepts_valid_object(self, nullable_spec_path):
        """Property with nullable: true + allOf should still accept a valid object."""
        validator = SchemaValidator(nullable_spec_path)
        body = {"config": {"key": "value"}}
        result = validator.validate_response(body, "getNullableComposition", 200)
        assert result.valid is True, (
            f"Expected valid but got violations: {[v.message for v in result.violations]}"
        )

    def test_nullable_composition_property_rejects_wrong_type(self, nullable_spec_path):
        """Property with nullable: true + allOf should reject a wrong type."""
        validator = SchemaValidator(nullable_spec_path)
        body = {"config": "not an object"}
        result = validator.validate_response(body, "getNullableComposition", 200)
        assert result.valid is False


class TestEdgeCases:
    """Edge case tests for schema validation."""

    def test_none_body_passes_validation(self, validator):
        """None body passes validation (matches "no content" case)."""
        result = validator.validate_response(None, "listStrictWidgets", 200)
        assert result.valid is True

    def test_explicit_null_is_validated_against_the_schema(self, validator):
        result = validator.validate_response(None, "listStrictWidgets", 200, json_body_state="null")
        assert [(v.path, v.violation_type) for v in result.violations] == [("$", "wrong_type")]

    def test_empty_object_missing_required(self, validator):
        """Empty object fails when required fields are missing."""
        result = validator.validate_response({}, "listStrictWidgets", 200)
        assert result.valid is False
        assert any(v.violation_type == "missing_required" for v in result.violations)

    def test_validation_result_has_path_information(self, validator):
        """Violations include path information."""
        body = {
            "widgets": [
                {"id": 123, "name": "Widget", "price": 10.0}  # id should be string
            ],
            "total": 1
        }
        result = validator.validate_response(body, "listStrictWidgets", 200)
        assert result.valid is False
        # Check that path is present
        assert any("$" in v.path for v in result.violations)

    def test_validation_caches_schemas(self, validator):
        """Schema extraction is cached for repeated calls."""
        # Call twice to use cache
        validator.validate_response({"widgets": [], "total": 0}, "listStrictWidgets", 200)
        validator.validate_response({"widgets": [], "total": 0}, "listStrictWidgets", 200)
        # Cache key should exist
        assert ("listStrictWidgets", 200) in validator._schema_cache


# =============================================================================
# Operation Index Tests
# =============================================================================


class TestBuildOperationIndex:
    """Tests for build_operation_index helper.

    This helper builds an operationId -> operation dict at init time so callers
    can do O(1) lookups instead of scanning all paths × methods each time.
    """

    def test_builds_index_from_spec(self):
        """Index maps each operationId to its operation definition."""
        spec = {
            "paths": {
                "/users": {
                    "get": {"operationId": "listUsers", "summary": "List users"},
                    "post": {"operationId": "createUser", "summary": "Create user"},
                },
                "/items": {
                    "get": {"operationId": "listItems", "summary": "List items"},
                },
            },
        }
        index = build_operation_index(spec)
        assert len(index) == 3
        assert index["listUsers"]["summary"] == "List users"
        assert index["createUser"]["summary"] == "Create user"
        assert index["listItems"]["summary"] == "List items"

    def test_operations_without_operation_id_excluded(self):
        """Operations without an operationId key are not indexed."""
        spec = {
            "paths": {
                "/users": {
                    "get": {"operationId": "listUsers"},
                    "post": {"summary": "No operationId here"},
                },
            },
        }
        index = build_operation_index(spec)
        assert len(index) == 1
        assert "listUsers" in index

    def test_skips_non_operation_keys(self):
        """Non-operation keys like 'parameters' and '$ref' are skipped."""
        spec = {
            "paths": {
                "/users": {
                    "parameters": [{"name": "id", "in": "path"}],
                    "$ref": "#/components/pathItems/Users",
                    "get": {"operationId": "listUsers"},
                },
            },
        }
        index = build_operation_index(spec)
        assert len(index) == 1
        assert "listUsers" in index

    def test_empty_paths(self):
        """Empty paths dict produces empty index."""
        assert build_operation_index({"paths": {}}) == {}
        assert build_operation_index({}) == {}

    def test_non_dict_path_item_skipped(self):
        """Non-dict path items (e.g., $ref string) are skipped."""
        spec = {
            "paths": {
                "/users": "not-a-dict",
                "/items": {
                    "get": {"operationId": "listItems"},
                },
            },
        }
        index = build_operation_index(spec)
        assert len(index) == 1
        assert "listItems" in index

    def test_validator_uses_index_for_find_operation(self):
        """SchemaValidator._find_operation uses the pre-built index."""
        validator = SchemaValidator(SCHEMA_VALIDATION_SPEC)
        # The fixture spec has operationIds - verify _find_operation returns them
        op = validator._find_operation("listStrictWidgets")
        assert op is not None
        assert op["operationId"] == "listStrictWidgets"

        # Non-existent operationId returns None
        assert validator._find_operation("nonExistent") is None


def test_recursive_response_schema_checks_each_instance_level(tmp_path):
    node_ref = {"$ref": "#/components/schemas/Node"}
    node = {"type": "object", "required": ["id"], "properties": {
        "id": {"type": "integer"},
        "label": {"type": "string", "nullable": True},
        "child": node_ref,
        "branches": {"type": "array", "items": {"$ref": "#/components/schemas/Branch"}},
    }}
    branch = {"type": "object", "properties": {"node": node_ref}}
    spec = {"openapi": "3.0.3", "components": {"schemas": {"Node": node, "Branch": branch}},
            "paths": {"/node": {"get": {"operationId": "getNode", "responses": {
                "200": {"content": {"application/json": {"schema": node_ref}}}}}}}}
    validator = SchemaValidator(tmp_path / "openapi.json", source_bytes=json.dumps(spec).encode())
    body = {"id": 1, "child": {"id": 2, "label": None},
            "branches": [{"node": {"id": 3, "extra": True}}]}
    result = validator.validate_response(body, "getNode", 200)
    assert result.valid, result.violations
    assert result.extra_fields == ["$.branches[0].node.extra"]
    assert validator.get_extra_fields(body, "getNode", 200) == result.extra_fields

    body["branches"][0]["node"]["id"] = "bad"
    result = validator.validate_response(body, "getNode", 200)
    assert [(v.path, v.violation_type) for v in result.violations] == [
        ("$.branches[0].node.id", "wrong_type")]


def test_dangling_pointer_preserves_sibling_validation_and_is_reported_by_lint(tmp_path):
    from api_parity.spec_linter import SpecLinter

    missing = "#/components/schemas/Missing"
    node = {"type": "object", "properties": {
        "id": {"$ref": "#/components/schemas/Item~1Id%20Type"},
        "meta": {"$ref": missing}, "other": {"$ref": missing},
        "child": {"$ref": "#/components/schemas/Node"}}}
    spec = {"openapi": "3.0.3", "components": {"schemas": {
        "Node": node, "Item/Id Type": {"type": "integer"}}},
        "paths": {"/node": {"get": {"operationId": "getNode", "responses": {"200": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}
        }}}}}}
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(spec))
    validator = SchemaValidator(path)
    assert validator.validate_response({"id": 1, "meta": {}}, "getNode", 200).valid
    result = validator.validate_response({"child": {"id": "bad", "meta": 1}}, "getNode", 200)
    assert [(v.path, v.violation_type) for v in result.violations] == [("$.child.id", "wrong_type")]
    messages = [m.message for m in SpecLinter(path).lint().errors if m.code == "missing_response_reference"]
    assert messages == [f"Missing response reference {missing}"]


def test_schema_selection_uses_most_specific_matching_content(tmp_path):
    def declared(kind):
        return {"schema": {"type": kind}}

    content = {
        "*/*": declared("boolean"),
        "text/*": declared("string"),
        "text/plain": declared("integer"),
        'TEXT/PLAIN; profile="A;B"; charset=utf-8': declared("array"),
        "text/html": {},
        "application/json": {"schema": {"type": "object", "properties": {"id": {}}}},
    }
    spec = {"paths": {"/x": {"get": {"operationId": "x", "responses": {
        "2XX": {"$ref": "#/components/responses/Success"},
        "204": {"description": "No Content"},
        "400": {"content": {"application/json": declared("string")}},
    }}}}, "components": {"responses": {"Success": {"content": content}}}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    validator = SchemaValidator(path)
    for media, valid, invalid in [
        ('text/plain; charset=UTF-8; profile="A;B"', [], 1),
        ('text/plain; charset=utf-8; profile="a;b"', 1, []),
        ("Text/Plain; charset=UTF-8", 1, "no"),
        ("text/csv", "yes", 1),
        ("image/example", True, 1),
    ]:
        assert validator.validate_response(valid, "x", 200, media).valid
        assert not validator.validate_response(invalid, "x", 200, media).valid

    assert validator.get_extra_fields({"id": 1, "extra": 2}, "x", 200) == ["$.extra"]
    assert validator.get_extra_fields({"id": 1}, "x", 200, "text/html") == []
    assert validator.has_schema("x", 400, "application/vnd.other+json")
    assert not validator.has_schema("x", 200, "text/html")
    assert not validator.has_schema("x", 204, "application/json")


def response_contract(tmp_path, version, schema, components=None, **settings):
    spec = {"openapi": version, "components": {"schemas": components or {}}, **settings,
            "paths": {"/item": {"get": {"operationId": "read", "responses": {
                "200": {"content": {"application/json": {"schema": schema}}}
            }}}}}
    return SchemaValidator(tmp_path / "spec.json", source_bytes=json.dumps(spec).encode())


def test_openapi31_checks_constraints_at_each_reference_and_lists_sibling_properties(tmp_path):
    schema = {"$ref": "#/components/schemas/Alias", "required": ["state"],
              "properties": {"state": {"const": "ready"}}}
    components = {"Alias": {"$ref": "#/components/schemas/Base", "properties": {"count": {"exclusiveMinimum": 1}}},
                  "Base": {"properties": {"id": {"type": "integer"}}}}
    validator = response_contract(tmp_path, "3.1.0", schema, components)
    def check(body):
        return validator.validate_response(body, "read", 200)
    good = {"id": 1, "state": "ready", "count": 2, "extra": 0}
    assert check(good).valid
    assert check(good).extra_fields == ["$.extra"]
    bad = check({**good, "id": "one", "state": "bad", "count": 1})
    assert {(v.path, v.violation_type) for v in bad.violations} == {
        ("$.state", "invalid_const"), ("$.count", "out_of_range"), ("$.id", "wrong_type")}


@pytest.mark.parametrize(("version", "valid"), [("3.0.3", True), ("3.1.0", False)])
def test_nullable_keyword_is_specific_to_openapi30(tmp_path, version, valid):
    validator = response_contract(tmp_path, version, {"type": "string", "nullable": True})
    assert validator.validate_response(None, "read", 200, json_body_state="null").valid is valid


def test_openapi30_write_only_requirements_follow_instance_location(tmp_path):
    # A shared component supplies required names. Only one use marks input as write-only.
    components = {"Names": {"required": ["id", "input"]}, "Secret": {"type": "string", "writeOnly": True}}
    item = {"allOf": [{"$ref": "#/components/schemas/Names"}, {"properties": {
        "id": {"type": "integer", "readOnly": True}, "input": {"$ref": "#/components/schemas/Secret"}}}]}
    schema = {"properties": {"rows": {"type": "array", "items": item},
                             "ordinary": {"$ref": "#/components/schemas/Names"}}}
    validator = response_contract(tmp_path, "3.0.3", schema, components)
    def check(rows, ordinary):
        return validator.validate_response({"rows": rows, "ordinary": ordinary}, "read", 200)
    assert check([{"id": 1}], {"id": 2, "input": "visible"}).valid
    result = check([{}, {"id": 1, "input": 0}], {"id": 2})
    assert {(v.path, v.violation_type) for v in result.violations} == {
        ("$.rows[0]", "missing_required"), ("$.rows[1].input", "wrong_type"), ("$.ordinary", "missing_required")}


def test_response_dialect_override_and_unrecognized_dialect(tmp_path):
    draft4 = "http://json-schema.org/draft-04/schema#"
    base = "https://spec.openapis.org/oas/3.1/dialect/base"
    for schema, default, expected in [
        ({"const": "ready"}, draft4, True),
        ({"$schema": base, "const": "ready"}, draft4, False),
        ({"$schema": draft4, "const": "ready"}, base, True),
    ]:
        validator = response_contract(tmp_path, "3.1.0", schema, jsonSchemaDialect=default)
        assert validator.validate_response("bad", "read", 200).valid is expected
    validator = response_contract(tmp_path, "3.1.0", {"$schema": "urn:unknown"})
    violation, = validator.validate_response("bad", "read", 200).violations
    assert "Unsupported response schema dialect: urn:unknown" in violation.message


def test_openapi31_lint_visits_tuple_positions_and_ref_siblings(tmp_path):
    from api_parity.schema_validator import missing_response_references

    missing = "#/components/schemas/Missing"
    schema = {"$ref": "#/components/schemas/Base", "prefixItems": [{"$ref": missing}, {"const": 42}]}
    validator = response_contract(tmp_path, "3.1.0", schema, {"Base": {"type": "array"}})
    result = validator.validate_response(["ignored", 43], "read", 200)
    assert [(v.path, v.violation_type) for v in result.violations] == [("$[1]", "invalid_const")]
    assert missing_response_references(json.loads(validator.source_bytes)) == [missing]


def test_openapi31_embedded_resource_resolves_its_own_definitions(tmp_path):
    from api_parity.schema_validator import missing_response_references

    row = {"properties": {"state": {"const": "ready"}}}
    schema = {"$id": "urn:parity:rows", "$defs": {"Row": row},
              "prefixItems": [{"$ref": "#/$defs/Row"}], "items": {"type": "string"}}
    validator = response_contract(tmp_path, "3.1.0", {"$ref": "#/components/schemas/Rows"}, {"Rows": schema})
    good = validator.validate_response([{"state": "ready", "extra": 1}, "text"], "read", 200)
    assert good.valid and good.extra_fields == ["$[0].extra"]
    result = validator.validate_response([{"state": "bad"}, 0], "read", 200)
    assert {(v.path, v.violation_type) for v in result.violations} == {
        ("$[0].state", "invalid_const"), ("$[1]", "wrong_type")}
    assert missing_response_references(json.loads(validator.source_bytes)) == []
