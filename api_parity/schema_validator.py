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
import math
import re
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from jsonschema import Draft4Validator, Draft201909Validator, Draft202012Validator, ValidationError, validators
from referencing import Registry
from referencing.exceptions import PointerToNowhere, Unresolvable
from referencing.jsonschema import DRAFT4, DRAFT202012, specification_with

from api_parity.models import JsonBodyState, response_body_format


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


FieldLocation = tuple[str | int, ...]
_SIMPLE_FIELD = re.compile(r"[A-Za-z_@][A-Za-z_@0-9-]*")


def field_path(location: FieldLocation, *, wildcard_indices: bool = False) -> str:
    """Format a concrete JSON location for a public diagnostic or group key."""
    parts = ["$"]
    for part in location:
        if isinstance(part, int):
            parts.append("[*]" if wildcard_indices else f"[{part}]")
        elif _SIMPLE_FIELD.fullmatch(part) and part not in {"where", "wherenot"}:
            parts.append("." + part)
        else:
            escaped = part.replace("\\", "\\\\").replace("'", "\\'")
            parts.append(f"['{escaped}']")
    return "".join(parts)


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

    schema: dict[str, Any] | bool
    allows_extra_fields: bool
    media_range: str = "application/json"

    def validation_format(self, content_type: str | None) -> str:
        # The legacy JSON fallback checks the captured value as-is.
        if self.media_range == "application/json":
            return "json"
        return response_body_format(content_type or "application/json")


# =============================================================================
# Helpers
# =============================================================================


# Indexed by declared media range, with parameter-specific variants first.
_ResponseContent = dict[str, list[tuple[dict[str, str], ResponseSchema | None]]]


def _media_parameters(content_type: str) -> dict[str, str]:
    """Read quoted parameter values, which may contain semicolons."""
    if ";" not in content_type:
        return {}
    message = Message()
    message["Content-Type"] = content_type
    return {name.lower(): value.lower() if name.lower() == "charset" else value
            for name, value in message.get_params()[1:] if isinstance(value, str)}


def _expects_text_scalar(error: ValidationError, validator, value: int | float | bool) -> bool:
    """A failed scalar type or enum can identify a typed text representation."""
    if error.validator == "type":
        types = error.validator_value
        if isinstance(types, str):
            types = [types]
        return any(t in ("integer", "number", "boolean") and validator.is_type(value, t) for t in types)
    if error.validator in ("enum", "const"):
        return validator.evolve(schema={error.validator: error.validator_value}).is_valid(value)
    return any(_expects_text_scalar(child, validator, value) for child in error.context)


def _text_scalar_errors(validator, body: str, errors: list[ValidationError]) -> list[ValidationError]:
    # Preserve the literal text whenever it satisfies the schema. Parse only
    # numbers and booleans, including for referenced and composed scalar schemas.
    token = body.strip()
    if not token or (token[0] not in "-0123456789" and token not in ("true", "false")):
        return errors
    try:
        value = json.loads(token)
    except ValueError:
        return errors
    if isinstance(value, float) and not math.isfinite(value):
        return errors
    if any(_expects_text_scalar(error, validator, value) for error in errors):
        return list(validator.iter_errors(value))
    return errors


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


_DOCUMENT_URI = "https://api-parity.invalid/openapi"  # Local base for resolving relative schema IDs.
_OAS31_DIALECT = "https://spec.openapis.org/oas/3.1/dialect/base"


def _json_schema_draft(uri: str):
    if uri == _OAS31_DIALECT:
        return Draft202012Validator
    return validators.validator_for({"$schema": uri}, default=None)


_BOUND_PAIRS = (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum"))


def _oas_schema_keywords(schema):
    """Accept legacy spellings in the OAS dialect while preserving adjacent constraints."""
    if schema.get("nullable") is not True and not any(isinstance(schema.get(k), bool) for k, _ in _BOUND_PAIRS):
        return schema.items()
    keywords = dict(schema)
    if keywords.pop("nullable", False) is True:
        if "type" in keywords:
            types = keywords["type"]
            types = types if isinstance(types, list) else [types]
            keywords["type"] = types if "null" in types else [*types, "null"]
        else:
            # Legacy nullable compositions admit null. Keep const, enum, and
            # other independent assertions at the original schema location.
            branches = {k: keywords.pop(k) for k in ("allOf", "anyOf", "oneOf") if k in keywords}
            if branches:
                keywords["anyOf"] = [branches, {"type": "null"}]
    for exclusive, bound in _BOUND_PAIRS:
        if isinstance(keywords.get(exclusive), bool):
            enabled = keywords.pop(exclusive)
            if enabled and bound in keywords:
                keywords[exclusive] = keywords.pop(bound)
    return keywords.items()


@dataclass
class ResponseContractChecks:
    missing: set[str] = field(default_factory=set)
    unsupported: set[str] = field(default_factory=set)
    legacy: set[str] = field(default_factory=set)
    invalid_bounds: set[tuple[str, str]] = field(default_factory=set)

    def errors(self):
        for ref in sorted(self.missing):
            yield "missing_response_reference", f"Missing response reference {ref}"
        yield from self.dialect_errors()

    def dialect_errors(self):
        for keyword, dialect in sorted(self.invalid_bounds):
            yield "invalid_response_bound", f"{keyword} must be a number in response schema dialect {dialect}"
        for uri in sorted(self.unsupported):
            yield "unsupported_response_dialect", f"Unsupported response schema dialect: {uri}"

    def warnings(self):
        for keyword in sorted(self.legacy):
            yield "legacy_response_keyword", f"Response schema uses the OpenAPI 3.0 spelling of {keyword}."


class _DocumentReferences:
    """Resolve each local JSON pointer once per contract, including aliases."""

    def __init__(self, spec: dict):
        self.uses_json_schema_dialect = str(spec.get("openapi", "")).startswith(("3.1.", "3.2."))
        self.dialect = spec.get("jsonSchemaDialect", _OAS31_DIALECT) if self.uses_json_schema_dialect else None
        # OpenAPI is the containing document for local fragment references.
        specification = DRAFT202012 if self.uses_json_schema_dialect else DRAFT4
        self.registry = Registry().with_resource(_DOCUMENT_URI, specification.create_resource(spec))
        self.scopes = {}
        self.targets = {}
        self.missing: set[str] = set()
        self.operations = build_operation_index(spec)
        self.response_roots = list(self.response_schemas()) if self.uses_json_schema_dialect else []
        if self.uses_json_schema_dialect:
            roots = list(spec.get("components", {}).get("schemas", {}).values())
            roots.extend(self.response_roots)
            self._index_schema_resources(roots)

    def response_schemas(self):
        for operation in self.operations.values():
            for response in operation.get("responses", {}).values():
                response = self.resolve(response)
                if isinstance(response, dict):
                    for media in response.get("content", {}).values():
                        if isinstance(media, dict) and "schema" in media:
                            yield media["schema"]

    def _index_schema_resources(self, roots):
        # The registry knows JSON Schema keywords, but paths and components are
        # OpenAPI containers. Enter each embedded schema with its own dialect and base.
        pending = [(schema, _DOCUMENT_URI, self.dialect) for schema in roots]
        while pending:
            schema, base, dialect = pending.pop()
            if not isinstance(schema, dict) or id(schema) in self.scopes:
                continue
            dialect = schema.get("$schema", dialect)
            draft = _json_schema_draft(dialect)
            if draft is not None:
                specification = specification_with(draft.ID_OF(draft.META_SCHEMA))
                resource = specification.create_resource(schema)
                if resource.id():
                    base = urljoin(base, resource.id())
                    self.registry = self.registry.with_resource(base, resource)
                pending.extend((child, base, dialect) for child in specification.subresources_of(schema))
            self.scopes[id(schema)] = (base, dialect)

    def scope(self, schema):
        scope = self.scopes.get(id(schema))
        if scope is not None:
            return scope
        dialect = schema.get("$schema", self.dialect) if self.uses_json_schema_dialect and isinstance(schema, dict) else self.dialect
        return _DOCUMENT_URI, dialect

    def ref_siblings_apply(self, schema):
        if id(schema) not in self.scopes:
            return False
        _, uri = self.scope(schema)
        return _json_schema_draft(uri) in (Draft201909Validator, Draft202012Validator)

    def resolve(self, schema):
        if not isinstance(schema, dict) or "$ref" not in schema:
            return schema
        ref = schema["$ref"]
        if not isinstance(ref, str):
            return schema
        seen = set()
        while isinstance(ref, str) and ref.startswith("#/"):
            base, _ = self.scope(schema)
            key = (base, ref)
            if key in self.targets:
                schema = self.targets[key]
                break
            if key in seen:
                break
            seen.add(key)
            try:
                schema = self.registry.resolver(base).lookup(ref).contents
            except Unresolvable:
                self.missing.add(ref)
                schema = {}
                break
            # OpenAPI 3.1 schema references keep adjacent validation keywords.
            if isinstance(schema, dict) and "$ref" in schema and len(schema) > 1 and self.ref_siblings_apply(schema):
                break
            ref = schema.get("$ref") if isinstance(schema, dict) else None
        for key in seen:
            self.targets[key] = schema
        return schema

    def check_responses(self):
        checks = ResponseContractChecks()
        pending = list(self.response_roots if self.uses_json_schema_dialect else self.response_schemas())
        visited = set()
        while pending:
            schema = pending.pop()
            if id(schema) in visited:
                continue
            visited.add(id(schema))
            specification = DRAFT4
            if self.uses_json_schema_dialect:
                base, dialect = self.scope(schema)
                draft = _json_schema_draft(dialect)
                if draft is None:
                    checks.unsupported.add(dialect)
                    continue
                specification = specification_with(draft.ID_OF(draft.META_SCHEMA))
            if not isinstance(schema, dict):
                continue
            if self.uses_json_schema_dialect and dialect != _OAS31_DIALECT and "exclusiveMinimum" in draft.VALIDATORS:
                checks.invalid_bounds.update((k, dialect) for k, _ in _BOUND_PAIRS if isinstance(schema.get(k), bool))
            if self.uses_json_schema_dialect and dialect == _OAS31_DIALECT:
                if schema.get("nullable") is True:
                    checks.legacy.add("nullable")
                checks.legacy.update(k for k, _ in _BOUND_PAIRS if isinstance(schema.get(k), bool))
            ref_keywords = ("$ref", "$dynamicRef", "$recursiveRef") if self.uses_json_schema_dialect else ("$ref",)
            for keyword in ref_keywords:
                ref = schema.get(keyword)
                if not isinstance(ref, str):
                    continue
                if self.uses_json_schema_dialect:
                    try:
                        pending.append(self.registry.resolver(base).lookup(ref).contents)
                    except Unresolvable:
                        if ref.startswith("#/"):
                            checks.missing.add(ref)
                elif ref.startswith("#/"):
                    pending.append(self.resolve(schema))
            if "$ref" not in schema or self.ref_siblings_apply(schema):
                pending.extend(specification.subresources_of(schema))
        checks.missing.update(self.missing)
        return checks


def check_response_contract(spec: dict) -> ResponseContractChecks:
    return _DocumentReferences(spec).check_responses()


def missing_response_references(spec: dict) -> list[str]:
    return sorted(check_response_contract(spec).missing)


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
        self._schema_cache: dict[tuple[str, int], _ResponseContent] = {}

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
        if refs.uses_json_schema_dialect:
            errors = [message for _, message in refs.check_responses().dialect_errors()]
            if errors:
                raise SchemaExtractionError("\n".join(errors))

        self._scoped_ref_validators = {}
        self._response_validators = {}
        self._drafts = {}
        if not refs.uses_json_schema_dialect:
            self._document_validator = self._openapi30_validator(refs)

        # Pre-build operationId -> operation dict index for O(1) lookups.
        # Without this, every _find_operation() call scans all paths × methods.
        self._operation_index = refs.operations

    def _modern_keywords(self, schema):
        # jsonschema can enter a resource with an explicit dialect through a
        # parent validator's descend(). Respect the child schema's dialect.
        if self._references.scope(schema)[1] == _OAS31_DIALECT:
            return _oas_schema_keywords(schema)
        return schema.items()

    def _validator_for_schema(self, schema):
        if not self._references.uses_json_schema_dialect:
            return self._document_validator.evolve(schema=schema)
        key = id(schema)
        if key not in self._response_validators:
            base, uri = self._references.scope(schema)
            draft = _json_schema_draft(uri)
            if draft is None:
                raise SchemaExtractionError(f"Unsupported response schema dialect: {uri}")
            if draft not in self._drafts:
                if draft is Draft202012Validator:
                    self._drafts[draft] = validators.create(
                        meta_schema=draft.META_SCHEMA, validators={**draft.VALIDATORS, "$ref": self._validate_schema_ref},
                        type_checker=draft.TYPE_CHECKER, id_of=draft.ID_OF,
                        applicable_validators=self._modern_keywords,
                    )
                else:
                    self._drafts[draft] = validators.extend(draft, {"$ref": self._validate_schema_ref})
            registry = self._references.registry
            self._response_validators[key] = self._drafts[draft](
                schema, registry=registry, _resolver=registry.resolver(base),
            )
        return self._response_validators[key]

    def _validate_schema_ref(self, validator, ref, instance, schema):
        # Rows in an array commonly follow the same reference in the same
        # resolver. Keep at most one binding per schema, including its caller scope.
        key = id(schema)
        cached = self._scoped_ref_validators.get(key)
        if cached is not None and cached[0] is validator._resolver:
            yield from cached[1].iter_errors(instance)
            return
        try:
            target = validator._resolver.lookup(ref)
        except PointerToNowhere:
            if ref.startswith("#/"):
                return  # Lint reports missing local pointers. Siblings still apply.
            raise
        if id(target.contents) in self._references.scopes:
            selected = self._validator_for_schema(target.contents)
            base, _ = self._references.scope(target.contents)
            # A pointer through OpenAPI containers can enter a new schema resource.
            # Keep the active dynamic scope while selecting that resource's dialect.
            resolver = target.resolver.in_subresource(DRAFT202012.create_resource({"$id": base}))
            scoped = type(selected)(target.contents, registry=self._references.registry, _resolver=resolver)
            if key in self._references.scopes:
                self._scoped_ref_validators[key] = validator._resolver, scoped
            yield from scoped.iter_errors(instance)
        else:
            yield from validator.descend(instance, target.contents, resolver=target.resolver)

    def _openapi30_validator(self, refs):
        def validate_ref(validator, ref, instance, schema):
            if not isinstance(ref, str) or not ref.startswith("#/"):
                yield from Draft4Validator.VALIDATORS["$ref"](validator, ref, instance, schema)
                return
            cached = self._scoped_ref_validators.get(ref)
            if cached is None or cached[0] is not validator._resolver:
                target = refs.resolve(schema)
                if not isinstance(target, dict) or "$schema" in target:
                    yield from validator.descend(instance, target)
                    return
                resolver = validator._resolver.in_subresource(DRAFT4.create_resource(target))
                cached = validator._resolver, validator.evolve(schema=target, _resolver=resolver)
                self._scoped_ref_validators[ref] = cached
            yield from cached[1].iter_errors(instance)

        # Compile at the object schema, where required and properties may be
        # contributed by different allOf branches. Never rewrite the input spec.
        keywords_by_schema = {}

        def response_keywords(schema):
            if "$ref" in schema or ("required" not in schema and "allOf" not in schema):
                return _openapi_keywords(schema)
            key = id(schema)
            if key not in keywords_by_schema:
                exempt = set()
                for part in self._all_of_schemas(schema):
                    for name, prop in part.get("properties", {}).items():
                        if any(s.get("writeOnly") is True for s in self._all_of_schemas(prop)):
                            exempt.add(name)
                projected = self._omit_write_required(schema, exempt, {}) if exempt else schema
                # Retain the original alongside its keys (identity cache).
                keywords_by_schema[key] = schema, _openapi_keywords(projected)
            return keywords_by_schema[key][1]

        validator_class = validators.create(
            meta_schema=Draft4Validator.META_SCHEMA,
            validators={**Draft4Validator.VALIDATORS, "$ref": validate_ref},
            type_checker=Draft4Validator.TYPE_CHECKER,
            id_of=Draft4Validator.ID_OF,
            applicable_validators=response_keywords,
        )
        return validator_class(self._spec, registry=Registry())

    def _all_of_schemas(self, schema):
        """Yield unconditional schema declarations at one instance location."""
        pending = [schema]
        seen = set()
        while pending:
            part = pending.pop()
            if not isinstance(part, dict) or id(part) in seen:
                continue
            seen.add(id(part))
            if "$ref" in part:
                pending.append(self._references.resolve(part))
                if not self._references.ref_siblings_apply(part):
                    continue
            yield part
            pending.extend(part.get("allOf", []))

    def _omit_write_required(self, schema, exempt, memo):
        """Project same-instance branches only. Nested properties have their own scope."""
        schema = self._references.resolve(schema)
        if not isinstance(schema, dict):
            return schema
        key = id(schema)
        if key in memo:
            return memo[key]
        # Preserve cyclic references and share completed projections across aliases.
        memo[key] = schema
        projected = dict(schema)
        if "required" in schema:
            projected["required"] = [name for name in schema["required"] if name not in exempt]
        for keyword in ("allOf", "anyOf", "oneOf"):
            if keyword in schema:
                projected[keyword] = [self._omit_write_required(branch, exempt, memo) for branch in schema[keyword]]
        memo[key] = projected
        return projected

    def validate_response(
        self,
        body: Any,
        operation_id: str,
        status_code: int,
        content_type: str | None = None,
        *, json_body_state: JsonBodyState | None = None,
    ) -> ValidationResult:
        """Validate a response body against its OpenAPI schema.

        Args:
            body: The response body (parsed JSON).
            operation_id: The operationId of the endpoint.
            status_code: The HTTP status code of the response.
            content_type: The response Content-Type. Defaults to application/json.
            json_body_state: Use "null" for a parsed JSON null or "invalid" for a parse failure.

        Returns:
            ValidationResult with validity status and any violations or extra fields.
        """
        violations, locations = self.validate_response_locations(
            body, operation_id, status_code, content_type, json_body_state=json_body_state,
        )
        return ValidationResult(valid=not violations, violations=violations,
                                extra_fields=[field_path(location) for location in locations])

    def validate_response_locations(
        self, body: Any, operation_id: str, status_code: int, content_type: str | None = None,
        *, json_body_state: JsonBodyState | None = None,
    ) -> tuple[list[SchemaViolation], list[FieldLocation]]:
        """Validate and return literal extra-field locations for direct comparison."""
        # Get the schema for this operation+status_code
        response_schema = self._get_response_schema(operation_id, status_code, content_type)

        # No schema defined for this operation+status_code - pass validation
        if response_schema is None:
            return [], []

        # Captured XML keeps its root and string leaves for configured field
        # rules. This tree is not an instance of the OpenAPI body schema.
        body_format = response_schema.validation_format(content_type)
        if json_body_state == "invalid":
            return [SchemaViolation("$", "Response body is not valid JSON", "invalid_json")], []
        if (body is None and json_body_state != "null") or body_format == "xml":
            return [], []

        # Validate against schema
        violations: list[SchemaViolation] = []

        try:
            validator = self._validator_for_schema(response_schema.schema)
            errors = list(validator.iter_errors(body))
            if errors and body_format == "text" and isinstance(body, str):
                errors = _text_scalar_errors(validator, body, errors)

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

        extra_fields = self._find_extra_fields(body, response_schema.schema, (), allowed_only=True)

        return violations, extra_fields

    def get_extra_fields(
        self,
        body: Any,
        operation_id: str,
        status_code: int,
        content_type: str | None = None,
    ) -> list[str]:
        """Get list of extra fields present in response but not defined in schema.

        This is used to identify fields that should be compared between implementations
        even though they're not explicitly defined in the spec.

        Args:
            body: The response body (parsed JSON).
            operation_id: The operationId of the endpoint.
            status_code: The HTTP status code of the response.
            content_type: The response Content-Type. Defaults to application/json.

        Returns:
            List of JSONPath expressions for extra fields.
        """
        response_schema = self._get_response_schema(operation_id, status_code, content_type)

        if response_schema is None or body is None or response_schema.validation_format(content_type) == "xml":
            return []

        return [field_path(location) for location in
                self._find_extra_fields(body, response_schema.schema, (), allowed_only=False)]

    def has_schema(self, operation_id: str, status_code: int, content_type: str | None = None) -> bool:
        """Check if a schema exists for the given operation+status_code.

        Args:
            operation_id: The operationId of the endpoint.
            status_code: The HTTP status code.
            content_type: The response Content-Type. Defaults to application/json.

        Returns:
            True if a response schema is defined.
        """
        return self._get_response_schema(operation_id, status_code, content_type) is not None

    def _get_response_schema(
        self, operation_id: str, status_code: int, content_type: str | None,
    ) -> ResponseSchema | None:
        # Cache only declared variants. Arbitrary wire types and parameters must
        # not grow the cache or change the choice for a subsequent response.
        key = (operation_id, status_code)
        if key not in self._schema_cache:
            self._schema_cache[key] = self._extract_response_content(operation_id, status_code)
        content = self._schema_cache[key]
        content_type = "application/json" if content_type is None else content_type
        media = content_type.partition(";")[0].strip().lower()
        major, slash, subtype = media.partition("/")
        ranges = (media, major + "/*", "*/*") if major and slash and subtype else ()
        actual_params = None
        for media_range in ranges:
            for required, schema in content.get(media_range, ()):
                if required:
                    if actual_params is None:
                        actual_params = _media_parameters(content_type)
                    if not required.items() <= actual_params.items():
                        continue
                return schema

        # Before media selection, a bare application/json declaration applied
        # to the captured body regardless of the returned Content-Type.
        for required, schema in content.get("application/json", ()):
            if not required:
                return schema
        return None

    def _extract_response_content(self, operation_id: str, status_code: int) -> _ResponseContent:
        """Index schemas for the selected status by media range and parameters."""
        # Find the operation by operationId
        operation = self._find_operation(operation_id)
        if operation is None:
            return {}

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
            return {}

        # Handle $ref if present
        response_def = self._references.resolve(response_def)

        content: _ResponseContent = {}
        if isinstance(response_def, dict):
            for declared, representation in response_def.get("content", {}).items():
                media = declared.partition(";")[0].strip().lower()
                schema = representation.get("schema")
                entry = None
                if schema is not None:
                    resolved = self._references.resolve(schema)
                    entry = ResponseSchema(
                        schema=schema, allows_extra_fields=self._allows_additional_properties(resolved),
                        media_range=media,
                    )
                content.setdefault(media, []).append((_media_parameters(declared), entry))
        for variants in content.values():
            variants.sort(key=lambda variant: len(variant[0]), reverse=True)
        return content

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
            properties = self._collect_properties(resolved, visited | {ref}) if resolved is not schema else {}
            if not self._references.ref_siblings_apply(schema):
                return properties
        else:
            properties = {}

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
        path: FieldLocation,
        *,
        allowed_only: bool,
    ) -> list[FieldLocation]:
        """Find fields in body that are not defined in schema.

        Recursively walks the body and schema to identify extra fields.
        Handles schema composition (allOf/anyOf/oneOf) by collecting
        properties from all branches.

        Args:
            body: The response body (or nested part of it).
            schema: The schema (or nested part of it).
            path: Literal keys and indices leading to this value.
            allowed_only: Exclude forbidden properties from comparison. The public
                get_extra_fields inventory includes all undeclared properties.

        Returns:
            List of concrete locations for extra fields.
        """
        extra: list[FieldLocation] = []

        if not isinstance(body, (dict, list)):
            return extra

        # Resolve the schema at this instance location
        declared = schema
        schema = self._references.resolve(schema)
        if not isinstance(schema, dict):
            if not self._references.ref_siblings_apply(declared):
                return extra
            schema = declared

        # Handle arrays - check items in the array
        if isinstance(body, list):
            variants = []
            parts = self._all_of_schemas(declared) if self._references.uses_json_schema_dialect else (schema,)
            for part in parts:
                items = part.get("items")
                prefix = ()
                if "prefixItems" in part and self._references.uses_json_schema_dialect:
                    _, uri = self._references.scope(part)
                    draft = _json_schema_draft(uri)
                    if draft and "prefixItems" in draft.VALIDATORS:
                        prefix = part["prefixItems"]
                if prefix or isinstance(items, dict):
                    variants.append((prefix, items))
            if variants:
                for i, item in enumerate(body):
                    if not isinstance(item, (dict, list)):
                        continue
                    if len(variants) == 1:
                        prefix, items = variants[0]
                        item_schema = prefix[i] if i < len(prefix) else items
                    else:
                        item_schema = {"allOf": [prefix[i] if i < len(prefix) else items for prefix, items in variants]}
                    if isinstance(item_schema, dict):
                        extra.extend(self._find_extra_fields(item, item_schema, (*path, i), allowed_only=allowed_only))
            return extra

        # Get defined properties from schema, including composition branches
        properties = self._collect_properties(declared if self._references.uses_json_schema_dialect else schema)
        include_extra = not allowed_only or self._allows_additional_properties(schema)

        # Find fields in body not in schema
        for field_name in body.keys():
            if field_name not in properties:
                if include_extra:
                    extra.append((*path, field_name))
            else:
                # Recursively check nested objects/arrays
                nested_body = body[field_name]
                nested_schema = properties.get(field_name, {})
                nested_extra = self._find_extra_fields(
                    nested_body, nested_schema, (*path, field_name), allowed_only=allowed_only
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
        elif validator == "const":
            return "invalid_const"
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
