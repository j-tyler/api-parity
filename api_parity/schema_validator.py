"""Schema Validator - Validates API responses against OpenAPI schemas.

This module implements the "OpenAPI Spec as Field Authority" feature:
- Validates responses against the OpenAPI response schema for operation+status_code
- Detects schema violations (extra fields when additionalProperties: false)
- Identifies extra fields when additionalProperties: true (allowed but compared)

See ARCHITECTURE.md "OpenAPI Spec as Field Authority" and DESIGN.md
"Handling additionalProperties in Schema Validation" for design decisions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft4Validator, ValidationError


# =============================================================================
# Exceptions
# =============================================================================


class SchemaValidatorError(Exception):
    """Base class for schema validator errors."""


class SchemaExtractionError(SchemaValidatorError):
    """Error extracting schema from OpenAPI spec."""


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class SchemaViolation:
    """A single schema violation in a response.

    Attributes:
        path: JSONPath to the violating field (e.g., "$.extra_field")
        message: Human-readable description of the violation
        violation_type: Type of violation (extra_field, wrong_type, missing_required, etc.)
    """

    path: str
    message: str
    violation_type: str


@dataclass
class ValidationResult:
    """Result of validating a response against its schema.

    Attributes:
        valid: Whether the response passes schema validation
        violations: List of schema violations (empty if valid)
        extra_fields: Fields present in response but not in schema (when additionalProperties allows)
    """

    valid: bool
    violations: list[SchemaViolation] = field(default_factory=list)
    extra_fields: list[str] = field(default_factory=list)


@dataclass
class ResponseSchema:
    """Extracted response schema for an operation+status_code combination.

    Attributes:
        schema: The JSON Schema for the response body
        allows_extra_fields: True if additionalProperties is not explicitly false
    """

    schema: dict[str, Any]
    allows_extra_fields: bool


# =============================================================================
# Schema Validator
# =============================================================================


class SchemaValidator:
    """Validates API responses against OpenAPI specification schemas.

    This class extracts response schemas from an OpenAPI spec and validates
    actual responses against them. It handles the additionalProperties setting:
    - additionalProperties: false → Extra fields are schema violations
    - additionalProperties: true or unspecified → Extra fields allowed but tracked

    Usage:
        validator = SchemaValidator(spec_path)
        result = validator.validate_response(body, "createWidget", 201)
        if not result.valid:
            for violation in result.violations:
                print(f"Schema violation: {violation.message}")
    """

    def __init__(self, spec_path: Path) -> None:
        """Initialize the schema validator.

        Args:
            spec_path: Path to OpenAPI specification file (YAML or JSON).

        Raises:
            SchemaExtractionError: If spec cannot be loaded or parsed.
        """
        self._spec_path = spec_path
        self._spec: dict[str, Any] = {}
        self._schema_cache: dict[tuple[str, int], ResponseSchema | None] = {}

        try:
            with open(spec_path) as f:
                if spec_path.suffix.lower() in (".yaml", ".yml"):
                    self._spec = yaml.safe_load(f)
                else:
                    self._spec = json.load(f)
        except Exception as e:
            raise SchemaExtractionError(f"Failed to load OpenAPI spec: {e}") from e

    def validate_response(
        self,
        body: Any,
        operation_id: str,
        status_code: int,
    ) -> ValidationResult:
        """Validate a response body against its OpenAPI schema.

        Args:
            body: The response body (parsed JSON).
            operation_id: The operationId of the endpoint.
            status_code: The HTTP status code of the response.

        Returns:
            ValidationResult with validity status and any violations or extra fields.
        """
        # Get the schema for this operation+status_code
        response_schema = self._get_response_schema(operation_id, status_code)

        # No schema defined for this operation+status_code - pass validation
        if response_schema is None:
            return ValidationResult(valid=True)

        # No body to validate
        if body is None:
            # If schema expects content, this might be a violation
            # But for simplicity, we treat None body as valid (matches "no content" case)
            return ValidationResult(valid=True)

        # Validate against schema
        violations: list[SchemaViolation] = []
        extra_fields: list[str] = []

        try:
            validator = Draft4Validator(response_schema.schema)
            errors = list(validator.iter_errors(body))

            for error in errors:
                path = self._error_path_to_jsonpath(error.absolute_path)
                violation = SchemaViolation(
                    path=path,
                    message=error.message,
                    violation_type=self._classify_validation_error(error),
                )
                violations.append(violation)

        except Exception as e:
            # Schema validation itself failed (e.g., invalid schema)
            violations.append(
                SchemaViolation(
                    path="$",
                    message=f"Schema validation error: {e}",
                    violation_type="validation_error",
                )
            )

        # If schema allows extra fields, identify them for comparison
        if response_schema.allows_extra_fields and isinstance(body, dict):
            extra_fields = self._find_extra_fields(body, response_schema.schema, "$")

        return ValidationResult(
            valid=len(violations) == 0,
            violations=violations,
            extra_fields=extra_fields,
        )

    def get_extra_fields(
        self,
        body: Any,
        operation_id: str,
        status_code: int,
    ) -> list[str]:
        """Get list of extra fields present in response but not defined in schema.

        This is used to identify fields that should be compared between implementations
        even though they're not explicitly defined in the spec.

        Args:
            body: The response body (parsed JSON).
            operation_id: The operationId of the endpoint.
            status_code: The HTTP status code of the response.

        Returns:
            List of JSONPath expressions for extra fields.
        """
        response_schema = self._get_response_schema(operation_id, status_code)

        if response_schema is None or body is None:
            return []

        if not isinstance(body, dict):
            return []

        return self._find_extra_fields(body, response_schema.schema, "$")

    def has_schema(self, operation_id: str, status_code: int) -> bool:
        """Check if a schema exists for the given operation+status_code.

        Args:
            operation_id: The operationId of the endpoint.
            status_code: The HTTP status code.

        Returns:
            True if a response schema is defined.
        """
        return self._get_response_schema(operation_id, status_code) is not None

    def _get_response_schema(
        self,
        operation_id: str,
        status_code: int,
    ) -> ResponseSchema | None:
        """Get the response schema for an operation+status_code.

        Caches results for performance.

        Args:
            operation_id: The operationId to look up.
            status_code: The HTTP status code.

        Returns:
            ResponseSchema if found, None otherwise.
        """
        cache_key = (operation_id, status_code)
        if cache_key in self._schema_cache:
            return self._schema_cache[cache_key]

        schema = self._extract_response_schema(operation_id, status_code)
        self._schema_cache[cache_key] = schema
        return schema

    def _extract_response_schema(
        self,
        operation_id: str,
        status_code: int,
    ) -> ResponseSchema | None:
        """Extract the response schema from the OpenAPI spec.

        Args:
            operation_id: The operationId to look up.
            status_code: The HTTP status code.

        Returns:
            ResponseSchema if found, None otherwise.
        """
        # Find the operation by operationId
        operation = self._find_operation(operation_id)
        if operation is None:
            return None

        # Get responses for this operation
        responses = operation.get("responses", {})

        # Look for exact status code match first, then "default"
        status_str = str(status_code)
        response_def = responses.get(status_str)
        if response_def is None:
            # Try wildcard patterns (2XX, 3XX, etc.)
            wildcard = f"{status_code // 100}XX"
            response_def = responses.get(wildcard)
        if response_def is None:
            response_def = responses.get("default")

        if response_def is None:
            return None

        # Handle $ref if present
        response_def = self._resolve_ref(response_def)

        # Get content -> application/json -> schema
        content = response_def.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema")

        if schema is None:
            return None

        # Resolve $ref in schema
        schema = self._resolve_schema_refs(schema)

        # Determine if additionalProperties allows extra fields
        allows_extra = self._allows_additional_properties(schema)

        return ResponseSchema(schema=schema, allows_extra_fields=allows_extra)

    def _find_operation(self, operation_id: str) -> dict[str, Any] | None:
        """Find an operation by its operationId.

        Args:
            operation_id: The operationId to find.

        Returns:
            The operation definition dict, or None if not found.
        """
        paths = self._spec.get("paths", {})
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for method_or_key, operation in path_item.items():
                # Skip non-operation keys like 'parameters', '$ref'
                if not isinstance(operation, dict) or method_or_key.startswith("$"):
                    continue
                if operation.get("operationId") == operation_id:
                    return operation
        return None

    def _resolve_ref(self, obj: dict[str, Any]) -> dict[str, Any]:
        """Resolve a $ref reference in the spec.

        Args:
            obj: Object that may contain a $ref.

        Returns:
            The resolved object.
        """
        if not isinstance(obj, dict):
            return obj

        ref = obj.get("$ref")
        if ref is None:
            return obj

        # Parse the $ref path (e.g., "#/components/schemas/Widget")
        if not ref.startswith("#/"):
            # External refs not supported
            return obj

        parts = ref[2:].split("/")
        resolved = self._spec
        for part in parts:
            if isinstance(resolved, dict):
                resolved = resolved.get(part, {})
            else:
                return obj

        return resolved if isinstance(resolved, dict) else obj

    def _resolve_schema_refs(
        self, schema: dict[str, Any], visited: frozenset[str] | None = None
    ) -> dict[str, Any]:
        """Recursively resolve all $ref references in a schema.

        Args:
            schema: The schema to resolve.
            visited: Set of already-visited $ref paths to detect cycles.

        Returns:
            Schema with all refs resolved. Cycles are broken by returning
            the unresolved $ref to prevent infinite recursion.
        """
        if visited is None:
            visited = frozenset()

        if not isinstance(schema, dict):
            return schema

        # Handle $ref at this level
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref in visited:
                # Cycle detected - return unresolved to break recursion
                return schema
            resolved = self._resolve_ref(schema)
            # Recursively resolve nested refs with updated visited set
            return self._resolve_schema_refs(resolved, visited | {ref})

        # Recursively process nested schemas
        result = {}
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                result[key] = {
                    prop: self._resolve_schema_refs(prop_schema, visited)
                    for prop, prop_schema in value.items()
                }
            elif key == "items" and isinstance(value, dict):
                result[key] = self._resolve_schema_refs(value, visited)
            elif key in ("allOf", "anyOf", "oneOf") and isinstance(value, list):
                result[key] = [self._resolve_schema_refs(item, visited) for item in value]
            elif key == "additionalProperties" and isinstance(value, dict):
                result[key] = self._resolve_schema_refs(value, visited)
            else:
                result[key] = value

        return result

    def _allows_additional_properties(self, schema: dict[str, Any]) -> bool:
        """Check if schema allows additional properties.

        Per JSON Schema and OpenAPI defaults:
        - additionalProperties: false → Extra fields NOT allowed
        - additionalProperties: true or unspecified → Extra fields allowed

        Args:
            schema: The JSON Schema.

        Returns:
            True if additional properties are allowed.
        """
        if not isinstance(schema, dict):
            return True

        additional = schema.get("additionalProperties")

        # Explicitly false means no additional properties
        if additional is False:
            return False

        # True, unspecified, or a schema (object) means allowed
        return True

    def _find_extra_fields(
        self,
        body: Any,
        schema: dict[str, Any],
        path: str,
    ) -> list[str]:
        """Find fields in body that are not defined in schema.

        Recursively walks the body and schema to identify extra fields.

        Args:
            body: The response body (or nested part of it).
            schema: The schema (or nested part of it).
            path: Current JSONPath.

        Returns:
            List of JSONPaths for extra fields.
        """
        extra: list[str] = []

        if not isinstance(schema, dict):
            return extra

        # Handle arrays - check items in the array
        if isinstance(body, list):
            items_schema = schema.get("items", {})
            if items_schema:
                for i, item in enumerate(body):
                    item_extra = self._find_extra_fields(
                        item, items_schema, f"{path}[{i}]"
                    )
                    extra.extend(item_extra)
            return extra

        # Handle objects
        if not isinstance(body, dict):
            return extra

        # Get defined properties from schema
        properties = schema.get("properties", {})
        defined_fields = set(properties.keys())

        # Find fields in body not in schema
        for field_name in body.keys():
            if field_name not in defined_fields:
                field_path = f"{path}.{field_name}"
                extra.append(field_path)
            else:
                # Recursively check nested objects/arrays
                nested_body = body[field_name]
                nested_schema = properties.get(field_name, {})
                nested_extra = self._find_extra_fields(
                    nested_body, nested_schema, f"{path}.{field_name}"
                )
                extra.extend(nested_extra)

        return extra

    def _error_path_to_jsonpath(self, path) -> str:
        """Convert jsonschema error path to JSONPath format.

        Args:
            path: The error's absolute_path (deque of path segments).

        Returns:
            JSONPath string (e.g., "$.data.items[0].id").
        """
        if not path:
            return "$"

        parts = ["$"]
        for segment in path:
            if isinstance(segment, int):
                parts.append(f"[{segment}]")
            else:
                parts.append(f".{segment}")

        return "".join(parts)

    def _classify_validation_error(self, error: ValidationError) -> str:
        """Classify a validation error by type.

        Args:
            error: The jsonschema ValidationError.

        Returns:
            Classification string (e.g., "extra_field", "wrong_type", etc.).
        """
        validator = error.validator

        if validator == "additionalProperties":
            return "extra_field"
        elif validator == "type":
            return "wrong_type"
        elif validator == "required":
            return "missing_required"
        elif validator == "enum":
            return "invalid_enum"
        elif validator in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            return "out_of_range"
        elif validator in ("minLength", "maxLength"):
            return "invalid_length"
        elif validator == "pattern":
            return "pattern_mismatch"
        elif validator == "format":
            return "invalid_format"
        else:
            return "validation_error"
