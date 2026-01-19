# Bug Hunt: Comparator Module

## Confirmed Bugs

### 1. Inconsistent Wildcard Count Mismatch Detection (HIGH SEVERITY)

**Location:** `comparator.py`, lines 698-728, `_compare_jsonpath` method

**Description:** The wildcard detection logic uses `is_multi_match = len(matches_a) > 1 or len(matches_b) > 1`. This creates inconsistent behavior when comparing arrays with wildcard paths:

- If A has 2 matches and B has 1 match: `is_multi_match = True`, reports `wildcard_count_mismatch`
- If A has 1 match and B has 0 matches: `is_multi_match = False`, reports `presence:parity`

Both cases represent the same semantic issue (different number of array elements matching a wildcard path), but they produce different error messages.

**Example:**
```python
# With path "$.items[*].id"
# Response A: {"items": [{"id": 1}]}  -> 1 match
# Response B: {"items": []}           -> 0 matches

# Current behavior: is_multi_match = (1 > 1) or (0 > 1) = False
# Goes through single-value path, reports "presence:parity" instead of "wildcard_count_mismatch"
```

**Fix:** Change the condition to also consider the case where one has matches and the other has zero:
```python
is_multi_match = len(matches_a) > 1 or len(matches_b) > 1 or (len(matches_a) == 1 and len(matches_b) == 0) or (len(matches_a) == 0 and len(matches_b) == 1)
```

Or more simply, detect wildcards by checking if match count differs when the path contains wildcard syntax.

---

### 2. Type Annotation Mismatch in `make_response_case` (LOW SEVERITY)

**Location:** `tests/conftest.py`, line 32

**Description:** The `headers` parameter has incorrect type annotation:
```python
def make_response_case(
    ...
    headers: dict[str, str] | None = None,  # WRONG
    ...
) -> ResponseCase:
```

The `ResponseCase.headers` field expects `dict[str, list[str]]` (headers are arrays to support repeated headers), but the helper function declares `dict[str, str]`.

**Impact:** Tests work at runtime because they pass the correct type, but static type checkers would not catch misuse. This is misleading documentation.

**Fix:** Change the type annotation to:
```python
headers: dict[str, list[str]] | None = None,
```

---

## Likely Bugs

### 1. Overly Permissive Test Assertion for String Escaping

**Location:** `tests/test_comparator_rules.py`, lines 64-67, `test_string_param_escaped`

**Description:** The test accepts two different outcomes for backslash escaping:
```python
assert '"^[a-z]+-\\\\d+$"' in expr or '"^[a-z]+-\\d+$"' in expr
```

This accepts either properly escaped (`\\d` in CEL) or unescaped (`\d` in CEL) versions. Based on the code in `_expand_predefined`, only the first form (properly escaped) should be correct.

**Impact:** If the escaping logic breaks, the test would still pass with the second alternative.

**Fix:** Remove the `or` clause and assert only the correct escaped form:
```python
assert '"^[a-z]+-\\\\d+$"' in expr
```

---

## Suspicious Code

### 1. Parameter Substitution Uses Global String Replace

**Location:** `comparator.py`, lines 924-936, `_expand_predefined` method

**Description:** The parameter substitution uses `str.replace()` which replaces ALL occurrences:
```python
if isinstance(value, str):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    expr = expr.replace(param, f'"{escaped}"')
else:
    expr = expr.replace(param, str(value))
```

If a parameter name appears elsewhere in the expression (not as a parameter placeholder), it would be incorrectly replaced.

**Current Risk:** Low, because current parameter names (`tolerance`, `seconds`, `pattern`, `length`, etc.) are unlikely to appear in variable names or CEL built-ins.

**Potential Future Bug:** If someone adds a predefined with a param name that collides with CEL syntax (e.g., a param named `size` when the expression uses `size(a)`), the replacement would corrupt the expression.

**Recommendation:** Consider using a more robust substitution mechanism (e.g., regex with word boundaries, or template syntax like `${param}`).

---

### 2. Silent Skip of Invalid Extra Field Paths

**Location:** `comparator.py`, lines 359-361, `_compare_extra_fields` method

**Description:** When comparing extra fields, invalid JSONPath expressions are silently skipped:
```python
try:
    matches_a = self._expand_jsonpath(body_a, path)
    matches_b = self._expand_jsonpath(body_b, path)
except JSONPathError:
    # Skip invalid paths
    continue
```

This could hide issues if the schema validator returns malformed paths.

---

## Missing Test Coverage

### 1. No Test for Wildcard with 1 vs 0 Matches

**Issue:** There is no test case where a wildcard path produces exactly 1 match in one response and 0 matches in the other. This is the edge case that triggers the confirmed bug above.

**Suggested Test:**
```python
def test_wildcard_one_vs_zero_matches(self, comparator):
    """Wildcard path with 1 match vs 0 matches should report count mismatch."""
    response_a = make_response_case(body={"items": [{"id": 1}]})  # 1 match
    response_b = make_response_case(body={"items": []})           # 0 matches
    rules = OperationRules(
        body=BodyRules(
            field_rules={"$.items[*].id": FieldRule(predefined="exact_match")}
        ),
    )

    result = comparator.compare(response_a, response_b, rules)

    assert result.match is False
    # Should be wildcard_count_mismatch, not presence:parity
    assert "wildcard_count_mismatch" in result.details["body"].differences[0].rule
```

### 2. No Test for Recursive Descent with Different Structures

**Issue:** The `test_recursive_descent_detected_as_wildcard` test only covers the case where both responses have identical structure. There is no test for when the structures differ (different number of nested `value` fields).

**Suggested Test:**
```python
def test_recursive_descent_different_structure(self, comparator):
    """Recursive descent with different nesting depth reports count mismatch."""
    response_a = make_response_case(
        body={
            "level1": {"value": 1},
            "level2": {"nested": {"value": 2}}
        }
    )  # 2 matches
    response_b = make_response_case(
        body={"only": {"value": 1}}
    )  # 1 match
    rules = OperationRules(
        body=BodyRules(
            field_rules={"$..value": FieldRule(predefined="exact_match")}
        ),
    )

    result = comparator.compare(response_a, response_b, rules)

    assert result.match is False
    assert "wildcard_count_mismatch" in result.details["body"].differences[0].rule
```

### 3. No Test for Filter Expressions

**Issue:** JSONPath filter expressions like `$.items[?(@.active == true)].id` are not tested. These could have different match counts in A vs B based on field values, not just array length.

### 4. No Test for CEL Evaluation Timeout

**Issue:** Per CLAUDE.md, the CEL evaluator has a 5-second timeout. There is no test verifying the comparator handles timeout errors correctly.

### 5. No Test for Multiple Missing Parameters

**Issue:** The test `test_missing_required_param` only tests one missing parameter. There is no test for a predefined requiring multiple parameters where more than one is missing.

### 6. No Test for Empty String in JSONPath Field Name

**Issue:** No test for edge case where a JSONPath refers to a field with an empty string key, e.g., `$['']`.

### 7. No Integration Test for Schema Validation with Invalid JSONPath in Extra Fields

**Issue:** If `SchemaValidator.validate_response` returns malformed paths in `extra_fields`, the comparator silently skips them. No test verifies this edge case or confirms the behavior is intentional.
