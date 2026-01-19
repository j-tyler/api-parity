# Bug Hunt: Schema Validator Module

## Confirmed Bugs

### 1. `_find_extra_fields` Does Not Handle Schema Composition (allOf/anyOf/oneOf)

**Location:** `api_parity/schema_validator.py`, lines 451-507

**Description:** The `_find_extra_fields` method only checks `schema.get("properties", {})` at the current level. If a schema uses composition (allOf, anyOf, oneOf), the properties are nested inside those constructs, not at the top level.

**Code Evidence:**
```python
# Line 490
properties = schema.get("properties", {})
defined_fields = set(properties.keys())
```

**Impact:** For any schema like:
```yaml
Widget:
  allOf:
    - $ref: "#/components/schemas/BaseWidget"
    - type: object
      properties:
        extraProp: string
```

After resolution, the schema has:
```python
{
    "allOf": [
        {"type": "object", "properties": {"baseProp": {...}}},
        {"type": "object", "properties": {"extraProp": {...}}}
    ]
}
```

When `_find_extra_fields` is called, `schema.get("properties", {})` returns `{}` (empty - no top-level properties). Result: **EVERY field in the response is incorrectly reported as "extra"**.

**Verification:** No tests exist for allOf/anyOf/oneOf schemas. The test fixture `test_api_schema_validation.yaml` contains no composed schemas.

**Fix Direction:** Collect properties from all branches of allOf (at minimum). anyOf/oneOf require more complex handling since only one branch applies.


### 2. `_allows_additional_properties` Does Not Handle Schema Composition

**Location:** `api_parity/schema_validator.py`, lines 426-449

**Description:** The method only checks the top-level `additionalProperties`:
```python
additional = schema.get("additionalProperties")
```

**Impact:** If any component of an `allOf` has `additionalProperties: false`, this method would still return `True` (because the top level has no `additionalProperties` set).

**Example:**
```yaml
Widget:
  allOf:
    - $ref: "#/components/schemas/StrictBase"  # has additionalProperties: false
    - type: object
      properties:
        name: string
```

The method returns `allows_extra_fields=True`, but jsonschema validation would correctly reject extra fields. This creates inconsistency between:
- `result.extra_fields` list (would include fields that aren't really allowed)
- `result.valid` (correctly reports violations)

**Verification:** No tests for composed schemas with mixed additionalProperties settings.


## Likely Bugs

### 3. Wildcard Status Code Case Sensitivity

**Location:** `api_parity/schema_validator.py`, lines 295-296

**Code:**
```python
wildcard = f"{status_code // 100}XX"
response_def = responses.get(wildcard)
```

**Issue:** This generates uppercase wildcards (`2XX`, `3XX`). The OpenAPI 3.0 specification examples use uppercase (`2XX`), but some tools generate lowercase (`2xx`). If a spec uses lowercase wildcards, they won't be matched.

**Impact:** Schema lookup would fail and return `None`, causing validation to pass (no schema = nothing to violate).

**Confidence:** Medium. The OAS spec examples use uppercase, but I've seen lowercase in the wild. Should defensively check both cases.


### 4. `_resolve_schema_refs` Missing JSON Schema Keywords

**Location:** `api_parity/schema_validator.py`, lines 407-424

**Code:**
```python
for key, value in schema.items():
    if key == "properties" and isinstance(value, dict):
        # handled
    elif key == "items" and isinstance(value, dict):
        # handled
    elif key in ("allOf", "anyOf", "oneOf") and isinstance(value, list):
        # handled
    elif key == "additionalProperties" and isinstance(value, dict):
        # handled
    else:
        result[key] = value  # NOT recursively resolved
```

**Missing Keywords:**
- `patternProperties` - can contain schemas with $refs
- `not` - can contain a schema with $ref
- `if`/`then`/`else` - conditional schemas (JSON Schema Draft 7+)
- `dependencies` (when value is a schema, not array of strings)
- `items` when it's an array (tuple validation) - only dict form is handled

**Impact:** If a spec uses these keywords with $refs, the refs won't be resolved. Subsequent validation would fail or behave incorrectly.

**Confidence:** Medium-Low. These keywords are rare in OpenAPI specs, but valid. The code documents that it uses Draft4Validator, but OpenAPI 3.0 actually aligns with JSON Schema Draft 5/7 in some areas.


## Suspicious Code

### 5. None Body Always Passes Validation

**Location:** `api_parity/schema_validator.py`, lines 152-156

**Code:**
```python
if body is None:
    # If schema expects content, this might be a violation
    # But for simplicity, we treat None body as valid (matches "no content" case)
    return ValidationResult(valid=True)
```

**Issue:** The comment acknowledges this is a simplification. If the schema defines required properties and the body is `None`, this should arguably be a violation. Currently passes silently.

**Impact:** Missing validation for empty response bodies against schemas that expect content.

**Confidence:** Low. This might be intentional design (comment says "for simplicity"). But it could mask real issues where an API returns no body when it should.


### 6. `_resolve_ref` Silently Returns Original on External Refs

**Location:** `api_parity/schema_validator.py`, lines 360-362

**Code:**
```python
if not ref.startswith("#/"):
    # External refs not supported
    return obj
```

**Issue:** External refs (e.g., `$ref: "./common.yaml#/components/schemas/Error"`) are silently ignored. The unresolved `$ref` object is returned, which will likely cause validation to fail in confusing ways or pass incorrectly.

**Impact:** Specs using external refs would have broken validation without any warning.

**Confidence:** Medium. This is documented behavior ("External refs not supported") but the silent failure mode is problematic. A warning or exception would be better.


## Missing Test Coverage

### Critical Gaps

1. **Schema composition tests (allOf/anyOf/oneOf)** - No tests exist. This is required to verify the confirmed bugs.

2. **Wildcard status codes (2XX, 3XX)** - No tests verify that `2XX` responses are matched for status codes 200-299.

3. **`default` response handling** - No tests verify fallback to `default` response when specific code isn't defined.

4. **Recursive schema handling** - No tests for self-referencing schemas like:
   ```yaml
   TreeNode:
     type: object
     properties:
       children:
         type: array
         items:
           $ref: "#/components/schemas/TreeNode"
   ```

### Secondary Gaps

5. **JSON file loading** - Only YAML loading is tested. The code supports JSON but it's not tested.

6. **Malformed schemas** - No tests for invalid JSON Schema (missing type, conflicting keywords).

7. **Path-level $ref** - OpenAPI allows `$ref` at the path item level, not just in schemas.

8. **Response-level $ref** - Tests don't cover `$ref` in the response definition itself.

9. **Deeply nested extra fields** - Tests only go 2-3 levels deep. Deep nesting (5+ levels) might expose issues.

10. **Empty arrays** - No tests for `body = {"widgets": []}` with schema that has `minItems: 1`.

11. **Numeric status code edge cases** - No tests for informational (1xx), redirect (3xx), or server error (5xx) status codes.


## Recommendations

1. **Highest Priority:** Fix `_find_extra_fields` to merge properties from `allOf` branches. This is a confirmed bug affecting any spec using schema composition.

2. **High Priority:** Add tests for schema composition before fixing, to prevent regression.

3. **Medium Priority:** Add case-insensitive wildcard matching (`2XX` and `2xx`).

4. **Medium Priority:** Log a warning for external $refs instead of silently returning unresolved.
