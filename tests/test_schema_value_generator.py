"""Unit tests for SchemaValueGenerator.

Tests the schema-aware value generation for synthetic responses during
chain discovery. The generator must respect constraint priority:
enum > const > format > type > fallback.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

import pytest

from api_parity.schema_value_generator import SchemaValueGenerator


class TestConstraintPriority:
    """Tests for constraint priority in value generation."""

    def test_enum_takes_priority_over_format(self):
        """Enum constraint should beat format hint."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        # Schema has both enum and format
        schema = {
            "type": "string",
            "format": "uuid",
            "enum": ["alpha", "beta", "gamma"],
        }

        value = generator.generate(schema)
        assert value == "alpha", "Enum should take priority over uuid format"

    def test_enum_takes_priority_over_type(self):
        """Enum constraint should beat type."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        # Integer enum
        schema = {
            "type": "integer",
            "enum": [100, 200, 300],
        }

        value = generator.generate(schema)
        assert value == 100, "Enum should take priority over integer type"

    def test_const_takes_priority_over_format(self):
        """Const constraint should beat format hint."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        schema = {
            "type": "string",
            "format": "uuid",
            "const": "fixed-value",
        }

        value = generator.generate(schema)
        assert value == "fixed-value", "Const should take priority over format"

    def test_empty_enum_falls_through(self):
        """Empty enum array should fall through to next constraint."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        schema = {
            "type": "string",
            "format": "uuid",
            "enum": [],  # Empty enum
        }

        value = generator.generate(schema)
        # Should fall through to uuid format
        try:
            uuid_module.UUID(value)
        except ValueError:
            pytest.fail(f"Expected UUID format for empty enum, got {value}")


class TestTypeGeneration:
    """Tests for type-based value generation."""

    def test_integer_type(self):
        """Integer type produces integer value."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "integer"})
        assert value == 1
        assert isinstance(value, int)

    def test_number_type(self):
        """Number type produces float value."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "number"})
        assert value == 1.0
        assert isinstance(value, float)

    def test_boolean_type(self):
        """Boolean type produces True."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "boolean"})
        assert value is True

    def test_string_type(self):
        """String type produces UUID string."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "string"})
        # Should be a valid UUID string
        try:
            uuid_module.UUID(value)
        except ValueError:
            pytest.fail(f"Expected UUID string, got {value}")

    def test_array_type(self):
        """Array type produces single-item array."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "array", "items": {"type": "integer"}})
        assert isinstance(value, list)
        assert len(value) == 1
        assert value[0] == 1

    def test_array_tuple_validation(self):
        """Array with tuple validation (items as list) produces correct-length array.

        Regression test for Bug 4: Tuple validation schema crashes generate().
        JSON Schema allows items to be a list of schemas (tuple validation)
        where each position has its own schema. Previously this crashed with
        AttributeError because the code called .get() on the list.
        """
        spec = {}
        generator = SchemaValueGenerator(spec)

        # Tuple validation schema: [string, integer, boolean]
        schema = {
            "type": "array",
            "items": [
                {"type": "string"},
                {"type": "integer"},
                {"type": "boolean"},
            ]
        }

        value = generator.generate(schema)
        assert isinstance(value, list)
        assert len(value) == 3
        # First item should be UUID string
        assert isinstance(value[0], str)
        uuid_module.UUID(value[0])  # Should not raise
        # Second item should be integer 1
        assert value[1] == 1
        # Third item should be boolean True
        assert value[2] is True

    def test_object_type(self):
        """Object type produces empty dict."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "object"})
        assert value == {}


class TestFormatGeneration:
    """Tests for format-based value generation."""

    def test_uuid_format(self):
        """UUID format produces valid UUID."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "string", "format": "uuid"})
        try:
            uuid_module.UUID(value)
        except ValueError:
            pytest.fail(f"Expected valid UUID, got {value}")

    def test_datetime_format(self):
        """Date-time format produces ISO timestamp."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "string", "format": "date-time"})
        # Should be parseable as ISO datetime
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"Expected ISO datetime, got {value}")

    def test_date_format(self):
        """Date format produces ISO date."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "string", "format": "date"})
        # Should be YYYY-MM-DD format
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            pytest.fail(f"Expected ISO date, got {value}")

    def test_uri_format(self):
        """URI format produces valid URI."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "string", "format": "uri"})
        assert value.startswith("http://"), f"Expected URI, got {value}"

    def test_email_format(self):
        """Email format produces valid email."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "string", "format": "email"})
        assert "@example.com" in value, f"Expected email, got {value}"


class TestSchemaNavigation:
    """Tests for navigate_to_field functionality."""

    def test_navigate_simple_property(self):
        """Navigation to simple object property."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "name": {"type": "string"},
            },
        }

        result = generator.navigate_to_field(schema, "id")
        assert result == {"type": "string", "format": "uuid"}

    def test_navigate_nested_property(self):
        """Navigation through nested objects."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        schema = {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {
                        "item": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "enum": ["a", "b"]},
                            },
                        },
                    },
                },
            },
        }

        result = generator.navigate_to_field(schema, "data/item/id")
        assert result == {"type": "string", "enum": ["a", "b"]}

    def test_navigate_array_items(self):
        """Navigation through array items (arrays are homogeneous)."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                        },
                    },
                },
            },
        }

        # Navigate to items/0/id - array index is handled, items are homogeneous
        result = generator.navigate_to_field(schema, "items/0/id")
        assert result == {"type": "integer"}

        # Different index should return same schema (homogeneous)
        result2 = generator.navigate_to_field(schema, "items/5/id")
        assert result2 == {"type": "integer"}

    def test_navigate_missing_property_returns_none(self):
        """Navigation to non-existent property returns None."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
            },
        }

        result = generator.navigate_to_field(schema, "nonexistent")
        assert result is None

    def test_navigate_empty_pointer_returns_schema(self):
        """Empty pointer returns the schema itself."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        schema = {"type": "string", "enum": ["x"]}

        result = generator.navigate_to_field(schema, "")
        assert result == schema


class TestRefResolution:
    """Tests for $ref resolution during navigation and generation."""

    def test_ref_resolution_in_generate(self):
        """Generate resolves $ref to get actual schema."""
        spec = {
            "components": {
                "schemas": {
                    "Status": {
                        "type": "string",
                        "enum": ["active", "inactive"],
                    },
                },
            },
        }
        generator = SchemaValueGenerator(spec)

        schema = {"$ref": "#/components/schemas/Status"}

        value = generator.generate(schema)
        assert value in ["active", "inactive"]

    def test_ref_resolution_in_navigation(self):
        """Navigate resolves $ref at each level."""
        spec = {
            "components": {
                "schemas": {
                    "Item": {
                        "type": "object",
                        "properties": {
                            "status": {"$ref": "#/components/schemas/Status"},
                        },
                    },
                    "Status": {
                        "type": "string",
                        "enum": ["active", "inactive"],
                    },
                },
            },
        }
        generator = SchemaValueGenerator(spec)

        schema = {"$ref": "#/components/schemas/Item"}

        result = generator.navigate_to_field(schema, "status")
        # Should resolve to the Status enum schema
        assert result is not None
        assert result.get("enum") == ["active", "inactive"]

    def test_circular_ref_handled(self):
        """Circular $refs don't cause infinite recursion."""
        spec = {
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                            "children": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Node"},
                            },
                        },
                    },
                },
            },
        }
        generator = SchemaValueGenerator(spec)

        schema = {"$ref": "#/components/schemas/Node"}

        # Should not hang or crash
        value = generator.generate(schema)
        assert value == {}  # Object type fallback


class TestResponseSchemaLookup:
    """Tests for get_response_schema functionality."""

    def test_get_response_schema_exact_status(self):
        """Get response schema with exact status code match."""
        spec = {
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        generator = SchemaValueGenerator(spec)

        result = generator.get_response_schema("listUsers", 200)
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_get_response_schema_wildcard_status(self):
        """Get response schema with wildcard status code (2XX)."""
        spec = {
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "responses": {
                            "2XX": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        generator = SchemaValueGenerator(spec)

        result = generator.get_response_schema("createUser", 201)
        assert result == {"type": "object"}

    def test_get_response_schema_default_fallback(self):
        """Get response schema falls back to 'default' response."""
        spec = {
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "responses": {
                            "default": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        generator = SchemaValueGenerator(spec)

        result = generator.get_response_schema("listItems", 200)
        assert result == {"type": "string"}

    def test_get_response_schema_not_found(self):
        """Get response schema returns None for unknown operation."""
        spec = {"paths": {}}
        generator = SchemaValueGenerator(spec)

        result = generator.get_response_schema("unknownOp", 200)
        assert result is None


class TestFallbackBehavior:
    """Tests for fallback behavior when no schema is available."""

    def test_none_schema_returns_uuid(self):
        """None schema returns UUID string."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate(None)
        try:
            uuid_module.UUID(value)
        except ValueError:
            pytest.fail(f"Expected UUID for None schema, got {value}")

    def test_empty_schema_returns_uuid(self):
        """Empty schema returns UUID string."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({})
        try:
            uuid_module.UUID(value)
        except ValueError:
            pytest.fail(f"Expected UUID for empty schema, got {value}")

    def test_unknown_type_returns_uuid(self):
        """Unknown type returns UUID string."""
        spec = {}
        generator = SchemaValueGenerator(spec)

        value = generator.generate({"type": "unknown_type"})
        try:
            uuid_module.UUID(value)
        except ValueError:
            pytest.fail(f"Expected UUID for unknown type, got {value}")
