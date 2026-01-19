# Bug Hunt: Bundle Loader Module

## Confirmed Bugs

### 1. AttributeError when diff.json contains non-dict JSON

**Location:** `bundle_loader.py`, functions `load_bundle()` (line 255) and `detect_bundle_type()` (line 221)

**Description:** Both functions call `_detect_bundle_type_from_data()` which assumes `diff_data` is a dict and calls `.get("type")` on it. However, `json.load()` can return any JSON type (list, string, number, boolean, null), not just dicts.

**Code path:**
```python
# In load_bundle(), line 247-248:
with open(diff_path, encoding="utf-8") as f:
    original_diff = json.load(f)  # Returns Any - could be [1,2,3], "hello", 42, etc.

# Then line 255:
bundle_type = _detect_bundle_type_from_data(original_diff, bundle_path)

# Inside _detect_bundle_type_from_data(), lines 176-178:
if diff_data is not None:
    bundle_type = diff_data.get("type")  # CRASH: 'list' object has no attribute 'get'
```

**Reproduction:** Create a diff.json file containing valid JSON that is not a dict, e.g.:
```json
[1, 2, 3]
```
or
```json
"just a string"
```

**Impact:** `load_bundle()` will raise an unhandled `AttributeError` instead of the expected `BundleLoadError`. The error message will be confusing to users.

**Exception handlers miss this:** Lines 249-252 only catch `json.JSONDecodeError` and `OSError`. `AttributeError` propagates up.

**Same bug in `detect_bundle_type()`:** Lines 214-219 have the same try/except pattern that doesn't catch `AttributeError`.


## Likely Bugs

### 1. Empty JSON pointer silently ignored

**Location:** `bundle_loader.py`, lines 94-97 in `_extract_from_expression()`

**Description:** If a link expression is exactly `"$response.body#/"` (referring to the entire response body root), the extracted `json_pointer` is empty string `""`, which fails the `if json_pointer:` check and is silently skipped.

**Code:**
```python
if expr.startswith("$response.body#/"):
    json_pointer = expr[len("$response.body#/"):]
    if json_pointer:  # Empty string is falsy - root reference skipped!
        link_fields.body_pointers.add(json_pointer)
```

**Impact:** If an OpenAPI spec uses a link that references the entire response body (unusual but valid), replay would fail to extract that variable.

**Verdict:** Likely intentional given the explicit check, but undocumented and untested behavior.


## Suspicious Code

### 1. Broad exception handler masks validation errors

**Location:** `bundle_loader.py`, lines 267-268, 284-285, 297-298

**Description:** The `except Exception` handlers catch and wrap ALL exceptions as `BundleLoadError`. While this provides uniform error handling, it can mask unexpected exceptions and make debugging harder.

**Example:**
```python
except Exception as e:
    raise BundleLoadError(f"Invalid metadata.json: {e}") from e
```

**Assessment:** This is a deliberate design choice to wrap Pydantic validation errors, but it also catches programming errors like `AttributeError`, `KeyError`, etc. Could be improved by catching `pydantic.ValidationError` explicitly.


### 2. Type annotation mismatch

**Location:** `bundle_loader.py`, line 54

**Description:** `original_diff` is typed as `dict[str, Any]` in `LoadedBundle`, but `json.load()` returns `Any`. The type annotation is aspirational, not enforced.

```python
# In LoadedBundle dataclass:
original_diff: dict[str, Any]

# But in load_bundle():
original_diff = json.load(f)  # Returns Any
```

**Assessment:** This is related to the confirmed bug above. If the type annotation is kept as `dict[str, Any]`, validation should enforce it.


## Missing Test Coverage

### 1. No test for non-dict diff.json
- `diff.json` containing a JSON array: `[1, 2, 3]`
- `diff.json` containing a JSON string: `"hello"`
- `diff.json` containing a JSON number: `42`
- `diff.json` containing JSON null: `null`

### 2. No test for "parameters" format in extract_link_fields_from_chain
The function handles two formats:
```python
# New format (tested elsewhere, NOT in test_bundle_loader.py):
"link_source": {"step": 0, "parameters": {"id": "$response.body#/id"}}

# Old format (tested):
"link_source": {"step": 0, "field": "$response.body#/id"}
```
All tests in `test_bundle_loader.py` use the old "field" format. The "parameters" format code path is untested in this module.

### 3. No test for multiple parameters in one link_source
```python
"link_source": {
    "step": 0,
    "parameters": {
        "id": "$response.body#/id",
        "version": "$response.body#/version"
    }
}
```

### 4. No test for non-string values in parameters dict
The code checks `if isinstance(expr, str)` but there's no test verifying the behavior when a parameter value is not a string (e.g., `{"id": 123}`).

### 5. No test for duplicate header references
What happens if the same header is referenced multiple times in a chain? The code appends to a list, so duplicates would accumulate.

### 6. No test for empty bundle directory name
Edge case: what if the bundle directory name is empty or contains special characters?

### 7. No test for symlink handling
- `discover_bundles()` uses `is_dir()` which follows symlinks
- A symlink to a non-bundle directory would be checked and skipped (correct)
- A symlink to a bundle directory would be included (intended?)
- A circular symlink could cause issues

### 8. No test for file permission errors
What happens if `case.json` exists but is not readable? The `OSError` would be caught but wrapped as "Invalid case.json" which is misleading.

### 9. No test for concurrent modification
Race condition: file exists at `is_file()` check but deleted before `open()`.

### 10. No test for empty files
- Empty `diff.json` file (0 bytes) - would cause `json.JSONDecodeError`
- Empty `case.json` file - would cause `json.JSONDecodeError`
Both should be caught but error messages could be verified.
