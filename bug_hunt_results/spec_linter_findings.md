# Bug Hunt: Spec Linter Module

## Confirmed Bugs

### 1. Incorrect Wildcard Status Code Categorization

**Location:** `spec_linter.py`, line 426 in `_check_non_200_status_code_links`

**Code:**
```python
code_2xx = [l for l in non_200_links if l["status_code"].endswith("XX")]
```

**Bug:** This line incorrectly categorizes ANY wildcard status code (3XX, 4XX, 5XX) as a "2XX wildcard". The `.endswith("XX")` check doesn't verify the first character is "2".

**Impact:** If a spec has links on a "3XX" (redirect) or "4XX" (client error) status code, the linter would report them as "2XX wildcards" in the info message:
```
Found N links on non-200 status codes: ... M on wildcards (2XX) ...
```

**Evidence:** Verified by running Python:
```
>>> "3XX".endswith("XX")
True
>>> "4XX".endswith("XX")
True
>>> "5XX".endswith("XX")
True
```
All non-2XX wildcards would be incorrectly categorized as "2XX wildcards".

**Fix:** Change to:
```python
code_2xx = [l for l in non_200_links if l["status_code"].endswith("XX") and l["status_code"].startswith("2")]
```

### 2. Quoted YAML Keys Not Handled in Duplicate Link Detection

**Location:** `spec_linter.py`, lines 564-566 in `_check_duplicate_link_names`

**Code:**
```python
if ":" in stripped:
    key = stripped.split(":")[0].strip()
```

**Bug:** The code doesn't strip quotes from YAML keys. In YAML, `GetItem:` and `"GetItem":` are equivalent keys, but this code treats them as different.

**Impact:** False negative - the linter will NOT detect duplicate link names when one is quoted and one is unquoted:
```yaml
links:
  GetItem:
    operationId: foo
  "GetItem":
    operationId: bar
```
YAML parsers silently keep only the second definition (the entire purpose of this check), but the linter sees `GetItem` and `"GetItem"` as distinct keys.

**Evidence:** Verified by running Python:
```
>>> '"GetItem":'.split(":")[0].strip()
'"GetItem"'
>>> 'GetItem:'.split(":")[0].strip()
'GetItem'
```
The quoted form preserves quotes, so `"GetItem"` and `GetItem` are treated as different keys even though YAML considers them identical.

**Fix:** Strip quotes from extracted keys:
```python
key = stripped.split(":")[0].strip().strip('"').strip("'")
```

---

## Likely Bugs

### 3. External operationRef Values Cause False Positive Errors

**Location:** `spec_linter.py`, lines 207-221 in `_check_link_connectivity`

**Code:**
```python
if target_op not in self._operation_ids:
    # operationRef is a JSON pointer, not validated here
    if not target_op.startswith("#"):
        result.add(LintMessage(
            level="error",
            code="invalid-link-target",
            ...
        ))
    continue
```

**Issue:** The code only considers `operationRef` values starting with `#` as valid references. However, OpenAPI 3.x allows external references like:
- `./other-spec.yaml#/paths/~1items/get`
- `https://api.example.com/spec.yaml#/paths/~1users/post`

**Impact:** Valid external references are incorrectly flagged as "invalid-link-target" errors.

**Evidence:** From OpenAPI 3.0.3 spec: "A relative or absolute URI reference to an OAS operation." External URIs are valid but would fail the `startswith("#")` check.

**Mitigating factor:** External references are relatively rare in practice. Most specs use `operationId` instead.

---

## Suspicious Code

### 4. OpenAPI Extension Fields (x-*) Not Filtered in Operation Processing

**Location:** `spec_linter.py`, lines 139-141 in `_build_operation_index`

**Code:**
```python
# Skip non-dict entries and OpenAPI extension fields (e.g., $ref, x-custom)
if not isinstance(operation, dict) or method.startswith("$"):
    continue
```

**Issue:** The comment mentions filtering `x-custom` but the code only checks for `$` prefix, not `x-` prefix. OpenAPI path items can have `x-*` extension fields that are dicts.

**Impact:** If a path item has an `x-*` extension that is a dict (e.g., `x-internal-config: {operationId: "test", ...}`), it would be processed as an operation. However, this is low-risk because:
1. Extension dicts rarely have `operationId` fields
2. Even if processed, they'd just add to `_operation_ids` set harmlessly

**Code pattern suggests intent to filter `x-*`:** The comment explicitly mentions `x-custom` but the implementation doesn't match the intent.

### 5. Duplicate Link Check Only Works for YAML, Not JSON Specs

**Location:** `spec_linter.py`, `_check_duplicate_link_names` method

**Issue:** The raw text parsing logic looks for YAML patterns like `links:` with indentation tracking. For JSON spec files, this check will not work:
- JSON uses `"links": {` not `links:`
- JSON has different structural patterns

**Impact:** Duplicate link names in JSON specs won't be detected. However:
- JSON parsers also silently dedupe keys, so the problem exists
- Most OpenAPI specs are YAML, so this affects a minority of users

---

## Missing Test Coverage

### No Tests For:

1. **JSON format specs** - All tests use YAML files. No test verifies JSON spec parsing or that the duplicate check fails gracefully for JSON.

2. **Wildcard status codes beyond 2XX** - No test with "3XX", "4XX", "5XX" status codes to verify correct categorization.

3. **External operationRef** - No test with `operationRef` pointing to external files (e.g., `./other.yaml#/...`).

4. **Quoted YAML keys** - No test with quoted link names like `"GetItem":` to verify duplicate detection.

5. **x-extension fields at path item level** - No test with `x-*` extension dicts in path items.

6. **operationId missing from some operations** - Tests don't verify behavior when operations lack `operationId` (valid in OpenAPI, the operation just can't be a link target).

7. **Mixed quoted/unquoted duplicate keys** - The most important missing test for bug #2.

8. **Tab/space mixing in YAML** - No test verifying behavior when indentation uses tabs or mixed tabs/spaces.

9. **Multiline strings in YAML that contain key-like patterns** - Edge case where multiline string content looks like YAML keys.

10. **Empty links section** - `links: {}` or `links:` with no children.
