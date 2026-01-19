"""Unit tests for SchemaValidator - OpenAPI Spec as Field Authority.

Tests schema extraction, validation, additionalProperties handling,
and extra field detection.
"""

from pathlib import Path

import pytest

from api_parity.schema_validator import (
    SchemaExtractionError,
    SchemaValidator,
    SchemaViolation,
    ValidationResult,
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


class TestEdgeCases:
    """Edge case tests for schema validation."""

    def test_none_body_passes_validation(self, validator):
        """None body passes validation (matches "no content" case)."""
        result = validator.validate_response(None, "listStrictWidgets", 200)
        assert result.valid is True

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
