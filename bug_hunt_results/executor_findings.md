# Bug Hunt: Executor Module

## Confirmed Bugs

### 1. Resource Leak in `close()` Method

**Location:** `/home/user/api-parity/api_parity/executor.py`, lines 118-121

**Code:**
```python
def close(self) -> None:
    """Close HTTP clients."""
    self._client_a.close()
    self._client_b.close()
```

**Issue:** If `_client_a.close()` raises an exception, `_client_b.close()` is never called. This leaks the HTTP client for target B, including any underlying socket connections.

**Evidence:** This is a straightforward control flow issue. If an exception occurs on line 120, line 121 is never executed. While `httpx.Client.close()` is unlikely to raise in normal circumstances, it can happen if:
- The underlying transport has issues
- SSL/TLS cleanup fails
- The client was already corrupted by an earlier error

**Correct pattern:**
```python
def close(self) -> None:
    """Close HTTP clients."""
    try:
        self._client_a.close()
    finally:
        self._client_b.close()
```

**Severity:** Medium - Resource leak that accumulates over repeated executor creation/destruction cycles.

---

### 2. Resource Leak in `__init__()` on Partial Initialization Failure

**Location:** `/home/user/api-parity/api_parity/executor.py`, lines 103-105

**Code:**
```python
# Create HTTP clients for each target
self._client_a = httpx.Client(**self._build_client_kwargs(target_a, default_timeout))
self._client_b = httpx.Client(**self._build_client_kwargs(target_b, default_timeout))
```

**Issue:** If `_client_b` creation fails (line 105) after `_client_a` was successfully created (line 104), `_client_a` is never closed. The exception propagates up without cleanup.

**Evidence:** This can happen when:
- target_b has invalid TLS configuration (e.g., bad cipher string for target_b but valid for target_a)
- Network/DNS issues specific to target_b's base_url
- Memory pressure causes the second allocation to fail

**Correct pattern:**
```python
self._client_a = httpx.Client(**self._build_client_kwargs(target_a, default_timeout))
try:
    self._client_b = httpx.Client(**self._build_client_kwargs(target_b, default_timeout))
except Exception:
    self._client_a.close()
    raise
```

**Severity:** Medium - Resource leak on initialization failure, though this is a less common path.

---

## Likely Bugs

### 1. Rate Limit Lock Held During Sleep (Thread Safety Issue)

**Location:** `/home/user/api-parity/api_parity/executor.py`, lines 457-468

**Code:**
```python
def _wait_for_rate_limit(self) -> None:
    """Wait if necessary to respect rate limit."""
    if self._min_interval <= 0:
        return

    with self._rate_limit_lock:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            sleep_time = self._min_interval - elapsed
            time.sleep(sleep_time)  # <-- Sleep inside lock!
        self._last_request_time = time.monotonic()
```

**Issue:** The lock is held during `time.sleep()`. If someone attempts to use the Executor concurrently (e.g., from multiple threads), all threads would serialize on the lock, waiting for the sleeping thread to wake up. This effectively serializes ALL requests across threads, not just rate-limited ones.

**Evidence:** The presence of `_rate_limit_lock` suggests thread-safety was considered. However, the current implementation defeats the purpose of having a lock because the sleep is inside the critical section.

**Why "Likely" not "Confirmed":** The current codebase uses the Executor serially (execute() calls targets A then B sequentially). The bug only manifests if someone tries concurrent use, which is not the current usage pattern. However, the lock's presence implies thread-safety was intended.

**Correct pattern:**
```python
def _wait_for_rate_limit(self) -> None:
    if self._min_interval <= 0:
        return

    with self._rate_limit_lock:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            sleep_time = self._min_interval - elapsed
        else:
            sleep_time = 0
        # Update time BEFORE releasing lock, assuming sleep will happen
        self._last_request_time = now + sleep_time

    # Sleep OUTSIDE the lock
    if sleep_time > 0:
        time.sleep(sleep_time)
```

**Severity:** Low (for current usage) / High (if concurrent usage is attempted)

---

## Suspicious Code

### 1. Direct Variable Name Match in `_apply_variables()`

**Location:** `/home/user/api-parity/api_parity/executor.py`, lines 312-314

**Code:**
```python
# Also check for direct match (Schemathesis may have already resolved)
if value == var_name:
    value = self._variable_to_string(var_value)
```

**Concern:** This replaces any path parameter value that exactly matches a variable name. For example, if you have a variable named `id` with value `123`, and a path parameter happens to have the literal value `id` (not `{id}`), it would be replaced with `123`.

The comment says "Schemathesis may have already resolved" but this could lead to unintended replacements if:
- A legitimate API path parameter is named after a common word that also happens to be a variable name
- An OpenAPI spec uses literal strings that coincidentally match variable names

**Why "Suspicious" not "Bug":** The code has an explicit comment explaining the intent, suggesting this was a deliberate design choice to handle a known edge case. Without deeper knowledge of how Schemathesis resolves variables, I cannot confirm this is incorrect.

---

### 2. Only First Header Value Used for Requests

**Location:** `/home/user/api-parity/api_parity/executor.py`, lines 501-504

**Code:**
```python
headers: dict[str, str] = {}
for key, values in request.headers.items():
    if values:
        headers[key] = _sanitize_header_value(values[0])
```

**Concern:** When a request has multiple values for the same header (e.g., `Accept: application/json, text/plain`), only the first value is sent. HTTP allows multiple header values, typically comma-separated.

**Why "Suspicious" not "Bug":** CLAUDE.md explicitly documents this as "Multi-value headers use first value only" - so this is a known limitation. However, it could cause test failures if the API under test relies on multi-value headers.

---

## Missing Test Coverage

### 1. `close()` Error Handling Not Tested

No tests verify that `close()` properly handles exceptions from the first client close. The resource leak bug (#1) would not be caught by existing tests.

**Recommended test:**
```python
def test_close_cleans_up_both_clients_even_if_first_fails():
    """Verify client_b is closed even if client_a.close() raises."""
    # Mock client_a.close() to raise
    # Verify client_b.close() is still called
```

### 2. Initialization Failure Cleanup Not Tested

No tests verify that if `_client_b` creation fails, `_client_a` is properly closed.

**Recommended test:**
```python
def test_init_cleans_up_client_a_if_client_b_creation_fails():
    """Verify client_a is closed if client_b creation raises."""
    # Make target_b have invalid config that causes httpx.Client to fail
    # Verify client_a.close() is called before exception propagates
```

### 3. Array Response Body Variable Extraction Not Tested

**Location:** `/home/user/api-parity/api_parity/executor.py`, lines 407-427

The `_extract_variables()` method only extracts from dict bodies:
```python
if isinstance(response.body, dict):
    # ... extraction logic
```

If an API returns a list response (e.g., `[{"id": "123"}, ...]`), no variables are extracted even though JSONPointer supports array indexing (e.g., `0/id`).

**Recommended test:**
```python
def test_extract_variables_from_array_body():
    """Verify extraction works when response body is a list."""
    response = ResponseCase(
        status_code=200,
        body=[{"id": "123"}, {"id": "456"}],
        elapsed_ms=100,
    )
    # Test that 0/id extracts "123"
```

### 4. Concurrent Rate Limiting Behavior Not Tested

The rate limiting implementation has a lock, suggesting thread-safe intent, but no tests verify behavior under concurrent access. The "lock held during sleep" issue (#1 in Likely Bugs) would not be detected.

### 5. `execute_chain` with Partial Failure Not Tested

If a request in the middle of a chain raises `RequestError`, the partial `steps_a` and `steps_b` lists are lost. While this may be intentional (per docstring), there are no tests verifying this behavior or that the exception propagates correctly with useful context.

### 6. Integration Test Missing Return Code Check

**Location:** `/home/user/api-parity/tests/integration/test_explore_execution.py`, lines 130-147

```python
def run_explore(out_name: str) -> dict:
    out_dir = tmp_path / out_name
    subprocess.run(
        [...],
        capture_output=True,
        cwd=PROJECT_ROOT,
        timeout=60,
    )  # <-- No check for return code!
    with open(out_dir / "summary.json") as f:
        return json.load(f)
```

If the subprocess fails, the test attempts to read `summary.json` which may not exist, leading to a confusing `FileNotFoundError` instead of seeing the actual subprocess failure.

**Recommended fix:**
```python
result = subprocess.run(...)
assert result.returncode == 0, f"Explore failed: {result.stderr}"
```
