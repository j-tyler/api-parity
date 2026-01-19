# Executor Analysis Findings

Analysis of `api_parity/executor.py` against its tests and documentation.

---

## Bug Found: Case-Sensitive Text Content-Type Check

**Severity:** Medium
**Location:** `api_parity/executor.py`, lines 597-603, in `_convert_response()`

### Issue Description

The content-type detection logic uses inconsistent case handling:

```python
# Line 597 - JSON check is CASE INSENSITIVE (correct)
if "json" in content_type.lower():
    try:
        body = response.json()
    except Exception:
        body_base64 = base64.b64encode(response.content).decode("ascii")

# Line 603 - Text check is CASE SENSITIVE (incorrect)
elif content_type.startswith("text/"):
    try:
        body = response.text
    except Exception:
        body_base64 = base64.b64encode(response.content).decode("ascii")
```

### Impact

A response with a non-lowercase text content-type will be incorrectly treated as binary content:

| Content-Type | Expected Behavior | Actual Behavior |
|--------------|-------------------|-----------------|
| `text/plain` | Store as text string | Store as text string |
| `TEXT/PLAIN` | Store as text string | **Stored as base64 (bug)** |
| `Text/Plain` | Store as text string | **Stored as base64 (bug)** |

Per RFC 2616 Section 3.7, media types are case-insensitive. The JSON check correctly handles this by using `.lower()`, but the text check does not.

### Suggested Fix

```python
elif content_type.lower().startswith("text/"):
```

### Test Coverage Gap

No unit tests exist for `_convert_response()` to verify case-insensitive content-type handling. The tests in `test_executor_rate_limit.py` do not cover this method directly.

---

## Documentation/Implementation Alignment (Verified Correct)

The following behaviors were verified to match documentation:

### Rate Limiting

**ARCHITECTURE.md states:**
> Rate limit applies globally across all requests to both targets.

**Implementation (line 532 in `_execute_single()`):**
```python
# Enforce rate limit before making request
self._wait_for_rate_limit()
```

**Behavior:** Each call to `_execute_single()` enforces rate limiting. For `execute()`, this means rate limiting before Target A AND before Target B. For `execute_chain()`, rate limiting happens before every step to every target.

This is consistent with documentation. The rate limit is global, not per-target.

### Header Sanitization

**CLAUDE.md states:**
> sanitizes by replacing non-ASCII with `?`

**Implementation (lines 38-52):**
```python
def _sanitize_header_value(value: str) -> str:
    return value.encode('ascii', errors='replace').decode('ascii')
```

**Tests verify this at `test_executor_rate_limit.py:595-609`:**
```python
def test_sanitize_header_value_non_ascii_replaced(self) -> None:
    assert _sanitize_header_value("\xaf") == "?"
    assert _sanitize_header_value("test\xafvalue") == "test?value"
```

This matches documentation and is correctly tested.

### Timeout Configuration

**ARCHITECTURE.md states:**
> `default_timeout`: applies to all operations (default 30s)
> `operation_timeouts`: per-operationId overrides

**Implementation (lines 453-455):**
```python
def _get_timeout(self, operation_id: str) -> float:
    return self._operation_timeouts.get(operation_id, self._default_timeout)
```

This correctly returns operation-specific timeout or falls back to default.

### Context Manager Protocol

**ARCHITECTURE.md states:**
> Supports context manager protocol

**Implementation (lines 107-116):**
```python
def __enter__(self) -> "Executor":
    return self

def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    self.close()
```

This is correctly implemented. Note: `close()` doesn't track if already closed, but `httpx.Client.close()` is idempotent, so double-close is safe.

### Chain Variable Extraction

**ARCHITECTURE.md states:**
> Body fields use their pointer path (e.g., `id`)
> Headers without index: `header/{name}` stores all values as a list
> Headers with index: `header/{name}/{index}` stores the specific value

**Implementation (lines 438-449):**
```python
for header_name in headers_to_extract:
    header_values = response.headers.get(header_name, [])
    if header_values:
        # Store all values as list at header/{name}
        extracted[f"header/{header_name}"] = header_values

        # Store specific indexed values at header/{name}/{index}
        if header_name in indexed_headers:
            for index in indexed_headers[header_name]:
                if index < len(header_values):
                    extracted[f"header/{header_name}/{index}"] = header_values[index]
```

This matches the documented key formats. Tests in `test_stateful_chains.py:767-811` verify this behavior.

---

## Test Coverage Analysis

### Well-Covered Areas

| Feature | Test Location | Coverage |
|---------|---------------|----------|
| Rate limiting logic | `test_executor_rate_limit.py:29-165` | Comprehensive |
| TLS/mTLS configuration | `test_executor_rate_limit.py:167-576` | Comprehensive |
| Header sanitization | `test_executor_rate_limit.py:578-746` | Comprehensive |
| Variable extraction | `test_stateful_chains.py:725-811` | Good |
| Header extraction | `test_stateful_chains.py:767-811, 1412-1446` | Good |

### Coverage Gaps

1. **`_convert_response()` method** - No unit tests for:
   - Case-insensitive content-type handling (this is where the bug exists)
   - Binary vs text vs JSON detection edge cases
   - HTTP version extraction
   - Empty response handling

2. **`execute()` method** - Only tested via integration tests, no unit tests for:
   - Exception handling (RequestError wrapping)
   - Serial execution order verification

3. **`_apply_variables()` method** - Limited edge case testing:
   - Nested body substitution
   - List substitution in body arrays

---

## Minor Observations (Not Bugs)

### Rate Limiting First Request Behavior

**Observation:** The first request never waits because `_last_request_time` is initialized to `0.0` and `time.monotonic()` returns system uptime (typically large).

```python
# Line 100
self._last_request_time: float = 0.0

# In _wait_for_rate_limit()
elapsed = now - self._last_request_time  # Large value on first call
```

**Tests verify this at `test_executor_rate_limit.py:141-164`:**
```python
def test_first_request_never_waits(self, mock_targets):
    """The first request should never wait (large elapsed time from init)."""
```

This is intentional behavior per ARCHITECTURE.md: "First request never waits."

### Variable Substitution Only in Path/Query/Body

**Observation:** `_apply_variables()` substitutes placeholders in path_parameters, query, and body, but NOT in headers.

This is documented in the method docstring:
> Substitutes {variable_name} placeholders in path parameters, query parameters, and body.

If header substitution is needed for some chain scenarios, this would be a feature gap, not a bug.

---

## Summary

| Finding | Severity | Action Required |
|---------|----------|-----------------|
| Case-sensitive text content-type | Medium | Fix needed |
| Rate limiting behavior | N/A | Correct, matches docs |
| Header sanitization | N/A | Correct, matches docs |
| Timeout handling | N/A | Correct, matches docs |
| Context manager | N/A | Correct, matches docs |
| Chain variable extraction | N/A | Correct, matches docs |
| Test coverage for `_convert_response()` | Low | Tests needed |
