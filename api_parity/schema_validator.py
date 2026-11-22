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
from jsonschema import Draft4Validator, ValidationError, validators
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT4


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
# Helpers
# =============================================================================


def _openapi_keywords(schema: dict[str, Any]):
    """Apply OpenAPI 3.0 nullable semantics at the schema node being validated."""
    if "$ref" in schema:
        # Draft 4 ignores siblings of a reference.
        return [("$ref", schema["$ref"])]
    if schema.get("nullable") is not True:
        return schema.items()

    non_nullable = {key: value for key, value in schema.items() if key != "nullable"}
    if "type" not in non_nullable:
        return [("anyOf", [non_nullable, {"type": "null"}])]
    types = non_nullable["type"]
    if not isinstance(types, list):
        types = [types]
    non_nullable["type"] = types if "null" in types else [*types, "null"]
    return non_nullable.items()


class _DocumentReferences:
    """Resolve each local JSON pointer once per contract, including aliases."""

    def __init__(self, spec: dict):
        self.resolver = Registry().resolver_with_root(DRAFT4.create_resource(spec))
        self.targets = {}
        self.missing: set[str] = set()

    def resolve(self, schema):
        if not isinstance(schema, dict) or "$ref" not in schema:
            return schema
        ref = schema["$ref"]
        if not isinstance(ref, str):
            return schema
        if ref in self.targets:
            return self.targets[ref]

        seen = set()
        while isinstance(ref, str) and ref.startswith("#/") and ref not in seen:
            if ref in self.targets:
                schema = self.targets[ref]
                break
            seen.add(ref)
            try:
                schema = self.resolver.lookup(ref).contents
            except Unresolvable:
                self.missing.add(ref)
                schema = {}
                break
            ref = schema.get("$ref") if isinstance(schema, dict) else None
        for alias in seen:
            self.targets[alias] = schema
        return schema


def missing_response_references(spec: dict) -> list[str]:
    """Check JSON response contracts without requiring a response instance."""
    refs = _DocumentReferences(spec)
    pending = []
    for operation in build_operation_index(spec).values():
        for response in operation.get("responses", {}).values():
            response = refs.resolve(response)
            if isinstance(response, dict):
                media = response.get("content", {}).get("application/json", {})
                pending.append(media.get("schema"))

    visited = set()
    while pending:
        schema = refs.resolve(pending.pop())
        if isinstance(schema, dict) and id(schema) not in visited:
            visited.add(id(schema))
            pending.extend(DRAFT4.subresources_of(schema))
    return sorted(refs.missing)


def build_operation_index(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build operationId -> operation definition index from an OpenAPI spec.

    Scans all paths × methods once at init time so callers can do O(1) lookups
    instead of O(paths × methods) linear scans per operationId lookup.

    Args:
        spec: Parsed OpenAPI specification dict.

    Returns:
        Dict mapping operationId strings to their operation definition dicts.
    """
    index: dict[str, dict[str, Any]] = {}
    paths = spec.get("paths", {})
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method_or_key, operation in path_item.items():
            # Skip non-operation keys like 'parameters', '$ref'
            if not isinstance(operation, dict) or method_or_key.startswith("$"):
                continue
            op_id = operation.get("operationId")
            if op_id is not None:
                index[op_id] = operation
    return index


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

    def __init__(self, spec_path: Path, *, source_bytes: bytes | None = None) -> None:
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
            self.source_bytes = spec_path.read_bytes() if source_bytes is None else source_bytes
            if spec_path.suffix.lower() in (".yaml", ".yml"):
                self.snapshot_name = "openapi.yaml"
                self._spec = yaml.safe_load(self.source_bytes)
            else:
                self.snapshot_name = "openapi.json"
                self._spec = json.loads(self.source_bytes)
            if not isinstance(self._spec, dict) or not isinstance(self._spec.get("paths"), dict):
                raise ValueError("expected an OpenAPI document with a paths mapping")
        except Exception as e:
            raise SchemaExtractionError(
                f"Failed to load OpenAPI spec from '{spec_path}': {e}"
            ) from e

        # Keep local references rooted in this contract, including during replay
        # from a snapshot. An empty registry does not retrieve external documents.
        refs = _DocumentReferences(self._spec)
        self._references = refs

        def validate_ref(validator, ref, instance, schema):
            if isinstance(ref, str) and ref.startswith("#/"):
                yield from validator.descend(instance, refs.resolve(schema))
            else:
                yield from Draft4Validator.VALIDATORS["$ref"](validator, ref, instance, schema)

        validator_class = validators.create(
            meta_schema=Draft4Validator.META_SCHEMA,
            validators={**Draft4Validator.VALIDATORS, "$ref": validate_ref},
            type_checker=Draft4Validator.TYPE_CHECKER,
            id_of=Draft4Validator.ID_OF,
            applicable_validators=_openapi_keywords,
        )
        self._document_validator = validator_class(self._spec, registry=Registry())

        # Pre-build operationId -> operation dict index for O(1) lookups.
        # Without this, every _find_operation() call scans all paths × methods.
        self._operation_index: dict[str, dict[str, Any]] = build_operation_index(
            self._spec
        )

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
            validator = self._document_validator.evolve(schema=response_schema.schema)
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
            # Schema validation itself failed - usually means the schema is malformed
            # or uses unsupported JSON Schema features. We capture this as a violation
            # rather than raising, so the comparison can continue with other fields.
            violations.append(
                SchemaViolation(
                    path="$",
                    message=f"Schema validation failed for operation '{operation_id}' "
                    f"status {status_code}: {e}",
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

        # OpenAPI spec lookup order: exact code -> wildcard (2XX/3XX/etc) -> default.
        # This matches how servers route responses: most specific wins, then class-level
        # fallback, then catch-all default. Changing order would cause schema mismatches
        # when specs define both specific codes and wildcards.
        status_str = str(status_code)
        response_def = responses.get(status_str)
        if response_def is None:
            wildcard = f"{status_code // 100}XX"
            response_def = responses.get(wildcard)
        if response_def is None:
            response_def = responses.get("default")

        if response_def is None:
            return None

        # Handle $ref if present
        response_def = self._references.resolve(response_def)

        # Get content -> application/json -> schema
        if not isinstance(response_def, dict):
            return None
        content = response_def.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema")

        if schema is None:
            return None

        allows_extra = self._allows_additional_properties(self._references.resolve(schema))

        return ResponseSchema(schema=schema, allows_extra_fields=allows_extra)

    def _find_operation(self, operation_id: str) -> dict[str, Any] | None:
        """Find an operation by its operationId via pre-built index.

        Args:
            operation_id: The operationId to find.

        Returns:
            The operation definition dict, or None if not found.
        """
        return self._operation_index.get(operation_id)

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

    def _collect_properties(
        self,
        schema: dict[str, Any],
        visited: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Collect all properties from a schema, including composition branches.

        Handles allOf, anyOf, oneOf by merging properties from all branches.
        Properties from later branches override earlier ones (last-write-wins).

        Args:
            schema: The schema to collect properties from.
            visited: Set of already-visited $ref paths to detect cycles.

        Returns:
            Merged properties dict from all sources.
        """
        if not isinstance(schema, dict):
            return {}

        if visited is None:
            visited = frozenset()

        # Handle $ref
        ref = schema.get("$ref")
        if ref:
            if ref in visited:
                return {}  # Circular reference
            resolved = self._references.resolve(schema)
            if resolved != schema:
                return self._collect_properties(resolved, visited | {ref})

        properties: dict[str, Any] = {}

        # Collect from direct properties
        if "properties" in schema:
            properties.update(schema["properties"])

        # Collect from allOf branches (all must match, so merge all)
        for subschema in schema.get("allOf", []):
            properties.update(self._collect_properties(subschema, visited))

        # Collect from anyOf branches (any can match, so merge all for extra field detection)
        for subschema in schema.get("anyOf", []):
            properties.update(self._collect_properties(subschema, visited))

        # Collect from oneOf branches (one must match, so merge all for extra field detection)
        for subschema in schema.get("oneOf", []):
            properties.update(self._collect_properties(subschema, visited))

        return properties

    def _find_extra_fields(
        self,
        body: Any,
        schema: dict[str, Any],
        path: str,
    ) -> list[str]:
        """Find fields in body that are not defined in schema.

        Recursively walks the body and schema to identify extra fields.
        Handles schema composition (allOf/anyOf/oneOf) by collecting
        properties from all branches.

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

        # Resolve $ref at top level
        schema = self._references.resolve(schema)
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

        # Get defined properties from schema, including composition branches
        properties = self._collect_properties(schema)
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
