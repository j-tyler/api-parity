# CEL Evaluator Analysis Findings

Analysis of `api_parity/cel_evaluator.py` against tests and documentation.

## Summary

The implementation is mostly correct and well-aligned with documentation. Found **1 confirmed bug** and **1 documentation inaccuracy**.

---

## CONFIRMED BUG: Missing `wait()` After `kill()` in Cleanup

**Location:** `api_parity/cel_evaluator.py`, lines 143-149

**Code:**
```python
try:
    self._process.terminate()
    self._process.wait(timeout=1)
except Exception:
    try:
        self._process.kill()
    except Exception:
        pass
self._process = None
```

**Issue:** When `wait(timeout=1)` raises `TimeoutExpired` after `terminate()`, the code calls `kill()` but does not call `wait()` to reap the zombie process.

**Documentation from CLAUDE.md says:**
```python
# RIGHT - timeout with escalation to SIGKILL
proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()
    try:
        proc.wait(timeout=5)  # <-- MISSING IN IMPLEMENTATION
    except subprocess.TimeoutExpired:
        pass  # Process is unkillable, nothing more we can do
```

**Impact:** Zombie processes can accumulate if:
1. Process doesn't respond to SIGTERM within 1 second
2. SIGKILL is sent but process is not reaped
3. Only cleaned up when parent Python process exits

**Severity:** Low (zombies are cleaned at CLI exit, rare edge case)

**Fix:** Add `wait(timeout=N)` after `kill()`:
```python
try:
    self._process.terminate()
    self._process.wait(timeout=1)
except subprocess.TimeoutExpired:
    self._process.kill()
    try:
        self._process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass  # Process is unkillable
except Exception:
    pass
```

---

## DOCUMENTATION INACCURACY: Timeout Error Message

**Location:** CLAUDE.md "CEL Evaluator Gotchas"

**Documentation says:**
> If evaluation exceeds this, it returns `{"ok":false,"error":"evaluation timeout exceeded"}`

**Actual Go implementation** (`cmd/cel-evaluator/main.go`, line 112):
```go
return Response{ID: req.ID, OK: false, Error: fmt.Sprintf("CEL evaluation timeout (%v)", evaluationTimeout)}
```

**Actual error message:** `"CEL evaluation timeout (5s)"`

**Impact:** Minor - error messages are informational. No functional bug.

---

## VERIFIED CORRECT: Key Implementation Details

### 1. Timeout Handling (CORRECT)

| Constant | Documented | Implementation |
|----------|------------|----------------|
| `MAX_RESTARTS` | 3 | 3 (line 51) |
| `STARTUP_TIMEOUT` | 5.0 | 5.0 (line 54) |
| `EVALUATION_TIMEOUT` | 10.0 | 10.0 (line 58) |

All timeouts match documentation.

### 2. Blocking I/O Protection (CORRECT)

**Startup timeout** (lines 93-98):
```python
ready, _, _ = select.select([self._process.stdout], [], [], self.STARTUP_TIMEOUT)
if not ready:
    self._cleanup_process()
    raise CELSubprocessError(...)
```

**Evaluation timeout** (lines 180-186):
```python
ready, _, _ = select.select(
    [self._process.stdout], [], [], self.EVALUATION_TIMEOUT
)
if not ready:
    raise CELEvaluationError(...)
```

Uses `select.select()` before all `readline()` calls, matching CLAUDE.md requirement.

### 3. Flush After Write (CORRECT)

Lines 176-177:
```python
self._process.stdin.write(json.dumps(request) + "\n")
self._process.stdin.flush()
```

Matches CLAUDE.md: "Flush after every write"

### 4. EOF Detection and Restart (CORRECT)

Lines 188-192:
```python
response_line = self._process.stdout.readline()
if not response_line:
    self._restart_subprocess()
    return self.evaluate(expression, data)
```

Correctly detects EOF (empty string from readline) and triggers restart.

### 5. MAX_RESTARTS Enforcement (CORRECT)

Lines 116-125:
```python
def _restart_subprocess(self) -> None:
    if self._restart_count >= self.MAX_RESTARTS:
        raise CELSubprocessError(
            f"CEL subprocess crashed {self.MAX_RESTARTS} times, giving up"
        )
    self._restart_count += 1
    ...
```

Test `test_max_restarts_exceeded` (lines 258-279) verifies:
- 3 restarts succeed (restart_count goes 0->1->2->3)
- 4th restart attempt fails (3 >= 3)

### 6. CEL Error vs Subprocess Error (CORRECT)

Lines 208-209:
```python
if not response.get("ok"):
    raise CELEvaluationError(response.get("error", "Unknown CEL evaluation error"))
```

CEL expression errors raise `CELEvaluationError` (not `CELSubprocessError`), matching documented behavior.

### 7. ID Correlation (CORRECT)

Lines 202-206:
```python
if response.get("id") != request_id:
    raise CELEvaluationError(
        f"Response ID mismatch: expected {request_id}, got {response.get('id')}"
    )
```

Validates response ID matches request ID per CLAUDE.md: "Correlate by ID for debugging"

### 8. NDJSON Protocol (CORRECT)

Python sends:
```python
json.dumps(request) + "\n"
```

Go uses:
```go
reader := bufio.NewScanner(os.Stdin)
// ...
w.WriteByte('\n')
w.Flush()
```

Both sides use newline-delimited JSON with proper flushing.

---

## Test Coverage Assessment

### Well-Tested Areas

1. **Basic evaluation** - `TestCELEvaluatorBasic` covers equality, strings, true/false
2. **Numeric comparisons** - `TestCELEvaluatorNumeric` covers tolerance, range checks
3. **Array operations** - `TestCELEvaluatorArrays` covers size, unordered comparison
4. **Error handling** - `TestCELEvaluatorErrors` covers undefined vars, syntax errors, type mismatches
5. **Lifecycle** - `TestCELEvaluatorLifecycle` covers context manager, crash recovery, max restarts

### Edge Cases Tested

- `test_subprocess_crash_recovery` (line 237): Kills process with SIGKILL, verifies restart
- `test_max_restarts_exceeded` (line 258): Exhausts restart limit, verifies exception

### Not Explicitly Tested

1. **Python timeout (10s)** - No test where subprocess hangs for >10s
2. **Go timeout (5s)** - No test with pathological expression that triggers internal timeout
3. **BrokenPipeError path** (line 196-198) - Covered by crash recovery implicitly

---

## Files Analyzed

- `/home/user/api-parity/api_parity/cel_evaluator.py` (221 lines)
- `/home/user/api-parity/tests/test_cel_evaluator.py` (393 lines)
- `/home/user/api-parity/tests/integration/test_comparator_cel.py` (942 lines)
- `/home/user/api-parity/cmd/cel-evaluator/main.go` (160 lines)
- `/home/user/api-parity/ARCHITECTURE.md` (CEL Evaluator section)
- `/home/user/api-parity/CLAUDE.md` (CEL Evaluator Gotchas section)
