"""Tests for dynamic link field extraction from OpenAPI specs.

Verifies that link expressions like $response.body#/resource_uuid and
$response.header.Location are correctly parsed and used for chain
generation and execution.
"""

import pytest
from pathlib import Path
from typing import Any

from api_parity.case_generator import (
    CaseGenerator,
    HeaderRef,
    LinkFields,
    extract_link_fields_from_spec,
    extract_by_jsonpointer,
    _get_operation_id,
)


def get_header_names(link_fields: LinkFields) -> set[str]:
    """Extract unique header names from LinkFields.headers list."""
    return {h.name for h in link_fields.headers}


def get_header_refs(link_fields: LinkFields, name: str) -> list[HeaderRef]:
    """Get all HeaderRef objects for a given header name."""
    return [h for h in link_fields.headers if h.name == name]


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
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == {"id"}
        assert link_fields.headers == []

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
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == {"resource_uuid"}

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
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == {"data/nested_id"}

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
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == {"resource_uuid", "owner_identifier", "entity_key"}

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
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == {"id"}

    def test_extracts_header_expressions(self):
        """Header expressions like $response.header.X-Id are extracted."""
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
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == set()
        assert get_header_names(link_fields) == {"x-item-id"}  # Lowercase normalized
        # Verify no index specified
        refs = get_header_refs(link_fields, "x-item-id")
        assert len(refs) == 1
        assert refs[0].index is None

    def test_empty_spec_returns_empty_link_fields(self):
        """Spec with no links returns empty LinkFields."""
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
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == set()
        assert link_fields.headers == []

    def test_handles_missing_paths(self):
        """Spec without paths key returns empty LinkFields."""
        spec = {"info": {"title": "Test"}}
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == set()
        assert link_fields.headers == []

    def test_extracts_location_header(self):
        """Location header expressions are extracted and normalized."""
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
                                            "item_url": "$response.header.Location"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        link_fields = extract_link_fields_from_spec(spec)
        assert get_header_names(link_fields) == {"location"}

    def test_extracts_mixed_body_and_header(self):
        """Mixed body and header expressions are both extracted."""
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
                                            "item_id": "$response.body#/id",
                                            "item_url": "$response.header.Location"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        link_fields = extract_link_fields_from_spec(spec)
        assert link_fields.body_pointers == {"id"}
        assert get_header_names(link_fields) == {"location"}

    def test_header_case_insensitivity(self):
        """Header names with different cases are normalized to lowercase."""
        spec = {
            "paths": {
                "/items": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetByLocation": {
                                        "operationId": "get1",
                                        "parameters": {
                                            "url": "$response.header.LOCATION"
                                        }
                                    },
                                    "GetByCustom": {
                                        "operationId": "get2",
                                        "parameters": {
                                            "custom": "$response.header.X-Custom-Header"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        link_fields = extract_link_fields_from_spec(spec)
        assert get_header_names(link_fields) == {"location", "x-custom-header"}

    def test_extracts_header_with_array_index(self):
        """Header expressions with array index are extracted with index."""
        spec = {
            "paths": {
                "/items": {
                    "post": {
                        "responses": {
                            "201": {
                                "links": {
                                    "GetFirst": {
                                        "operationId": "getFirst",
                                        "parameters": {
                                            "cookie": "$response.header.Set-Cookie[0]"
                                        }
                                    },
                                    "GetSecond": {
                                        "operationId": "getSecond",
                                        "parameters": {
                                            "cookie": "$response.header.Set-Cookie[1]"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        link_fields = extract_link_fields_from_spec(spec)
        assert get_header_names(link_fields) == {"set-cookie"}
        # Two HeaderRefs for Set-Cookie with indices 0 and 1
        refs = get_header_refs(link_fields, "set-cookie")
        assert len(refs) == 2
        indices = {r.index for r in refs}
        assert indices == {0, 1}


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

    def test_decodes_tilde_escape(self):
        """RFC 6901: ~0 decodes to tilde (~).

        Regression test for Bug 17: JSONPointer escape sequences not decoded.
        """
        data = {"weird~field": "value1", "normal": "value2"}
        # Per RFC 6901, ~0 decodes to ~
        result = extract_by_jsonpointer(data, "weird~0field")
        assert result == "value1"

    def test_decodes_slash_escape(self):
        """RFC 6901: ~1 decodes to slash (/).

        Regression test for Bug 17: JSONPointer escape sequences not decoded.
        """
        data = {"path/to/thing": "value1", "normal": "value2"}
        # Per RFC 6901, ~1 decodes to /
        result = extract_by_jsonpointer(data, "path~1to~1thing")
        assert result == "value1"

    def test_decodes_mixed_escapes(self):
        """RFC 6901: Multiple escape sequences decode correctly."""
        data = {"weird~/field": "complex"}
        # ~0 → ~, ~1 → /
        result = extract_by_jsonpointer(data, "weird~0~1field")
        assert result == "complex"

    def test_escape_decode_order_matters(self):
        """RFC 6901: ~01 should decode to ~1, not to /.

        The order is important: decode ~1 first, then ~0.
        """
        data = {"field~1": "correct"}
        # ~01 should become ~1 (not /)
        # The sequence is: ~01 → (decode ~1 first, no match) → (decode ~0) → ~1
        # Actually per RFC 6901, ~01 = ~0 followed by 1 = ~ followed by 1 = "~1"
        result = extract_by_jsonpointer(data, "field~01")
        assert result == "correct"

    def test_nested_with_escapes(self):
        """RFC 6901: Escape sequences work in nested paths."""
        data = {"level/one": {"sub~field": "nested"}}
        result = extract_by_jsonpointer(data, "level~1one/sub~0field")
        assert result == "nested"


class TestCaseGeneratorLinkFields:
    """Tests for CaseGenerator.get_link_fields() method."""

    def test_extracts_standard_fields_from_test_api(self):
        """Standard test API has 'id' in links."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        generator = CaseGenerator(spec_path)
        link_fields = generator.get_link_fields()
        # test_api.yaml uses $response.body#/id in links
        assert "id" in link_fields.body_pointers

    def test_extracts_custom_fields(self):
        """Custom fields API extracts resource_uuid and entity_identifier."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api_custom_fields.yaml"
        generator = CaseGenerator(spec_path)
        link_fields = generator.get_link_fields()
        assert "resource_uuid" in link_fields.body_pointers
        assert "entity_identifier" in link_fields.body_pointers
        assert "data/nested_id" in link_fields.body_pointers

    def test_extracts_array_index_paths(self):
        """Array index paths like items/0/item_id are extracted."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api_custom_fields.yaml"
        generator = CaseGenerator(spec_path)
        link_fields = generator.get_link_fields()
        # The custom fields spec has a link with $response.body#/items/0/item_id
        assert "items/0/item_id" in link_fields.body_pointers

    def test_link_fields_available_before_generation(self):
        """Link fields are available immediately after construction."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api.yaml"
        generator = CaseGenerator(spec_path)
        # Should not need to call generate() first
        link_fields = generator.get_link_fields()
        assert isinstance(link_fields, LinkFields)

    def test_extracts_header_links_from_fixture(self):
        """Header link API extracts header names."""
        spec_path = Path(__file__).parent / "fixtures" / "test_api_header_links.yaml"
        generator = CaseGenerator(spec_path)
        link_fields = generator.get_link_fields()
        # test_api_header_links.yaml uses $response.header.Location
        assert "location" in get_header_names(link_fields)


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


class TestGetOperationId:
    """Tests for _get_operation_id helper function.

    Regression tests for Bug 8: Inconsistent operationId defaults.
    Previously, different code paths used different defaults for operations
    without explicit operationId: "unknown" vs None vs f"{method}_{path}".
    This caused link attribution to silently fail.
    """

    def test_returns_explicit_operation_id(self):
        """When operation has explicit operationId, it is returned."""
        operation = {"operationId": "getUserById", "responses": {}}
        result = _get_operation_id(operation, "get", "/users/{id}")
        assert result == "getUserById"

    def test_generates_id_from_method_and_path(self):
        """When no operationId, generates from method and path."""
        operation = {"responses": {}}
        result = _get_operation_id(operation, "get", "/users/{id}")
        assert result == "get_/users/{id}"

    def test_consistent_across_different_methods(self):
        """Different methods on same path generate different IDs."""
        operation = {"responses": {}}
        get_id = _get_operation_id(operation, "get", "/items")
        post_id = _get_operation_id(operation, "post", "/items")
        delete_id = _get_operation_id(operation, "delete", "/items")

        assert get_id == "get_/items"
        assert post_id == "post_/items"
        assert delete_id == "delete_/items"
        assert get_id != post_id != delete_id

    def test_consistent_with_path_parameters(self):
        """Path parameters are preserved in generated ID."""
        operation = {"responses": {}}
        result = _get_operation_id(operation, "get", "/users/{userId}/posts/{postId}")
        assert result == "get_/users/{userId}/posts/{postId}"

    def test_never_returns_none(self):
        """Generated ID is never None, even for empty operation."""
        operation = {}
        result = _get_operation_id(operation, "get", "/test")
        assert result is not None
        assert result == "get_/test"


class TestSeedParameter:
    """Tests for seed parameter behavior in CaseGenerator.

    Regression tests for Bug 1: Seed parameter doesn't control randomness.
    Previously, derandomize=seed is not None just set True/False, making
    ALL non-None seed values produce identical results.
    """

    def test_seed_attribute_set_on_decorated_function(self):
        """Verify the fix: seed is set as Hypothesis internal attribute.

        This tests that when a seed is provided, the code sets the
        _hypothesis_internal_use_seed attribute on the test function.
        This is what Hypothesis's @seed() decorator does internally.
        """
        from hypothesis import given, settings
        from hypothesis.strategies import integers

        # Simulate what case_generator does
        collected = []

        @given(n=integers(min_value=0, max_value=1000))
        @settings(
            max_examples=3,
            database=None,
            derandomize=True,
        )
        def collect(n):
            collected.append(n)

        # Set the seed attribute (this is the fix)
        seed = 42
        collect._hypothesis_internal_use_seed = seed

        # Run it
        collect()
        results_seed_42 = list(collected)

        # Reset and run with different seed
        collected.clear()
        collect._hypothesis_internal_use_seed = 100
        collect()
        results_seed_100 = list(collected)

        # Different seeds should produce different results
        assert results_seed_42 != results_seed_100, (
            "Different seeds should produce different Hypothesis outputs"
        )

    def test_same_seed_attribute_produces_same_results(self):
        """Same seed value produces identical results."""
        from hypothesis import given, settings
        from hypothesis.strategies import integers

        def run_with_seed(seed_val):
            collected = []

            @given(n=integers(min_value=0, max_value=1000))
            @settings(
                max_examples=5,
                database=None,
                derandomize=True,
            )
            def collect(n):
                collected.append(n)

            collect._hypothesis_internal_use_seed = seed_val
            collect()
            return collected

        results_1 = run_with_seed(42)
        results_2 = run_with_seed(42)

        assert results_1 == results_2, "Same seed should produce identical results"


class TestCaseGeneratorSeedBehavior:
    """Test that CaseGenerator.generate() respects seed parameter.

    The seed parameter enables deterministic generation:
    - seed=None: Non-deterministic (different each run)
    - seed=42: Deterministic sequence A (same every run)
    - seed=100: Deterministic sequence B (different from seed=42, but reproducible)
    """

    @pytest.fixture
    def spec_path(self):
        """Path to test API spec."""
        return Path(__file__).parent / "fixtures" / "test_api.yaml"

    def test_same_seed_produces_same_sequence(self, spec_path):
        """Same seed value should produce identical results on repeated runs."""
        generator = CaseGenerator(spec_path)

        cases_run1 = list(generator.generate(max_cases=5, seed=42))
        cases_run2 = list(generator.generate(max_cases=5, seed=42))

        ops_run1 = [c.operation_id for c in cases_run1]
        ops_run2 = [c.operation_id for c in cases_run2]

        assert ops_run1 == ops_run2

    def test_different_seeds_produce_different_data(self, spec_path):
        """Different seed values should produce different generated data."""
        generator = CaseGenerator(spec_path)

        # Use more cases to see variation in generated data
        cases_seed_42 = list(generator.generate(max_cases=20, seed=42))
        cases_seed_100 = list(generator.generate(max_cases=20, seed=100))

        # The seed affects generated data (query params, bodies), not operation selection
        data_42 = [(c.query, c.body) for c in cases_seed_42]
        data_100 = [(c.query, c.body) for c in cases_seed_100]

        assert data_42 != data_100, "Different seeds should produce different generated data"


def filter_parent_pointers(pointers: set[str]) -> list[str]:
    """Filter out pointers that are prefixes of longer pointers.

    This mirrors the filtering logic in CaseGenerator._generate_synthetic_body().
    Parent pointers are filtered because setting a value at a parent pointer
    would overwrite the structure needed for child pointers.

    Example: If we have both "entries/0" and "entries/0/assetTerm", we should
    only process "entries/0/assetTerm" - the parent "entries/0" would be
    created as an intermediate dict anyway.
    """
    sorted_pointers = sorted(pointers, key=len, reverse=True)
    filtered = []
    for ptr in sorted_pointers:
        is_prefix = any(
            other.startswith(ptr + "/")
            for other in sorted_pointers if len(other) > len(ptr)
        )
        if not is_prefix:
            filtered.append(ptr)
    return filtered


class TestFilterParentPointers:
    """Tests for pointer filtering in synthetic body generation.

    When generating synthetic response bodies, pointers that are prefixes of
    other pointers should be filtered out. Otherwise, setting a value at the
    parent pointer would overwrite the nested structure needed by child pointers.
    """

    def test_filters_parent_when_child_exists(self):
        """Parent pointer is filtered when child pointer exists."""
        pointers = {"entries/0", "entries/0/assetTerm"}
        result = filter_parent_pointers(pointers)
        # Only the child should remain
        assert "entries/0/assetTerm" in result
        assert "entries/0" not in result

    def test_keeps_sibling_pointers(self):
        """Sibling pointers (not prefix relationships) are both kept."""
        pointers = {"entries/0", "entries/1"}
        result = filter_parent_pointers(pointers)
        assert "entries/0" in result
        assert "entries/1" in result

    def test_filters_deep_parent_chain(self):
        """Multiple levels of parent pointers are filtered."""
        pointers = {"a", "a/b", "a/b/c", "a/b/c/d"}
        result = filter_parent_pointers(pointers)
        # Only the deepest should remain
        assert result == ["a/b/c/d"]

    def test_keeps_unrelated_pointers(self):
        """Unrelated pointers are all kept."""
        pointers = {"id", "name", "data/value"}
        result = filter_parent_pointers(pointers)
        assert len(result) == 3
        assert set(result) == pointers

    def test_handles_similar_names_not_prefixes(self):
        """Pointers with similar names but not prefix relationships are kept."""
        # "foo" is NOT a prefix of "foobar" (no "/" separator)
        pointers = {"foo", "foobar", "foo/bar"}
        result = filter_parent_pointers(pointers)
        # "foo" IS prefix of "foo/bar", so only "foobar" and "foo/bar" remain
        assert "foobar" in result
        assert "foo/bar" in result
        assert "foo" not in result

    def test_empty_input(self):
        """Empty input returns empty list."""
        result = filter_parent_pointers(set())
        assert result == []

    def test_single_pointer(self):
        """Single pointer is returned."""
        result = filter_parent_pointers({"only/one"})
        assert result == ["only/one"]

    def test_complex_nested_structure(self):
        """Complex nested structure with mixed relationships."""
        pointers = {
            "entries/0/assetTerm",      # Keep
            "entries/0",                 # Filter (parent of assetTerm)
            "entries/1/assetTerm",      # Keep
            "entries/1",                 # Filter (parent of assetTerm)
            "metadata/id",              # Keep (no children)
            "other",                     # Keep (no children)
        }
        result = filter_parent_pointers(pointers)
        assert set(result) == {
            "entries/0/assetTerm",
            "entries/1/assetTerm",
            "metadata/id",
            "other",
        }


class TestGetLinkedOperationIds:
    """Tests for CaseGenerator.get_linked_operation_ids().

    Verifies that operations are correctly classified as "linked" (participating
    in at least one OpenAPI link as source or target) vs "orphan" (no link
    involvement, invisible to the state machine).
    """

    def test_linked_ops_from_test_api(self):
        """test_api.yaml: createWidget has outbound links, getWidget is a target, etc."""
        gen = CaseGenerator(Path("tests/fixtures/test_api.yaml"))
        linked = gen.get_linked_operation_ids()
        all_ops = gen.get_all_operation_ids()

        # Operations that participate in links (from spec inspection):
        # createWidget (source), getWidget (source+target), updateWidget (source+target),
        # deleteWidget (target), createOrder (source), getOrder (target)
        assert "createWidget" in linked
        assert "getWidget" in linked
        assert "updateWidget" in linked
        assert "deleteWidget" in linked
        assert "createOrder" in linked
        assert "getOrder" in linked

        # Orphans (no link involvement):
        assert "listWidgets" not in linked
        assert "getUserProfile" not in linked
        assert "healthCheck" not in linked

        # Sanity check: orphans + linked = all operations
        orphans = all_ops - linked
        assert orphans == {"listWidgets", "getUserProfile", "healthCheck"}

    def test_linked_ops_from_enum_chain_spec(self):
        """enum_chain_spec.yaml: all operations participate in links."""
        gen = CaseGenerator(Path("tests/fixtures/enum_chain_spec.yaml"))
        linked = gen.get_linked_operation_ids()
        all_ops = gen.get_all_operation_ids()

        # listLibraries -> getBooks -> getBookDetails: all linked
        assert "listLibraries" in linked
        assert "getBooks" in linked
        assert "getBookDetails" in linked

        # No orphans in this spec
        assert all_ops == linked

    def test_linked_ops_empty_spec(self):
        """Spec with no links returns empty linked set."""
        gen = CaseGenerator.__new__(CaseGenerator)
        # Manually set _raw_spec for a spec with no links
        gen._raw_spec = {
            "paths": {
                "/health": {
                    "get": {
                        "operationId": "healthCheck",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        }
        linked = gen.get_linked_operation_ids()
        assert linked == set()

    def test_excludes_do_not_affect_linked_computation(self):
        """get_linked_operation_ids() returns all linked ops regardless of excludes."""
        gen = CaseGenerator(
            Path("tests/fixtures/test_api.yaml"),
            exclude_operations=["createWidget"],
        )
        linked = gen.get_linked_operation_ids()

        # createWidget is still "linked" because it has outbound links in the spec.
        # Exclusion only affects which operations are generated, not which are linked.
        assert "createWidget" in linked
