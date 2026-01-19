# Bug Hunt: Case Generator Module

## Confirmed Bugs

### 1. Seed Parameter Does Not Control Randomness

**Location:** `case_generator.py` lines 411-421 (`_generate_for_operation`) and lines 913-920 (`generate_chains`)

**Problem:** The `seed` parameter in `generate()` and `generate_chains()` only controls the `derandomize` flag but does not actually set a seed value. This means ALL non-None seed values produce identical results.

```python
@settings(
    max_examples=max_cases,
    database=None,
    phases=[Phase.generate],
    derandomize=seed is not None,  # Only sets True/False, ignores actual seed value!
)
def collect_cases(case):
    collected.append(case)
```

**Impact:** Users who expect `seed=42` to produce different results than `seed=123` will get identical outputs. The API suggests reproducibility control but doesn't deliver it.

**Evidence:** Hypothesis's `derandomize` setting uses its own fixed internal sequence, not an external seed. To actually use a seed, you need to set the `HYPOTHESIS_SEED` environment variable or use `@reproduce_failure`.

**Reproduction:**
```python
# These will produce IDENTICAL results, not different ones:
generator.generate(max_cases=5, seed=42)
generator.generate(max_cases=5, seed=123)
```

---

### 2. Inconsistent operationId Defaults Break Link Attribution

**Location:** `case_generator.py` - multiple locations use different defaults for missing operationId

**Problem:** Different parts of the code use different default values when operationId is missing:

| Location | Default Value |
|----------|---------------|
| `call()` line 557 | `"unknown"` |
| `generate()` line 370 | `f"{op.method}_{op.path}"` |
| `get_operations()` line 311 | `f"{op.method}_{op.path}"` |
| `_find_link_between()` line 621 | `None` (via `operation.get("operationId")`) |
| `_find_status_code_with_links()` line 701 | `None` (via `operation.get("operationId")`) |

**Impact:** When an operation lacks an explicit operationId:
1. `call()` stores `"unknown"` in `prev_steps`
2. `_find_link_between()` looks for operations where `operation.get("operationId") == "unknown"`
3. But the spec operation has `operationId: None`, so `None != "unknown"` fails the match
4. Link attribution silently fails; `link_source` becomes `None` when it should have a value

**Evidence:** In `_find_link_between()`:
```python
op_id = operation.get("operationId")  # Returns None if missing
if op_id != source_op:  # source_op is "unknown" from call()
    continue  # Always skips because None != "unknown"
```

---

### 3. JSONPointer Escape Sequences Not Handled

**Location:** `case_generator.py` lines 184-218 (`extract_by_jsonpointer`) and lines 858-897 (`_set_by_jsonpointer`)

**Problem:** RFC 6901 JSONPointer specifies escape sequences:
- `~0` represents literal `~`
- `~1` represents literal `/`

The code does not decode these escapes before processing:

```python
def extract_by_jsonpointer(data: Any, pointer: str) -> Any:
    if not pointer:
        return data
    parts = pointer.split("/")  # Does not decode ~0 and ~1
```

**Impact:** Field names containing `/` or `~` cannot be correctly accessed. For example:
- A field literally named `data/id` would be escaped in OpenAPI as `$response.body#/data~1id`
- The code would look for a field named `data~1id` instead of `data/id`

**Evidence:** This matches a known limitation but violates RFC 6901 compliance. The tests do not cover this case.

---

## Likely Bugs

### 4. Excluded Operations Break Link Chain Attribution

**Location:** `case_generator.py` lines 557-584 (`call()` method in `ChainCapturingStateMachine`)

**Problem:** When an operation is excluded, the `call()` method returns early without adding the operation to `prev_steps`:

```python
if op_id in generator_self._exclude:
    # Return synthetic response for excluded operations
    return self._synthetic_response(case)  # Early return!

# ... step_counter, current_steps, and prev_steps are NOT updated
```

**Impact:** If chain A -> (Excluded B) -> C is generated:
1. Operation B is correctly excluded from the chain steps
2. But B is not added to `prev_steps`
3. When operation C runs, `_find_link_between(prev_steps, "C")` won't find B as a source
4. C's `link_source` is `None` even though there's a valid link from B in the spec

**Severity:** Medium - only affects specs with excluded operations that have outgoing links.

---

### 5. Header Case Inconsistency in Synthetic Generation

**Location:** `case_generator.py` lines 811-856 (`_generate_synthetic_headers`)

**Problem:** When collecting header info, the code preserves only the FIRST encountered `original_name`:

```python
for header_ref in generator_self._link_fields.headers:
    lowercase = header_ref.name
    if lowercase in header_info:
        orig_name, current_max = header_info[lowercase]  # Keeps OLD orig_name
    else:
        orig_name = header_ref.original_name  # Only first time
        current_max = 0
```

**Impact:** If an OpenAPI spec inconsistently uses different casings for the same header (e.g., `$response.header.LOCATION` and `$response.header.Location`), the synthetic headers will only use the first casing encountered. Schemathesis might fail to resolve links using the other casing.

**Severity:** Low - only affects specs with inconsistent header casing (which is itself a spec quality issue).

---

## Suspicious Code

### 6. Non-Standard Path Item Keys Could Be Processed as Operations

**Location:** `case_generator.py` lines 140-143 and similar patterns

**Code:**
```python
for method_or_key, operation in path_item.items():
    # Skip non-operation keys like 'parameters', '$ref'
    if not isinstance(operation, dict) or method_or_key.startswith("$"):
        continue
```

**Concern:** The comment says it skips `parameters` and `$ref`, but the code relies on:
1. `isinstance(operation, dict)` to filter non-dict values
2. `startswith("$")` to filter `$ref`

Standard OpenAPI path item keys (`parameters`, `summary`, `description`, `servers`) are filtered because they're not dicts. However, if someone adds a non-standard dict-valued key at the path level (e.g., `x-metadata: {}`), it would be incorrectly processed as an operation.

**Severity:** Very low - would require unusual spec extension usage.

---

### 7. `_find_operation` Uses `None` Comparison

**Location:** `schema_value_generator.py` lines 250-252

**Code:**
```python
if operation.get("operationId") == operation_id:
    return operation
```

**Concern:** If `operation_id` is `"unknown"` (from the inconsistent default bug above), this will never match operations without an explicit operationId (which return `None` from `.get()`).

**Related to:** Confirmed Bug #2

---

## Missing Test Coverage

### 1. No Tests for Operations Without operationId

The test fixtures all have explicit operationIds. There are no tests verifying:
- Chain generation works when operations lack operationId
- Link attribution works for operations without operationId
- Consistent behavior between `generate()` and `generate_chains()` for such operations

### 2. No Tests for Seed Reproducibility

No test verifies that passing different seed values produces different results, nor that passing the same seed produces identical results. This would have caught Bug #1.

### 3. No Tests for JSONPointer Escape Sequences

`TestExtractByJsonpointer` tests nested paths but not:
- Paths with `~0` (escaped tilde)
- Paths with `~1` (escaped slash)
- Field names containing literal `/` or `~`

### 4. No Tests for Excluded Operations with Outgoing Links

Tests for `exclude_operations` verify that excluded operations don't appear in results, but don't verify that subsequent operations still get correct `link_source` attribution.

### 5. No Tests for Empty JSONPointer Parts

What happens with paths like `data//nested` (double slash) or `/leading/slash`? These edge cases aren't tested.

### 6. No Tests for Non-2xx Status Codes in Link Lookup

`_matches_status_code` handles 3XX, 4XX, 5XX wildcards, but tests only cover 2XX scenarios. Links on error responses (e.g., retry-after on 429) aren't tested.

---

## Summary

| Category | Count | Severity |
|----------|-------|----------|
| Confirmed Bugs | 3 | High (seed), High (operationId), Medium (JSONPointer) |
| Likely Bugs | 2 | Medium, Low |
| Suspicious Code | 2 | Very Low |
| Missing Coverage | 6 | - |

**Highest Priority Fix:** Bug #1 (seed parameter) and Bug #2 (operationId inconsistency) affect API correctness and should be addressed first.
