"""Tests for dynamic link field extraction from OpenAPI specs.

Verifies that link expressions like $response.body#/resource_uuid are
correctly parsed and used for chain generation and execution.
"""

import pytest
from pathlib import Path
from typing import Any

from api_parity.case_generator import (
    CaseGenerator,
    extract_link_fields_from_spec,
    extract_by_jsonpointer,
)


def set_by_jsonpointer(data: dict, pointer: str, value: Any) -> None:
    """Helper that mirrors _set_by_jsonpointer for testing.

    Sets a value in nested data using a JSONPointer path.
    Creates intermediate dicts/lists as needed.
    """
    parts = pointer.split("/")
    current = data

    for i, part in enumerate(parts[:-1]):
        next_part = parts[i + 1]
        is_next_array = next_part.isdigit()

        if isinstance(current, list):
            idx = int(part)
            while len(current) <= idx:
                current.append({})
            if is_next_array and not isinstance(current[idx], list):
                current[idx] = []
            elif not is_next_array and not isinstance(current[idx], dict):
                current[idx] = {}
            current = current[idx]
        else:
            if part not in current:
                current[part] = [] if is_next_array else {}
            current = current[part]

    final_part = parts[-1]
    if isinstance(current, list) and final_part.isdigit():
        idx = int(final_part)
        while len(current) <= idx:
            current.append({})
        current[idx] = value
    else:
        current[final_part] = value


class TestExtractLinkFieldsFromSpec:
    """Tests for extract_link_fields_from_spec function."""

    def test_extracts_simple_field(self):
        """Simple $response.body#/id expression extracts 'id'."""
        spec = {
            "paths": {
                "/items": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetItem": {
                                        "operationId": "getItem",
                                        "parameters": {
                                            "item_id": "$response.body#/id"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        fields = extract_link_fields_from_spec(spec)
        assert fields == {"id"}

    def test_extracts_custom_field_name(self):
        """Non-standard field names like resource_uuid are extracted."""
        spec = {
            "paths": {
                "/resources": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetResource": {
                                        "operationId": "getResource",
                                        "parameters": {
                                            "resource_id": "$response.body#/resource_uuid"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        fields = extract_link_fields_from_spec(spec)
        assert fields == {"resource_uuid"}

    def test_extracts_nested_path(self):
        """Nested paths like data/nested_id are extracted."""
        spec = {
            "paths": {
                "/nested": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetNested": {
                                        "operationId": "getNested",
                                        "parameters": {
                                            "nested_id": "$response.body#/data/nested_id"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        fields = extract_link_fields_from_spec(spec)
        assert fields == {"data/nested_id"}

    def test_extracts_multiple_fields(self):
        """Multiple different fields across links are all extracted."""
        spec = {
            "paths": {
                "/resources": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetResource": {
                                        "operationId": "getResource",
                                        "parameters": {
                                            "resource_id": "$response.body#/resource_uuid"
                                        }
                                    },
                                    "GetOwner": {
                                        "operationId": "getOwner",
                                        "parameters": {
                                            "owner_id": "$response.body#/owner_identifier"
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/entities": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetEntity": {
                                        "operationId": "getEntity",
                                        "parameters": {
                                            "entity_id": "$response.body#/entity_key"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        fields = extract_link_fields_from_spec(spec)
        assert fields == {"resource_uuid", "owner_identifier", "entity_key"}

    def test_deduplicates_same_field(self):
        """Same field referenced multiple times is only included once."""
        spec = {
            "paths": {
                "/items": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetItem": {
                                        "operationId": "getItem",
                                        "parameters": {
                                            "item_id": "$response.body#/id"
                                        }
                                    },
                                    "UpdateItem": {
                                        "operationId": "updateItem",
                                        "parameters": {
                                            "item_id": "$response.body#/id"
                                        }
                                    },
                                    "DeleteItem": {
                                        "operationId": "deleteItem",
                                        "parameters": {
                                            "item_id": "$response.body#/id"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        fields = extract_link_fields_from_spec(spec)
        assert fields == {"id"}

    def test_ignores_non_body_expressions(self):
        """Header expressions like $response.header.X-Id are ignored."""
        spec = {
            "paths": {
                "/items": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetItem": {
                                        "operationId": "getItem",
                                        "parameters": {
                                            "item_id": "$response.header.X-Item-Id"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        fields = extract_link_fields_from_spec(spec)
        assert fields == set()

    def test_empty_spec_returns_empty_set(self):
        """Spec with no links returns empty set."""
        spec = {
            "paths": {
                "/items": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "List items"
                            }
                        }
                    }
                }
            }
        }
        fields = extract_link_fields_from_spec(spec)
        assert fields == set()

    def test_handles_missing_paths(self):
        """Spec without paths key returns empty set."""
        spec = {"info": {"title": "Test"}}
        fields = extract_link_fields_from_spec(spec)
        assert fields == set()


class TestExtractByJsonpointer:
    """Tests for extract_by_jsonpointer function."""

    def test_extracts_simple_field(self):
        """Simple field extraction works."""
        data = {"id": "abc123"}
        assert extract_by_jsonpointer(data, "id") == "abc123"

    def test_extracts_nested_field(self):
        """Nested field extraction works."""
        data = {"data": {"nested_id": "xyz789"}}
        assert extract_by_jsonpointer(data, "data/nested_id") == "xyz789"

    def test_extracts_deeply_nested(self):
        """Deeply nested extraction works."""
        data = {"level1": {"level2": {"level3": {"value": "deep"}}}}
        assert extract_by_jsonpointer(data, "level1/level2/level3/value") == "deep"

    def test_extracts_from_array(self):
        """Array index extraction works."""
        data = {"items": [{"id": "first"}, {"id": "second"}]}
        assert extract_by_jsonpointer(data, "items/0/id") == "first"
        assert extract_by_jsonpointer(data, "items/1/id") == "second"

    def test_returns_none_for_missing_field(self):
        """Missing field returns None."""
        data = {"other": "value"}
        assert extract_by_jsonpointer(data, "missing") is None

    def test_returns_none_for_missing_nested(self):
        """Missing nested path returns None."""
        data = {"data": {"other": "value"}}
        assert extract_by_jsonpointer(data, "data/missing/field") is None

    def test_returns_none_for_invalid_array_index(self):
        """Invalid array index returns None."""
        data = {"items": [{"id": "only"}]}
        assert extract_by_jsonpointer(data, "items/5/id") is None

    def test_empty_pointer_returns_data(self):
        """Empty pointer returns the data itself."""
        data = {"id": "value"}
        assert extract_by_jsonpointer(data, "") == data


class TestCaseGeneratorLinkFields:
    """Tests for CaseGenerator.get_link_fields() method."""

    def test_extracts_standard_fields_from_test_api(self):
        """Standard test API has 'id' in links."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        generator = CaseGenerator(spec_path)
        fields = generator.get_link_fields()
        # test_api.yaml uses $response.body#/id in links
        assert "id" in fields

    def test_extracts_custom_fields(self):
        """Custom fields API extracts resource_uuid and entity_identifier."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api_custom_fields.yaml"
        generator = CaseGenerator(spec_path)
        fields = generator.get_link_fields()
        assert "resource_uuid" in fields
        assert "entity_identifier" in fields
        assert "data/nested_id" in fields

    def test_extracts_array_index_paths(self):
        """Array index paths like items/0/item_id are extracted."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api_custom_fields.yaml"
        generator = CaseGenerator(spec_path)
        fields = generator.get_link_fields()
        # The custom fields spec has a link with $response.body#/items/0/item_id
        assert "items/0/item_id" in fields

    def test_link_fields_available_before_generation(self):
        """Link fields are available immediately after construction."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        generator = CaseGenerator(spec_path)
        # Should not need to call generate() first
        fields = generator.get_link_fields()
        assert isinstance(fields, set)


class TestSetByJsonpointer:
    """Tests for set_by_jsonpointer function (mirrors _set_by_jsonpointer).

    These tests verify the fix for Issue #2 - array handling bug.
    """

    def test_sets_simple_field(self):
        """Simple field like 'id' works."""
        data = {}
        set_by_jsonpointer(data, "id", "abc123")
        assert data == {"id": "abc123"}

    def test_sets_nested_field(self):
        """Nested field like 'data/nested_id' creates intermediate dict."""
        data = {}
        set_by_jsonpointer(data, "data/nested_id", "xyz789")
        assert data == {"data": {"nested_id": "xyz789"}}

    def test_sets_deeply_nested(self):
        """Deeply nested path creates all intermediate dicts."""
        data = {}
        set_by_jsonpointer(data, "a/b/c/d", "deep")
        assert data == {"a": {"b": {"c": {"d": "deep"}}}}

    def test_sets_array_element(self):
        """Direct array index like 'items/0' creates array."""
        data = {}
        set_by_jsonpointer(data, "items/0", "first")
        assert data == {"items": ["first"]}

    def test_sets_array_nested_field(self):
        """Array index path like 'items/0/id' creates array with object."""
        data = {}
        set_by_jsonpointer(data, "items/0/id", "item-id")
        assert data == {"items": [{"id": "item-id"}]}

    def test_sets_array_deeper_index(self):
        """Array index > 0 pads with empty objects."""
        data = {}
        set_by_jsonpointer(data, "items/2/id", "third")
        assert data == {"items": [{}, {}, {"id": "third"}]}

    def test_sets_multiple_paths(self):
        """Multiple set operations build up structure."""
        data = {}
        set_by_jsonpointer(data, "id", "root-id")
        set_by_jsonpointer(data, "items/0/item_id", "item-0")
        set_by_jsonpointer(data, "items/1/item_id", "item-1")
        set_by_jsonpointer(data, "data/nested", "nested-val")
        assert data == {
            "id": "root-id",
            "items": [{"item_id": "item-0"}, {"item_id": "item-1"}],
            "data": {"nested": "nested-val"},
        }

    def test_sets_nested_array_in_array(self):
        """Nested array path like 'matrix/0/0' works."""
        data = {}
        set_by_jsonpointer(data, "matrix/0/0", "cell")
        assert data == {"matrix": [["cell"]]}

    def test_sets_complex_array_path(self):
        """Complex path like 'data/items/0/sub/1/value' works."""
        data = {}
        set_by_jsonpointer(data, "data/items/0/sub/1/value", "deep-array")
        assert data == {
            "data": {
                "items": [
                    {"sub": [{}, {"value": "deep-array"}]}
                ]
            }
        }

    def test_overwrites_existing_value(self):
        """Setting same path twice overwrites."""
        data = {}
        set_by_jsonpointer(data, "id", "first")
        set_by_jsonpointer(data, "id", "second")
        assert data == {"id": "second"}

    def test_extends_existing_array(self):
        """Setting higher index extends existing array."""
        data = {"items": [{"id": "existing"}]}
        set_by_jsonpointer(data, "items/2/id", "new")
        assert data == {"items": [{"id": "existing"}, {}, {"id": "new"}]}
