# Bug Hunt: Artifact Writer Module

## Confirmed Bugs

### 1. `_set_value` IndexError with Empty Path (Low Severity)

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 512-536

**Issue:** If the `path` parameter results in an empty `parts` list after processing, `parts[-1]` raises an `IndexError`.

**Reproduction path:**
```python
# This would crash:
writer._set_value(data, "", "[REDACTED]")
```

**Analysis:**
```python
def _set_value(self, data: Any, path: str, value: Any) -> None:
    # Convert array notation to dot notation: "items[0].key" -> "items.0.key"
    parts = path.replace("[", ".").replace("]", "").split(".")
    parts = [p for p in parts if p]  # Empty string becomes empty list

    # ... loop over parts[:-1] ...

    # Set the final value
    final_part = parts[-1]  # IndexError: list index out of range if parts is []
```

When `path = ""`:
1. `"".split(".")` returns `[""]`
2. `[p for p in [""] if p]` returns `[]` (empty string is falsy)
3. `parts[-1]` on empty list raises `IndexError`

**Why it matters:** Although `_set_value` is only called from `_redact_path` with paths from jsonpath_ng matches (which should never be empty), the method is not defensively coded and could crash if used in a different context.

**Fix:** Add guard at start of `_set_value`:
```python
if not parts:
    return  # Nothing to set
```

---

### 2. Bundle Name Collision Can Overwrite Data (Medium Severity)

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 145-151

**Issue:** Timestamp resolution is only to the second, and `mkdir(exist_ok=True)` means if two mismatches for the same operation occur within the same second, the second write silently overwrites the first bundle's files.

**Code:**
```python
timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")  # Second resolution only
operation_id = self._sanitize_filename(case.operation_id)
case_id = case.case_id[:8]  # First 8 chars of UUID
bundle_name = f"{timestamp}__{operation_id}__{case_id}"
bundle_dir = self._mismatches_dir / bundle_name

bundle_dir.mkdir(parents=True, exist_ok=True)  # Silently succeeds if exists
```

**Scenario:**
1. Two mismatches for operation `getUser` occur at timestamps `20260119T123456`
2. Both have case_id starting with same 8 characters (unlikely but possible with some UUID implementations)
3. Both create bundle name `20260119T123456__getUser__abc12345`
4. Second write overwrites first bundle's files

**Why it matters:** Data loss. Mismatch bundles contain important debugging information that could be silently overwritten.

**Fix options:**
1. Add microseconds to timestamp: `strftime("%Y%m%dT%H%M%S%f")`
2. Use full case_id instead of first 8 chars
3. Check if directory exists and add a counter suffix if needed

---

### 3. Inconsistent Timestamps Between Bundle Name and Metadata (Low Severity)

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 145 and 174-177

**Issue:** Two separate `datetime.now()` calls generate different timestamps for the bundle directory name and the metadata file.

**Code:**
```python
# Line 145 - for bundle name
timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

# ... processing ...

# Lines 174-177 - for metadata
metadata = MismatchMetadata(
    tool_version=TOOL_VERSION,
    timestamp=datetime.now(timezone.utc).isoformat(),  # Different call!
    ...
)
```

**Impact:** The bundle directory name shows one timestamp (e.g., `20260119T123456`) while `metadata.json` contains a different timestamp (e.g., `2026-01-19T12:34:56.789012+00:00`). This can be confusing when debugging or correlating events across logs.

**Fix:** Capture timestamp once and reuse:
```python
now = datetime.now(timezone.utc)
timestamp_for_name = now.strftime("%Y%m%dT%H%M%S")
timestamp_for_metadata = now.isoformat()
```

---

## Likely Bugs

### 4. Same Issue in `write_chain_mismatch` (Low Severity)

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 223 and 254-258

**Issue:** Same double-timestamp and potential collision issues exist in `write_chain_mismatch` as in `write_mismatch`.

---

## Suspicious Code

### 5. `_sanitize_filename` Allows ".." Through (Informational)

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 423-441

**Code:**
```python
def _sanitize_filename(self, name: str) -> str:
    # Replace unsafe characters with underscores
    safe = re.sub(r"[^\w\-.]", "_", name)  # Dots are allowed
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("_")
    if len(safe) > 50:
        safe = safe[:50]
    return safe or "unnamed"
```

**Issue:** The regex `[^\w\-.]` allows dots through. This means an `operation_id` of `".."` would pass through unchanged.

**Trace:**
1. `name = ".."`
2. `re.sub(r"[^\w\-.]", "_", "..")` = `".."` (dots pass regex)
3. `safe.strip("_")` = `".."`
4. Returns `".."`

**Why this is probably not exploitable:** The sanitized value is embedded in a larger bundle name: `{timestamp}__..__.{case_id}`. The path traversal dots become part of a single filename, not separate path components. Still, allowing `..` through a filename sanitizer is poor practice.

**Fix:** Add explicit check:
```python
if safe in (".", ".."):
    safe = "unnamed"
```

---

### 6. Silent Failure on jsonpath_ng Import Error (Design Choice)

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 481-492

**Code:**
```python
def _redact_path(self, data: Any, jsonpath: str) -> Any:
    try:
        from jsonpath_ng import parse as jsonpath_parse
        # ... redaction logic ...
    except Exception:
        # Best-effort redaction: invalid paths or missing jsonpath_ng library
        # should not break artifact writing. The data just won't be redacted.
        pass
    return data
```

**Issue:** Catching all `Exception` types means real bugs (e.g., TypeError in redaction logic) are silently swallowed. The user would never know their secrets are not being redacted.

**Note:** This appears to be an intentional design decision for "best-effort" redaction. However, it could mask bugs and create a false sense of security about sensitive data.

---

## Missing Test Coverage

### 7. No Unit Tests for Redaction Logic

**Missing tests for:**
- `_redact()` method with various secrets_config settings
- `_redact_path()` with valid and invalid JSONPath expressions
- `_set_value()` with various path formats (arrays, nested objects, edge cases)
- Redaction when jsonpath_ng is not installed
- Redaction with malformed JSONPath expressions

**Current state:** The `SecretsConfig` model is tested in `test_models.py`, but the actual redaction implementation in `ArtifactWriter` is not tested.

---

### 8. No Unit Tests for `_sanitize_filename`

**Missing tests for:**
- Empty string input
- String with only special characters
- String with dots only (`.`, `..`)
- String exceeding 50 character limit
- Unicode characters
- Path traversal attempts

---

### 9. No Unit Tests for `write_mismatch` (Stateless)

**Current state:** Only `write_chain_mismatch` is tested in `tests/integration/test_stateful_chains.py`. The stateless `write_mismatch` method is exercised indirectly through CLI integration tests but has no focused unit tests.

**Missing coverage:**
- File structure verification for stateless bundles
- Atomic write behavior (temp file handling)
- Error cases (permission denied, disk full simulation)
- Concurrent write behavior

---

### 10. No Tests for Atomic Write Pattern

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 405-421

**Missing tests for:**
- Temp file cleanup on write errors
- Behavior when rename fails
- Behavior when temp file already exists
- Atomic guarantee verification (e.g., interruption during write doesn't corrupt)

---

### 11. No Tests for `write_chains_log`

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 267-345

**Missing tests for:**
- Output format verification
- Link tracking accuracy
- Edge cases (empty chains list, chains with no links)

---

### 12. No Tests for `write_replay_summary`

**Location:** `/home/user/api-parity/api_parity/artifact_writer.py`, lines 373-403

While replay functionality is tested end-to-end in integration tests, there are no unit tests for the summary writing specifically.

---

## Summary

| Category | Count | Severity Range |
|----------|-------|----------------|
| Confirmed Bugs | 3 | Low to Medium |
| Likely Bugs | 1 | Low |
| Suspicious Code | 2 | Informational |
| Missing Test Coverage | 6 areas | - |

**Recommended Priority:**
1. **High:** Fix bundle name collision issue (potential data loss)
2. **Medium:** Add guard for empty path in `_set_value`
3. **Medium:** Add unit tests for redaction logic (security feature needs coverage)
4. **Low:** Fix inconsistent timestamps
5. **Low:** Improve `_sanitize_filename` to reject `.` and `..`
