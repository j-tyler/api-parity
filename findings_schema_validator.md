# SchemaValidator Analysis Findings

Analysis of `api_parity/schema_validator.py` against its tests and documentation.

## Summary

Found **3 confirmed issues** and **2 test coverage gaps** where the implementation either doesn't match documented behavior or lacks test coverage for documented features.

---

## Issue 1: `_find_extra_fields` Does Not Handle allOf/anyOf/oneOf Composition Schemas

**Severity:** Medium - Incorrect behavior for composition schemas

**Location:** `api_parity/schema_validator.py`, lines 451-507

**Documentation states:**
> ARCHITECTURE.md line 827-828: "additionalProperties: false -> Extra fields are schema violations (errors)"

**Problem:**
The `_find_extra_fields` method only checks direct `properties` on a schema, not properties defined within `allOf`, `anyOf`, or `oneOf` composition schemas.

```python
# Lines 489-491
properties = schema.get("properties", {})  # Only checks top-level properties!
defined_fields = set(properties.keys())
```

**Example of broken behavior:**

Schema using `allOf`:
```yaml
MyType:
  allOf:
    - type: object
      properties:
        field_a: {type: string}
    - type: object
      properties:
        field_b: {type: string}
```

When `_find_extra_fields` processes this schema:
- It calls `schema.get("properties", {})` which returns `{}` (empty)
- ALL fields in the response body are incorrectly reported as extra fields

**Test gap:** No tests for `allOf`/`anyOf`/`oneOf` schemas in `test_schema_validator.py`.

**Verification:**
```bash
grep -n "allOf\|anyOf\|oneOf" tests/test_schema_validator.py
# Returns no matches
```

---

## Issue 2: `_allows_additional_properties` Does Not Handle allOf/anyOf/oneOf Composition Schemas

**Severity:** Medium - Incorrect behavior for composition schemas

**Location:** `api_parity/schema_validator.py`, lines 426-449

**Problem:**
The `_allows_additional_properties` method only checks the top-level `additionalProperties` setting. If a schema uses composition keywords like `allOf`, the `additionalProperties` setting inside those sub-schemas is ignored.

```python
# Lines 442-449
additional = schema.get("additionalProperties")  # Only top-level!

if additional is False:
    return False

return True
```

**Example of broken behavior:**

Schema:
```yaml
MyType:
  allOf:
    - $ref: "#/components/schemas/Base"
    - type: object
      additionalProperties: false  # This is ignored!
      properties:
        extra_field: {type: string}
```

The function returns `True` (allows extra) because the top-level schema doesn't have `additionalProperties` set, even though the `allOf` item explicitly forbids them.

**Note:** The jsonschema validator will still catch violations during validation. However, the `allows_extra_fields` flag will be incorrect, causing `_find_extra_fields` to be called when it shouldn't be.

---

## Issue 3: Unresolved `$ref` in Recursive Schemas May Cause Undefined Behavior

**Severity:** Low - Edge case with recursive schemas

**Location:** `api_parity/schema_validator.py`, lines 398-405

**Documentation states:**
> ARCHITECTURE.md line 852: "Recursive schema refs (e.g., `Node` with `children: $ref Node`) are detected and left unresolved to prevent infinite loops"

**Problem:**
When a cyclic `$ref` is detected, the code returns the unresolved schema:

```python
# Lines 398-405
if "$ref" in schema:
    ref = schema["$ref"]
    if ref in visited:
        # Cycle detected - return unresolved to break recursion
        return schema  # <-- Contains unresolved $ref
    resolved = self._resolve_ref(schema)
    return self._resolve_schema_refs(resolved, visited | {ref})
```

The unresolved `$ref` (e.g., `"#/components/schemas/TreeNode"`) is then passed to jsonschema's `Draft4Validator`. The validator receives a schema with a `$ref` that it cannot resolve (the path is relative to the OpenAPI spec root, not the schema fragment).

**Potential outcomes:**
1. jsonschema may ignore the unresolved `$ref` and validate nothing at that level
2. jsonschema may raise an error about unresolvable reference
3. jsonschema may treat it as an invalid schema

**Test gap:** No tests verify behavior with recursive/cyclic schemas.

**Verification:**
```bash
grep -rn "recursive\|cyclic\|cycle\|TreeNode" tests/test_schema_validator.py
# Returns no matches
```

---

## Test Coverage Gap 1: Wildcard Status Codes Not Tested in SchemaValidator

**Location:** `api_parity/schema_validator.py`, lines 288-298

**Implementation supports:**
```python
# Lines 292-298
status_str = str(status_code)
response_def = responses.get(status_str)
if response_def is None:
    wildcard = f"{status_code // 100}XX"  # 2XX, 3XX, etc.
    response_def = responses.get(wildcard)
if response_def is None:
    response_def = responses.get("default")
```

**Problem:**
The wildcard status code logic (2XX, 3XX, etc.) is implemented but NOT tested in `test_schema_validator.py`.

**Note:** A similar test exists in `test_schema_value_generator.py` (line 403), but that tests a DIFFERENT class with duplicated logic. The SchemaValidator's wildcard handling has no test coverage.

**Verification:**
```bash
grep -n "2XX\|3XX\|wildcard" tests/test_schema_validator.py
# Returns no matches
```

---

## Test Coverage Gap 2: Default Response Fallback Not Tested

**Location:** `api_parity/schema_validator.py`, line 298

**Implementation supports:**
```python
if response_def is None:
    response_def = responses.get("default")
```

**Problem:**
The `default` response fallback is implemented but NOT tested in `test_schema_validator.py`.

**Verification:** The test fixture `test_api_schema_validation.yaml` does not define any `default` responses to test this code path.

---

## Non-Issues (Verified as Intentional)

### None Body Passes Validation
**Location:** Lines 152-156

The code explicitly passes `None` bodies:
```python
if body is None:
    # If schema expects content, this might be a violation
    # But for simplicity, we treat None body as valid (matches "no content" case)
    return ValidationResult(valid=True)
```

This is tested in `test_none_body_passes_validation` (test line 283-286) and the comment documents this as intentional simplification.

### Extra Fields in Nested Strict Schemas
**Location:** Lines 192-193

When top-level schema allows extra fields but nested schemas don't, extra fields in nested schemas are both:
1. Caught as violations by jsonschema (correct)
2. Potentially added to `extra_fields` list by `_find_extra_fields`

Per ARCHITECTURE.md line 853: "`extra_fields` tracking only considers root-level `additionalProperties`; nested restrictions are enforced by jsonschema validation"

This is documented behavior, not a bug.

---

## Code Quality Notes (Not Bugs)

### Code Duplication
`SchemaValidator._extract_response_schema()` and `SchemaValueGenerator.get_response_schema()` contain nearly identical logic for:
- Finding operations by operationId
- Status code lookup (exact -> wildcard -> default)
- $ref resolution

This could be refactored into shared code, but is noted in DESIGN.md line 679:
> "SchemaValidator already has this logic; could refactor for reuse"

---

## Recommendations

1. **Add tests for allOf/anyOf/oneOf schemas** - Create test fixtures with composition schemas and verify:
   - Extra field detection works correctly
   - `additionalProperties` is respected within composition items

2. **Add tests for recursive/cyclic schemas** - Verify behavior when schemas reference themselves

3. **Add tests for wildcard status codes in SchemaValidator** - Even though similar tests exist for SchemaValueGenerator

4. **Add tests for default response fallback** - Test that `default` response is used when specific codes aren't defined

5. **Consider handling composition schemas in `_find_extra_fields`** - Either:
   - Walk into `allOf`/`anyOf`/`oneOf` to collect all defined properties
   - Or document this as a known limitation
