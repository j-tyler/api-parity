# Comparator Analysis Findings

Analysis of `api_parity/comparator.py` against its tests and documentation.

## Finding 1: Import Name Mismatch in Integration Tests

**Severity: HIGH (Broken Test)**

**Location:** `tests/integration/test_comparator_cel.py`, line 25

**Issue:** The integration test file imports a function that does not exist in conftest.py.

```python
# tests/integration/test_comparator_cel.py line 25
from tests.conftest import make_response
```

But `tests/conftest.py` only defines `make_response_case`, not `make_response`:

```python
# tests/conftest.py line 28
def make_response_case(
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    body: Any = None,
    body_base64: str | None = None,
    elapsed_ms: float = 10.0,
) -> ResponseCase:
```

**Evidence:**
- All other test files correctly import `make_response_case`:
  - `tests/test_comparator_body.py` line 4
  - `tests/test_comparator_headers.py` line 4
  - `tests/test_comparator_status.py` line 4
  - `tests/test_comparator_results.py` line 4
  - `tests/test_comparator_jsonpath.py` line 7
  - `tests/test_comparator_rules.py` line 8
- `tests/test_comparator_schema_validation.py` defines its own local `make_response()` function (line 75)

**Impact:** Running `tests/integration/test_comparator_cel.py` would fail with:
```
ImportError: cannot import name 'make_response' from 'tests.conftest'
```

**Fix:** Change line 25 in `tests/integration/test_comparator_cel.py` to:
```python
from tests.conftest import make_response_case as make_response
```
Or update all usages to use `make_response_case` directly.

---

## Finding 2: No Issues Found with Documented Behavior

After thorough analysis, the following documented behaviors match the implementation:

### Short-Circuit Logic (Verified)
- **Documentation (ARCHITECTURE.md):** "Comparison proceeds in order with short-circuit on first mismatch: 1. Status Code -> 2. Headers -> 3. Body"
- **Implementation (comparator.py lines 186-231):** Each phase returns early if `not xxx_result.match`, preventing subsequent phases from executing.
- **Tests:** `test_comparator_status.py::TestComparisonOrder` verifies this at lines 68-109.

### Error Handling (Verified)
- **Documentation:** "Rule errors recorded as mismatches with `rule: 'error: ...'`, not raised as exceptions"
- **Implementation (comparator.py lines 437-451, 512-523, 768-776):** `CELEvaluationError` and `ComparatorConfigError` are caught and recorded in `FieldDifference.rule` as `f"error: {e}"`.
- **Tests:** `test_comparator_rules.py::TestCELErrorHandling` verifies all components catch and record errors.

### Presence Mode Handling (Verified)
- **Documentation (models.py lines 154-160):** Documents PARITY, REQUIRED, FORBIDDEN, OPTIONAL modes.
- **Implementation (comparator.py lines 788-871):** `_check_presence()` correctly implements all four modes with appropriate `skip_value_comparison` logic.
- **Tests:** `test_comparator_body.py::TestPresenceModes` provides comprehensive coverage.

### Header Case-Insensitivity (Verified)
- **Documentation (README.md):** "Headers: case-insensitive"
- **Implementation (comparator.py lines 969-991):** `_get_header_value()` uses `name.lower()` and compares with `key.lower()`.
- **Tests:** `test_comparator_headers.py::TestHeaderComparison::test_header_case_insensitive` verifies this.

### Multi-Value Headers Use First Value (Verified)
- **Documentation (README.md):** "multi-value uses first value only"
- **Implementation (comparator.py line 990):** Returns `values[0]` for multi-value headers.
- **Tests:** `test_comparator_headers.py::TestHeaderComparison::test_header_multi_value_uses_first` verifies this.

### Override Semantics for Rules (Verified)
- **Documentation (CLAUDE.md):** "Override semantics, not merge - operation rules completely override defaults"
- **Implementation (models.py lines 228-233):** `OperationRules` docstring states "If specified, these completely override default_rules for any key defined. There is no deep merging."
- Note: The merging logic itself is in the caller (rules loader), not the Comparator.

### Binary Body Comparison (Verified)
- **Documentation (DESIGN.md):** "If `binary_rule` is not specified, binary bodies are not compared (match by default)"
- **Implementation (comparator.py lines 598-601):** `if binary_rule is None: return ComponentResult(match=True, differences=[])`
- **Tests:** `test_comparator_body.py::TestBinaryBodyComparison::test_no_binary_rule_both_have_binary` verifies this.

### JSONPath Wildcard Detection (Verified)
- **Implementation (comparator.py lines 695-699):** "Detect multi-match paths by actual match count, not by inspecting the path syntax."
- This is more robust than checking for `*` or `..` in the path string. It handles all wildcard types including filter expressions `[?()]`, slices `[0:5]`, and union indices `[0,1,2]`.

---

## Summary

| Finding | Severity | Type | Status |
|---------|----------|------|--------|
| Import name mismatch (`make_response` vs `make_response_case`) | HIGH | Test Bug | Action Required |
| Short-circuit logic | - | Verified | Matches docs |
| Error handling | - | Verified | Matches docs |
| Presence modes | - | Verified | Matches docs |
| Header case-insensitivity | - | Verified | Matches docs |
| Multi-value headers | - | Verified | Matches docs |
| Override semantics | - | Verified | Matches docs |
| Binary body default behavior | - | Verified | Matches docs |
| JSONPath wildcard detection | - | Verified | Robust implementation |

The implementation is well-aligned with documentation. The only bug found is a test file import error that would cause the integration tests to fail to run.
