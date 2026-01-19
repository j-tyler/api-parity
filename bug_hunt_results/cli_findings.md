# Bug Hunt: CLI Module

## Confirmed Bugs

### 1. Race Condition in ProgressReporter

**Location:** `/home/user/api-parity/api_parity/cli.py`, lines 69-71 and 111-116

**Issue:** The `_last_print_len` instance variable is accessed from both the main thread (in `stop()`) and the background thread (in `_print_progress()`), without any synchronization.

```python
# In background thread (_print_progress, line 113)
self._last_print_len = len(line)

# In main thread (stop, line 69-71)
if self._last_print_len > 0:
    sys.stderr.write("\r" + " " * self._last_print_len + "\r")
```

The lock (`self._lock`) is only used for `_completed` and `_total`, not for `_last_print_len`. While Python's GIL makes simple int reads/writes effectively atomic, the `join(timeout=2.0)` on line 67 can timeout, meaning the background thread may still be running and modifying `_last_print_len` when `stop()` reads it.

**Impact:** Low severity. Worst case is the progress line is not fully cleared (cosmetic issue). However, this violates proper threading hygiene and could cause subtle issues on platforms with different memory models.

---

### 2. Missing Error Handling for Link Field Extraction in Replay

**Location:** `/home/user/api-parity/api_parity/cli.py`, lines 1588-1599

**Issue:** When loading bundles for replay, `extract_link_fields_from_chain()` is called inside the `try` block, but only `BundleLoadError` is caught:

```python
for bundle_path in bundles:
    try:
        bundle = load_bundle(bundle_path)
        loaded_bundles.append(bundle)
        if bundle.bundle_type == BundleType.CHAIN and bundle.chain_case is not None:
            chain_link_fields = extract_link_fields_from_chain(bundle.chain_case)
            # ... more processing
    except BundleLoadError as e:
        load_errors.append((bundle_path, str(e)))
```

If `extract_link_fields_from_chain()` raises any exception other than `BundleLoadError` (e.g., `KeyError`, `AttributeError`, `TypeError` from malformed data), the entire CLI will crash instead of gracefully skipping the bundle.

**Impact:** Medium severity. A single malformed bundle can crash the entire replay run, even if other bundles are valid.

---

## Likely Bugs

### 1. Inconsistent Argument Validation for graph-chains vs explore

**Location:** `/home/user/api-parity/api_parity/cli.py`, lines 325-336 vs 432-444

**Issue:** The `--max-chains` and `--max-steps` arguments use different validation strategies:

For `graph-chains`:
```python
graph_chains_parser.add_argument(
    "--max-chains",
    type=int,  # Plain int - accepts 0 and negative values
    default=20,
    ...
)
graph_chains_parser.add_argument(
    "--max-steps",
    type=int,  # Plain int - accepts 0 and negative values
    ...
)
```

For `explore`:
```python
explore_parser.add_argument(
    "--max-chains",
    type=positive_int,  # Validates at parse time
    ...
)
explore_parser.add_argument(
    "--max-steps",
    type=positive_int,  # Validates at parse time
    ...
)
```

The `graph-chains` command validates these at runtime in `_run_graph_chains_generated()`, but only when `--generated` is used. This creates:
1. Inconsistent user experience (error at parse time vs runtime)
2. Different error messages between commands
3. No validation at all when `--max-chains 0` is passed without `--generated`

**Impact:** Low severity. Usability issue rather than functional bug, but violates principle of least surprise.

---

### 2. ProgressReporter Thread Leak on Exception

**Location:** `/home/user/api-parity/api_parity/cli.py`, lines 56-71

**Issue:** If an exception occurs between `start()` and `stop()`, and the exception is not caught by the outer handlers (anything other than `CELSubprocessError` or `KeyboardInterrupt`), the daemon thread continues running until process exit. While daemon=True means it won't prevent exit, the thread consumes resources and may continue writing to stderr during exception unwinding.

The `finally` block at line 1257-1260 does call `progress_reporter.stop()`, but only if `progress_reporter` is not None and was assigned. If an exception occurs during component initialization between lines 1203 and 1205, the thread may already be running when the exception propagates.

**Impact:** Low severity. The daemon flag prevents process hangs, but there's a window for resource leakage.

---

## Suspicious Code

### 1. Thread Join with Timeout in ProgressReporter

**Location:** `/home/user/api-parity/api_parity/cli.py`, line 67

```python
self._thread.join(timeout=2.0)
```

The `join(timeout=2.0)` may not give the thread enough time to finish if it's in the middle of `_print_progress()`, especially if stderr is blocked (e.g., piped to a slow consumer). After the timeout, `stop()` proceeds to clear the line, potentially interleaving with the thread's output.

**Note:** This is benign in practice because the thread checks `_stop_event` before each print cycle, and stderr writes are typically fast.

---

### 2. Hardcoded Default for max_chains in Stateful Mode

**Location:** `/home/user/api-parity/api_parity/cli.py`, line 1149

```python
max_chains = args.max_chains or 20
```

This uses `or 20` which would treat `0` as falsy and default to 20. However, since `positive_int` is used for explore's `--max-chains`, this case cannot occur through the CLI. Still, programmatic callers could pass 0.

---

### 3. Potential Integer Overflow in Large max_cases

**Location:** `/home/user/api-parity/api_parity/cli.py`

The test `test_large_max_cases` in test_cli_common.py passes `999999999` as max_cases. While Python handles arbitrary integers, if this value is passed to external components (like Hypothesis limits), it may cause unexpected behavior or memory issues.

---

## Missing Test Coverage

### 1. No Tests for ProgressReporter

The `ProgressReporter` class (lines 28-130) has zero test coverage. This includes:
- Thread lifecycle (start/stop)
- Progress calculation and formatting
- Race condition scenarios
- ETA calculation edge cases (rate=0, elapsed=0)

### 2. No Direct Tests for _is_same_mismatch and _is_same_chain_mismatch

These functions (lines 1919-1994) are critical for replay classification but have no direct unit tests. Testing only happens indirectly through integration tests if they exist. Edge cases not covered:
- Empty `differences` lists
- Missing keys in `original_diff`
- Mismatched structure between original and new diffs
- `None` values for `mismatch_step`

### 3. Missing Tests for positive_float with Edge Cases

**Location:** test_cli_common.py, TestPositiveFloat class

Not tested:
- Scientific notation (e.g., "1e-5")
- Very large values
- Infinity ("inf")
- NaN ("nan")

### 4. Missing Tests for on_step Callback Error Handling

The `on_step` callback in `_run_stateful_explore` and `_replay_chain_bundle` calls `comparator.compare()`. There are no tests verifying behavior when:
- Comparator raises an unexpected exception type
- The callback is called more times than there are steps
- The callback receives None responses

### 5. Missing Tests for run_explore and run_replay with Interrupted Execution

The `KeyboardInterrupt` handling (lines 1254-1256 and 1688-1690) sets `stats.interrupted = True` but there are no tests verifying:
- Summary is written with partial results
- Resources are properly cleaned up
- Exit code is correct

### 6. Missing Tests for Graph-Chains without --generated but with --max-chains

No test verifies behavior when user passes:
```bash
api-parity graph-chains --spec x.yaml --max-chains 100
```
(without --generated flag - the --max-chains is silently ignored)

### 7. No Tests for _format_duration Edge Cases

**Location:** ProgressReporter._format_duration (lines 118-130)

Not tested:
- Exactly 60 seconds (boundary)
- Exactly 3600 seconds (boundary)
- Very large durations (days)
- Negative durations (defensive)

---

## Notes on Code Quality

1. **Type annotations are inconsistent:** `original_diff: dict` vs `new_result: "ComparisonResult"` - the asymmetric handling in `_is_same_mismatch` is correct but not immediately obvious. A comment explaining this design would help.

2. **Exception handling philosophy varies:** Some functions catch specific exceptions and convert to results (comparator), while others let exceptions propagate (CLI handlers). This is documented in CLAUDE.md but could trip up future maintainers.

3. **The callback pattern in stateful execution is complex:** The `on_step` callback captures mutable state via closure (`step_diffs`, `mismatch_found`). While correct, this pattern is error-prone and not obvious without careful reading.
