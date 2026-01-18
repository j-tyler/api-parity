"""Schema Value Generator - Generates values that satisfy OpenAPI schema constraints.

This module provides schema-aware value generation for synthetic responses during
chain discovery. When link-extracted values must satisfy target parameter constraints
(e.g., enums), this generator produces compliant values instead of generic UUIDs.

See DESIGN.md "Schema-Driven Synthetic Value Generation" for rationale.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class SchemaValueGenerator:
    """Generates values satisfying OpenAPI schema constraints.

    Used during chain discovery to create synthetic response values that will
    satisfy target parameter constraints. Priority order ensures enum/const
    constraints take precedence over format hints.

    Usage:
        generator = SchemaValueGenerator(parsed_spec)
        value = generator.generate(field_schema)
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        """Initialize with parsed OpenAPI spec for $ref resolution.

        Args:
            spec: Parsed OpenAPI specification dict.
        """
        self._spec = spec

    def generate(self, schema: dict[str, Any] | None) -> Any:
        """Generate a value satisfying the schema constraints.

        Priority order (first matching constraint wins):
        1. enum - return first enum value
        2. const - return const value
        3. format: uuid - generate UUID
        4. format: date-time - generate ISO timestamp
        5. format: date - generate ISO date
        6. format: uri - generate placeholder URI
        7. format: email - generate placeholder email
        8. type: integer - return 1
        9. type: number - return 1.0
        10. type: boolean - return True
        11. type: string - generate UUID string
        12. type: array - generate single-item array
        13. type: object - generate empty dict
        14. Fallback - UUID string

        Note: Composition schemas (allOf/anyOf/oneOf) are not handled; uses fallback.

        Args:
            schema: JSON Schema dict (may be None or empty).

        Returns:
            A value satisfying the schema constraints.
        """
        if schema is None:
            return str(uuid.uuid4())

        # Resolve $ref if present
        schema = self._resolve_ref(schema)

        # Priority 1: enum constraint - always takes precedence
        if "enum" in schema:
            enum_values = schema["enum"]
            if enum_values:
                return enum_values[0]

        # Priority 2: const constraint
        if "const" in schema:
            return schema["const"]

        # Priority 3-7: format hints
        fmt = schema.get("format")
        if fmt == "uuid":
            return str(uuid.uuid4())
        if fmt == "date-time":
            return datetime.now(timezone.utc).isoformat()
        if fmt == "date":
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if fmt == "uri":
            return f"http://example.com/{uuid.uuid4()}"
        if fmt == "email":
            return f"user-{uuid.uuid4().hex[:8]}@example.com"

        # Priority 8-13: type-based generation
        # Numeric defaults are 1/1.0 rather than 0 to avoid triggering division-by-zero
        # edge cases and to satisfy minimum:1 constraints common in pagination/limits.
        schema_type = schema.get("type")
        if schema_type == "integer":
            return 1
        if schema_type == "number":
            return 1.0
        if schema_type == "boolean":
            return True
        if schema_type == "string":
            return str(uuid.uuid4())
        if schema_type == "array":
            # Generate single-item array with items schema
            items_schema = schema.get("items", {})
            return [self.generate(items_schema)]
        if schema_type == "object":
            return {}

        # Priority 14: Fallback
        return str(uuid.uuid4())

    def navigate_to_field(
        self, schema: dict[str, Any], pointer: str
    ) -> dict[str, Any] | None:
        """Navigate from a schema to a nested field's schema using JSONPointer path.

        Handles:
        - Object properties: "data" -> schema.properties.data
        - Array items: "items/0" -> schema.items (arrays are homogeneous)
        - $ref resolution during navigation

        Args:
            schema: Starting schema (typically a response schema).
            pointer: JSONPointer path without leading slash (e.g., "id", "data/items/0/id").

        Returns:
            The schema for the field at the pointer path, or None if not found.
        """
        if not pointer:
            return schema

        # Resolve $ref at current level
        schema = self._resolve_ref(schema)

        parts = pointer.split("/")
        current = schema

        for part in parts:
            if current is None:
                return None

            # Resolve $ref at each level
            current = self._resolve_ref(current)

            # Handle array index - arrays are homogeneous, return items schema
            if part.isdigit():
                items_schema = current.get("items")
                if items_schema is None:
                    return None
                current = items_schema
                continue

            # Handle object property
            schema_type = current.get("type")
            if schema_type == "object" or "properties" in current:
                properties = current.get("properties", {})
                if part in properties:
                    current = properties[part]
                    continue
                # Property not found
                return None

            # Handle array at this level without explicit index in pointer.
            # Example: pointer "items/name" where "items" field is an array of objects.
            # We skip into the items schema and look for "name" property there.
            # This differs from "items/0/name" which uses the digit branch above.
            if schema_type == "array":
                items_schema = current.get("items")
                if items_schema is None:
                    return None
                current = self._resolve_ref(items_schema)
                properties = current.get("properties", {})
                if part in properties:
                    current = properties[part]
                    continue
                return None

            # Unknown structure
            return None

        return self._resolve_ref(current)

    def get_response_schema(
        self, operation_id: str, status_code: int
    ) -> dict[str, Any] | None:
        """Get the response schema for an operation and status code.

        Args:
            operation_id: The operationId to look up.
            status_code: The HTTP status code.

        Returns:
            The response body schema, or None if not found.
        """
        # Find operation by operationId
        operation = self._find_operation(operation_id)
        if operation is None:
            return None

        # Get responses
        responses = operation.get("responses", {})

        # Look for exact status code match first, then wildcards, then default
        status_str = str(status_code)
        response_def = responses.get(status_str)
        if response_def is None:
            # Try wildcard (2XX, 3XX, etc.)
            wildcard = f"{status_code // 100}XX"
            response_def = responses.get(wildcard)
        if response_def is None:
            response_def = responses.get("default")

        if response_def is None:
            return None

        # Resolve $ref if present
        response_def = self._resolve_ref(response_def)

        # Get content -> application/json -> schema
        content = response_def.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema")

        if schema is None:
            return None

        return self._resolve_ref(schema)

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
                # Path items contain HTTP operations (get/post/...) plus metadata keys.
                # isinstance(dict) filters metadata (parameters=list, summary=string).
                # startswith("$") filters $ref strings that survived the dict check.
                if not isinstance(operation, dict) or method_or_key.startswith("$"):
                    continue
                if operation.get("operationId") == operation_id:
                    return operation
        return None

    def _resolve_ref(
        self, obj: dict[str, Any], visited: frozenset[str] | None = None
    ) -> dict[str, Any]:
        """Resolve a $ref reference in the spec.

        Handles circular references by tracking visited paths.

        Args:
            obj: Object that may contain a $ref.
            visited: Set of already-visited $ref paths to detect cycles.

        Returns:
            The resolved object, or the original if $ref not found or circular.
        """
        if not isinstance(obj, dict):
            return obj

        ref = obj.get("$ref")
        if ref is None:
            return obj

        # Initialize visited set
        if visited is None:
            visited = frozenset()

        # Detect circular reference
        if ref in visited:
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

        if isinstance(resolved, dict):
            # Recursively resolve nested $refs
            return self._resolve_ref(resolved, visited | {ref})

        return obj
