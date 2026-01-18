# Phase 2: Schema-Aware Synthetic Value Generation

## Problem Statement

The `graph-chains --generated` command and `explore --log-chains` produce different output because chain discovery fails when link-extracted values don't satisfy target parameter constraints.

**Root cause:** `_generate_synthetic_body()` uses `uuid.uuid4()` placeholders for all link-referenced fields. When a target parameter has constraints (enum, pattern, type), the UUID placeholder fails validation and the chain is not discovered.

**Example from user-provided spec:**
```yaml
LibraryId:
  type: string
  enum:
    - main_branch
    - downtown_branch
```

Chain discovery generates `{"libraries": [{"id": "a1b2c3d4-..."}]}`, but the next operation requires `library_id` to be one of the enum values. Schemathesis rejects the chain.

## Goal

Make `graph-chains --generated` and `explore --stateful --log-chains` produce equivalent chains (within random variance) by generating schema-compliant synthetic values during chain discovery.

## Architecture

### Current Flow (Broken for Enums)

```
_synthetic_response(case)
    └── _generate_synthetic_body()
            └── For each link_field in body_pointers:
                    set value = str(uuid.uuid4())  ❌ Ignores schema
```

### Proposed Flow

```
_synthetic_response(case)
    └── _generate_synthetic_body(operation_id)
            └── Get response schema for operation_id + status_code
            └── For each link_field in body_pointers:
                    schema = navigate_to_field_schema(response_schema, field_path)
                    value = generate_schema_compliant_value(schema)
```

## Implementation Plan

### Step 1: Create Schema Value Generator Module

Create `api_parity/schema_value_generator.py` with:

```python
class SchemaValueGenerator:
    """Generates values that satisfy OpenAPI schema constraints.

    Used for synthetic response generation during chain discovery.
    Values are placeholders, not realistic data - they just need to
    pass schema validation so Schemathesis can traverse links.
    """

    def __init__(self, spec: dict):
        """Initialize with parsed OpenAPI spec for $ref resolution."""
        self._spec = spec

    def generate(self, schema: dict) -> Any:
        """Generate a value satisfying the schema constraints.

        Priority order:
        1. enum - pick first enum value
        2. const - use const value
        3. format - generate format-specific value
        4. pattern - generate matching string (if feasible)
        5. type - generate type-appropriate placeholder
        6. default - uuid.uuid4() fallback
        """
        ...

    def navigate_to_field(self, schema: dict, pointer: str) -> dict | None:
        """Navigate response schema to find field's schema at JSON pointer.

        Handles:
        - Object properties: "data" -> schema.properties.data
        - Array items: "items/0" -> schema.items (arrays are homogeneous)
        - Nested paths: "data/items/0/id" -> schema.properties.data.items.properties.id
        """
        ...
```

### Step 2: Value Generation Logic

Handle constraint types in priority order:

| Constraint | Generation Strategy |
|------------|---------------------|
| `enum: [a, b, c]` | Return first enum value (`a`) |
| `const: "fixed"` | Return const value |
| `format: uuid` | Generate `str(uuid.uuid4())` |
| `format: date-time` | Generate ISO timestamp |
| `format: date` | Generate ISO date |
| `format: uri` | Generate `http://placeholder/{uuid}` |
| `format: email` | Generate `placeholder@example.com` |
| `type: integer` | Generate `1` |
| `type: number` | Generate `1.0` |
| `type: boolean` | Generate `true` |
| `type: string` + `pattern` | Attempt regex-based generation (or fallback) |
| `type: string` | Generate `str(uuid.uuid4())` |
| `type: array` | Generate `[<item>]` with one generated item |
| `type: object` | Generate `{}` with required properties |
| No constraints | Generate `str(uuid.uuid4())` |

**Note:** Enum takes priority over format/type. A field with `enum: [a, b]` and `format: uuid` should return `a`, not a UUID.

### Step 3: Integrate into Case Generator

Modify `case_generator.py`:

1. **Pass operation context to synthetic generation:**
   ```python
   def _synthetic_response(self, case) -> SchemathesisResponse:
       op_id = case.operation.definition.raw.get("operationId", "unknown")
       status_code = 201 if case.method == "POST" else 200
       synthetic_body = self._generate_synthetic_body(op_id, status_code)
       ...
   ```

2. **Use schema-aware body generation:**
   ```python
   def _generate_synthetic_body(self, operation_id: str, status_code: int) -> dict:
       # Get response schema for this operation
       schema = self._get_response_schema(operation_id, status_code)

       body = {}
       for field_pointer in generator_self._link_fields.body_pointers:
           if schema:
               field_schema = self._value_gen.navigate_to_field(schema, field_pointer)
               value = self._value_gen.generate(field_schema) if field_schema else str(uuid.uuid4())
           else:
               value = str(uuid.uuid4())
           self._set_by_jsonpointer(body, field_pointer, value)

       return body
   ```

3. **Initialize SchemaValueGenerator in CaseGenerator:**
   ```python
   def __init__(self, spec_path, ...):
       ...
       self._value_gen = SchemaValueGenerator(self._raw_spec)
   ```

### Step 4: Schema Navigation for Nested Paths

The `navigate_to_field` method must handle JSON pointer paths like:
- `id` → `schema.properties.id`
- `libraries/0/id` → `schema.properties.libraries.items.properties.id`
- `data/items/0/name` → `schema.properties.data.properties.items.items.properties.name`

**Algorithm:**
```python
def navigate_to_field(self, schema: dict, pointer: str) -> dict | None:
    schema = self._resolve_refs(schema)
    parts = pointer.split("/")
    current = schema

    for part in parts:
        current = self._resolve_refs(current)

        if part.isdigit():
            # Array index - go to items schema
            current = current.get("items", {})
        else:
            # Object property
            props = current.get("properties", {})
            current = props.get(part, {})

        if not current:
            return None

    return self._resolve_refs(current)
```

### Step 5: Header Schema Support (Extension)

Similarly update `_generate_synthetic_headers()` to use schema-aware generation for response headers defined in the spec.

OpenAPI response headers are defined at:
```yaml
responses:
  200:
    headers:
      X-Request-Id:
        schema:
          type: string
          format: uuid
```

## Integration Test

### Test Spec: `tests/fixtures/enum_chain_spec.yaml`

```yaml
openapi: "3.0.3"
info:
  title: Library System API (Chain Generation Test)
  version: "1.0.0"

paths:
  /libraries:
    get:
      operationId: listLibraries
      responses:
        "200":
          description: List of libraries
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/LibraryList"
          links:
            GetBooksFromLibrary:
              operationId: getBooks
              parameters:
                library_id: "$response.body#/libraries/0/id"

  /libraries/{library_id}/books:
    get:
      operationId: getBooks
      parameters:
        - name: library_id
          in: path
          required: true
          schema:
            $ref: "#/components/schemas/LibraryId"
      responses:
        "200":
          description: List of books
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/BookList"
          links:
            GetBookDetails:
              operationId: getBookDetails
              parameters:
                library_id: "$request.path.library_id"
                book_id: "$response.body#/books/0/id"

  /libraries/{library_id}/books/{book_id}:
    get:
      operationId: getBookDetails
      parameters:
        - name: library_id
          in: path
          required: true
          schema:
            $ref: "#/components/schemas/LibraryId"
        - name: book_id
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: Book details
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Book"

components:
  schemas:
    LibraryId:
      type: string
      enum:
        - main_branch
        - downtown_branch

    Library:
      type: object
      required: [id, name]
      properties:
        id:
          $ref: "#/components/schemas/LibraryId"
        name:
          type: string

    LibraryList:
      type: object
      required: [libraries]
      properties:
        libraries:
          type: array
          items:
            $ref: "#/components/schemas/Library"

    Book:
      type: object
      required: [id, title]
      properties:
        id:
          type: string
        title:
          type: string

    BookList:
      type: object
      required: [books]
      properties:
        books:
          type: array
          items:
            $ref: "#/components/schemas/Book"
```

### Test: `tests/integration/test_enum_chain_generation.py`

```python
"""Integration test: chain generation with enum-constrained parameters.

Verifies that schema-aware synthetic value generation enables chain discovery
when link-extracted values must satisfy enum constraints on target parameters.
"""

import pytest
from pathlib import Path

from api_parity.case_generator import CaseGenerator


@pytest.fixture
def enum_chain_spec(tmp_path: Path) -> Path:
    """Create the enum chain test spec."""
    spec_content = '''...'''  # YAML content from above
    spec_path = tmp_path / "enum_chain_spec.yaml"
    spec_path.write_text(spec_content)
    return spec_path


class TestEnumChainGeneration:
    """Test chain generation with enum constraints."""

    def test_discovers_full_chain_with_enum_constraint(self, enum_chain_spec: Path):
        """Verify 3-step chain is discovered despite enum constraint on library_id.

        Expected chain: listLibraries -> getBooks -> getBookDetails

        Before Phase 2: Chain breaks at getBooks because synthetic library_id
        is a UUID that doesn't match enum [main_branch, downtown_branch].

        After Phase 2: Synthetic response uses "main_branch" (first enum value),
        allowing the full chain to be discovered.
        """
        generator = CaseGenerator(enum_chain_spec)
        chains = generator.generate_chains(max_chains=50, seed=42)

        # Find chains that include all three operations
        full_chains = []
        for chain in chains:
            op_ids = [step.request_template.operation_id for step in chain.steps]
            if "listLibraries" in op_ids and "getBooks" in op_ids and "getBookDetails" in op_ids:
                full_chains.append(chain)

        # At least one full chain should be discovered
        assert len(full_chains) > 0, (
            f"Expected at least one chain with listLibraries -> getBooks -> getBookDetails. "
            f"Got {len(chains)} chains: {[self._chain_summary(c) for c in chains]}"
        )

        # Verify chain structure
        chain = full_chains[0]
        assert len(chain.steps) >= 3

        # First step should be listLibraries (no link source)
        assert chain.steps[0].request_template.operation_id == "listLibraries"
        assert chain.steps[0].link_source is None

        # Second step should be getBooks via link
        books_step = next(s for s in chain.steps if s.request_template.operation_id == "getBooks")
        assert books_step.link_source is not None
        assert books_step.link_source["source_operation"] == "listLibraries"

        # Third step should be getBookDetails via link
        details_step = next(s for s in chain.steps if s.request_template.operation_id == "getBookDetails")
        assert details_step.link_source is not None
        assert details_step.link_source["source_operation"] == "getBooks"

    def test_synthetic_value_uses_enum(self, enum_chain_spec: Path):
        """Verify synthetic body uses enum value, not UUID placeholder.

        This test inspects the synthetic value generation directly.
        """
        generator = CaseGenerator(enum_chain_spec)

        # The link_fields should include libraries/0/id
        link_fields = generator.get_link_fields()
        assert "libraries/0/id" in link_fields.body_pointers

        # Generate chains to trigger synthetic response generation
        chains = generator.generate_chains(max_chains=5, seed=42)

        # If chains include getBooks, the synthetic value worked
        has_get_books = any(
            any(s.request_template.operation_id == "getBooks" for s in c.steps)
            for c in chains
        )
        assert has_get_books, "getBooks should be reachable via link from listLibraries"

    def _chain_summary(self, chain) -> str:
        """Format chain as operation sequence for debugging."""
        ops = [s.request_template.operation_id for s in chain.steps]
        return " -> ".join(ops)


class TestSchemaValueGenerator:
    """Unit tests for schema value generation."""

    def test_enum_takes_priority(self):
        """Enum constraint should produce enum value, not type-based value."""
        from api_parity.schema_value_generator import SchemaValueGenerator

        gen = SchemaValueGenerator({})

        schema = {
            "type": "string",
            "format": "uuid",
            "enum": ["option_a", "option_b"]
        }

        value = gen.generate(schema)
        assert value == "option_a"  # First enum value

    def test_integer_type(self):
        """Integer type should produce integer."""
        from api_parity.schema_value_generator import SchemaValueGenerator

        gen = SchemaValueGenerator({})
        schema = {"type": "integer"}

        value = gen.generate(schema)
        assert isinstance(value, int)

    def test_uuid_format(self):
        """UUID format should produce valid UUID string."""
        from api_parity.schema_value_generator import SchemaValueGenerator
        import uuid

        gen = SchemaValueGenerator({})
        schema = {"type": "string", "format": "uuid"}

        value = gen.generate(schema)
        # Should be parseable as UUID
        uuid.UUID(value)

    def test_navigate_nested_path(self):
        """Navigate through object and array schemas."""
        from api_parity.schema_value_generator import SchemaValueGenerator

        spec = {
            "components": {
                "schemas": {
                    "LibraryId": {"type": "string", "enum": ["main", "downtown"]}
                }
            }
        }
        gen = SchemaValueGenerator(spec)

        response_schema = {
            "type": "object",
            "properties": {
                "libraries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"$ref": "#/components/schemas/LibraryId"}
                        }
                    }
                }
            }
        }

        # Navigate to libraries/0/id
        field_schema = gen.navigate_to_field(response_schema, "libraries/0/id")

        assert field_schema is not None
        assert field_schema.get("enum") == ["main", "downtown"]
```

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `api_parity/schema_value_generator.py` | Create | New module for schema-aware value generation |
| `api_parity/case_generator.py` | Modify | Integrate SchemaValueGenerator into synthetic response |
| `tests/fixtures/enum_chain_spec.yaml` | Create | Test spec with enum constraints |
| `tests/integration/test_enum_chain_generation.py` | Create | Integration tests for enum chain discovery |
| `tests/unit/test_schema_value_generator.py` | Create | Unit tests for value generator |
| `DESIGN.md` | Update | Document Phase 2 completion |

## Edge Cases to Handle

1. **Circular $ref** - Already handled in SchemaValidator, reuse pattern
2. **No response schema defined** - Fall back to UUID placeholder
3. **allOf/anyOf/oneOf** - For now, use first schema in composition
4. **Empty enum array** - Fall back to type-based generation
5. **Nested arrays** - `items/0/subitems/0/id` should navigate correctly
6. **Missing intermediate schemas** - Return None, use fallback

## Verification Criteria

After implementation:

1. **`graph-chains --generated`** shows chains including `listLibraries -> getBooks -> getBookDetails`
2. **`explore --stateful --log-chains`** logs the same chains being executed
3. **Integration test passes** confirming enum constraint handling
4. **Existing tests pass** with no regressions

## Implementation Order

1. Create `schema_value_generator.py` with unit tests
2. Add `navigate_to_field` with $ref resolution
3. Integrate into `_generate_synthetic_body()`
4. Create enum chain spec fixture
5. Write integration test
6. Verify with CLI commands
7. Update DESIGN.md
