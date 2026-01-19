# Bug Hunt: CEL Evaluator Module

Analysis of `/home/user/api-parity/api_parity/cel_evaluator.py` and `/home/user/api-parity/cmd/cel-evaluator/main.go`.

## Confirmed Bugs

### 1. Zombie Process Leak in `_cleanup_process`

**Location:** `cel_evaluator.py` lines 143-149

**Problem:** After calling `kill()`, the code does not call `wait()` to reap the zombie process.

```python
# Current code (buggy):
try:
    self._process.terminate()
    self._process.wait(timeout=1)
except Exception:
    try:
        self._process.kill()
    except Exception:
        pass
self._process = None  # Process may still be a zombie!
```

**Expected pattern (from CLAUDE.md):**
```python
proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()
    try:
        proc.wait(timeout=5)  # <-- MISSING!
    except subprocess.TimeoutExpired:
        pass
```

**Impact:** Each cleanup that requires `kill()` leaves a zombie process entry in the process table. On long-running systems with many CEL evaluator restarts, this leaks process table entries.

**Evidence:** Compare with `conftest.py` lines 180-190 (`MockServer.stop()`) which correctly waits after kill.

---

### 2. Thread Safety Violation

**Location:** `cel_evaluator.py` - entire `evaluate()` method

**Problem:** `CELEvaluator` has no thread synchronization. If multiple threads share a single instance and call `evaluate()` concurrently:

1. Thread A writes request 1 to stdin
2. Thread B writes request 2 to stdin
3. Thread A reads response (could be for request 1 or 2)
4. Thread B reads response (could be for request 1 or 2)

The ID mismatch check at line 203 will catch this and raise `CELEvaluationError`, but the error message ("Response ID mismatch") doesn't indicate the root cause is concurrent access.

**Impact:** Multi-threaded use fails unpredictably with confusing error messages. The class lacks documentation warning against concurrent use.

**Evidence:** No lock/mutex anywhere in the class. The `compare()` method in `comparator.py` calls `self._cel.evaluate()` multiple times sequentially, but if a user shares a Comparator across threads, this bug surfaces.

---

### 3. JSON Decode Error Does Not Trigger Restart

**Location:** `cel_evaluator.py` lines 194-200

**Problem:** If the Go subprocess crashes mid-write (after writing partial JSON), Python receives incomplete data:

```python
response_line = self._process.stdout.readline()  # Gets partial JSON like '{"id":"abc'
if not response_line:  # Not empty, so we don't restart
    self._restart_subprocess()
    return self.evaluate(expression, data)

response = json.loads(response_line)  # Raises JSONDecodeError
```

The `JSONDecodeError` handler raises `CELEvaluationError` instead of triggering a restart:

```python
except json.JSONDecodeError as e:
    raise CELEvaluationError(f"Invalid response from subprocess: {response_line}") from e
```

**Impact:** A subprocess crash that happens during response serialization is reported as "Invalid response" rather than triggering automatic recovery. The dead process remains in `self._process`, and the next `evaluate()` call will fail with `BrokenPipeError` (which does trigger restart), but the current request is lost.

**Expected:** JSON decode errors after select.select() indicates data-available should check if process is still alive and potentially restart.

---

## Likely Bugs

### 4. Windows Incompatibility

**Location:** `cel_evaluator.py` lines 93, 104, 180

**Problem:** `select.select()` on Windows only works with sockets, not pipes. The code uses `select.select()` on `subprocess.PIPE` file handles:

```python
ready, _, _ = select.select([self._process.stdout], [], [], self.STARTUP_TIMEOUT)
```

**Impact:** The CEL evaluator will fail to work on Windows. Python raises `OSError: [WinError 10038] An operation was attempted on something that is not a socket`.

**Evidence:** Python documentation for `select`: "Note that on Windows, it only works for sockets."

**Mitigation needed:** Use `selectors` module with platform-specific selector, or use threading-based timeout approach.

---

### 5. Goroutine Memory Accumulation on Timeout

**Location:** `main.go` lines 100-116

**Problem:** When CEL evaluation times out, the spawned goroutine continues running:

```go
go func() {
    resultCh <- evaluateSync(req)  // Still running after timeout
}()

select {
case <-ctx.Done():
    return Response{...}  // Return immediately, goroutine orphaned
case resp := <-resultCh:
    return resp
}
```

The goroutine writes to a buffered channel (won't block), but `evaluateSync()` allocates memory for CEL environment, AST, and program that won't be freed until the goroutine completes.

**Impact:** Under heavy load with many timeout-inducing expressions, Go process memory grows until the goroutines complete. Not a true leak (they eventually finish), but could cause OOM under adversarial input.

**Evidence:** The channel is buffered (`make(chan Response, 1)`), so goroutine won't deadlock, but resources remain allocated.

---

## Suspicious Code

### 6. Restart Count Never Resets

**Location:** `cel_evaluator.py` line 69

**Problem:** `_restart_count` is initialized to 0 but never reset to 0 after successful evaluations. If the subprocess crashes 3 times over weeks of operation (with many successful evaluations between crashes), the 4th crash will fail permanently.

```python
def __init__(self, ...):
    self._restart_count = 0  # Set once
    # Never reset even after successful evaluations
```

**Uncertainty:** This might be intentional design (detect flaky binaries). But the `MAX_RESTARTS` name suggests "consecutive" restarts, not "lifetime" restarts. The test `test_max_restarts_exceeded` kills the process 4 times in a row, suggesting "consecutive" semantics are intended.

---

### 7. Exception Handler Order in evaluate()

**Location:** `cel_evaluator.py` lines 196-200

**Problem:** The exception handler catches `BrokenPipeError` but not other I/O errors that could indicate subprocess death:

```python
except BrokenPipeError:
    self._restart_subprocess()
    return self.evaluate(expression, data)
except json.JSONDecodeError as e:
    raise CELEvaluationError(...)
```

What about:
- `OSError` from `stdin.write()` or `stdin.flush()`
- `ValueError` from `readline()` on a closed stream

These would propagate up as unexpected exceptions rather than triggering restart.

---

## Missing Test Coverage

### Critical Missing Tests

1. **No test for JSON decode error on response**
   - Scenario: Go crashes mid-write, Python receives partial JSON
   - Current behavior: Raises `CELEvaluationError` instead of restart
   - Test would expose Bug #3

2. **No test for response ID mismatch**
   - The code validates `response.get("id") != request_id` but no test verifies this check
   - Would expose thread-safety issues (Bug #2) if tested under concurrency

3. **No test for CEL evaluation timeout**
   - Go has internal 5s timeout, Python has 10s timeout
   - No test sends a pathological expression that triggers Go's timeout
   - Expected response: `{"ok":false,"error":"CEL evaluation timeout (5s)"}`

4. **No test for subprocess crash during evaluation wait**
   - Tests only crash subprocess between evaluations
   - `test_subprocess_crash_recovery` does SIGKILL then wait, but doesn't test crash while `select.select()` is pending

5. **No test for concurrent evaluations**
   - Would expose Bug #2 (thread safety)
   - Should verify either: (a) proper serialization, or (b) clear "not thread-safe" documentation

### Additional Missing Coverage

6. **No test for very large JSON payloads**
   - Go has 10MB buffer limit
   - Test should verify behavior at and beyond limit

7. **No test for subprocess binary that crashes on startup**
   - `test_invalid_binary_path` tests missing binary, not crashing binary
   - What if binary exists but segfaults immediately?

8. **No test for empty line handling**
   - Go skips empty lines (`if line == "" { continue }`)
   - Python could theoretically send just `\n`

9. **No Windows platform test**
   - Would expose Bug #4 (select.select doesn't work on Windows pipes)

10. **No test for malformed JSON request handling**
    - Go handles this with empty ID response
    - Python's ID check would catch it, but not explicitly tested

---

## Summary

| # | Issue | Severity | Confidence |
|---|-------|----------|------------|
| 1 | Zombie process leak in cleanup | Medium | Confirmed |
| 2 | Thread safety violation | High | Confirmed |
| 3 | JSON decode doesn't trigger restart | Medium | Confirmed |
| 4 | Windows incompatibility | High | Likely |
| 5 | Goroutine memory accumulation | Low | Likely |
| 6 | Restart count never resets | Low | Suspicious |
| 7 | Incomplete exception handling | Low | Suspicious |

The most impactful bugs are #2 (thread safety) and #4 (Windows). Bug #1 (zombie leak) is easy to fix by adding `wait()` after `kill()`.
