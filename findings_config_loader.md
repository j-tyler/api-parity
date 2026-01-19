# Findings: config_loader.py Analysis

Analysis of `api_parity/config_loader.py` against tests and documentation.

## Summary

| Issue | Severity | Type |
|-------|----------|------|
| Empty env var `${}` silently ignored | Medium | Bug |
| Empty headers dict doesn't override | Medium | Behavior inconsistency |
| No unit tests for core functions | Low | Test coverage gap |
| Parameter validation conflates missing vs unknown | Low | Misleading error message |
| Empty env var value not validated | Low | Edge case |

---

## Issue 1: Empty Environment Variable Name `${}` Silently Ignored

**Location:** `config_loader.py` lines 246-255

**Code:**
```python
def _substitute_string(s: str) -> str:
    pattern = re.compile(r"\$\{([^}]+)\}")  # Requires at least one char

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            raise ConfigError(f"Environment variable '{var_name}' is not set")
        return value

    return pattern.sub(replacer, s)
```

**Problem:** The regex pattern `\$\{([^}]+)\}` uses `+` which requires at least one character between `${` and `}`. If a user writes `${}` (typo or mistake), it won't match the pattern and the literal string `${}` will remain in the config value.

**Documentation says (docs/configuration.md line 45):**
> "Variables are resolved at config load time. Missing variables cause an error."

But `${}` is not detected as an error - it's silently passed through.

**Test gap:** No test verifies error handling for malformed env var patterns like `${}`.

**Example:**
```yaml
targets:
  production:
    base_url: "http://api.example.com:${}"  # Typo - empty var name
```

Would result in `base_url = "http://api.example.com:${}"` instead of an error.

---

## Issue 2: Empty Headers Dict Doesn't Override Defaults

**Location:** `config_loader.py` lines 161-165

**Code:**
```python
return OperationRules(
    status_code=override.status_code if override.status_code is not None else default.status_code,
    headers=override.headers if override.headers else default.headers,  # BUG HERE
    body=override.body if override.body is not None else default.body,
)
```

**Problem:** Inconsistent override behavior:
- `status_code` and `body` use `is not None` check (explicit None vs unset)
- `headers` uses truthiness check (`if override.headers`)

An empty dict `{}` is falsy in Python, so if a user explicitly specifies `headers: {}` to override with NO header rules (clearing all inherited defaults), the default headers are used instead.

**DESIGN.md says (line 271):**
> "Override semantics, not merge (simpler mental model)"

The implementation contradicts this for the headers case.

**Example:**
```json
{
  "default_rules": {
    "headers": {
      "content-type": {"predefined": "exact_match"}
    }
  },
  "operation_rules": {
    "legacyEndpoint": {
      "headers": {}  // Intent: don't compare any headers for this endpoint
    }
  }
}
```

Expected: legacyEndpoint compares no headers
Actual: legacyEndpoint inherits content-type rule from defaults

**Test gap:** No test for `get_operation_rules` with explicit empty headers override.

---

## Issue 3: No Unit Tests for Core Loading Functions

**Missing unit tests for:**
- `load_runtime_config()` - only tested at integration level
- `_substitute_env_vars()` - no direct tests
- `_substitute_string()` - no direct tests
- `get_operation_rules()` - no tests at all
- `resolve_comparison_rules_path()` - no direct tests

**Evidence:** Searched for test functions:
```bash
grep -r "load_runtime_config\|_substitute_env\|_substitute_string" tests/
# No matches

grep -r "get_operation_rules\|resolve_comparison" tests/
# No matches
```

The integration tests in `tests/integration/test_explore_config.py` cover some paths but don't exercise edge cases like:
- Malformed YAML syntax
- Missing required fields
- Nested env var substitution failure
- Path resolution with symlinks

---

## Issue 4: Parameter Validation Error Conflates Missing vs Unknown Parameter

**Location:** `config_loader.py` lines 426-434

**Code:**
```python
predefined_def = library.predefined[predefined_name]
for param in predefined_def.params:
    param_value = getattr(rule, param, None)
    if param_value is None:
        result.add_error(
            "predefined",
            f"{context}: Predefined '{predefined_name}' requires parameter "
            f"'{param}' but it was not provided."
        )
```

**Problem:** Uses `getattr(rule, param, None)` which returns `None` both when:
1. User didn't provide the parameter (common case)
2. The parameter name doesn't exist as a field on `FieldRule` (library/model mismatch)

If someone extends `comparison_library.json` with a new predefined that requires a parameter not defined on `FieldRule`, the error message incorrectly says "was not provided" when actually the parameter *cannot* be provided.

**Example:** If library defines `{"predefined": "new_rule", "params": ["new_param"]}` but FieldRule doesn't have a `new_param` field, users get:
> "Predefined 'new_rule' requires parameter 'new_param' but it was not provided."

When the real issue is model/library version mismatch.

**Impact:** Low - only affects library extensibility, which is rare.

---

## Issue 5: Environment Variable Set to Empty String Not Validated

**Location:** `config_loader.py` lines 250-252

**Code:**
```python
value = os.environ.get(var_name)
if value is None:
    raise ConfigError(f"Environment variable '{var_name}' is not set")
return value  # Returns "" if VAR="" is set
```

**Behavior:** If `API_TOKEN=""` (empty string), the substitution succeeds and empty string is inserted.

**Not strictly a bug** - empty string may be intentional in some cases. However:
- `Authorization: "Bearer ${API_TOKEN}"` with empty token becomes `"Bearer "` which silently fails auth
- `base_url: ${BASE_URL}` with empty value causes httpx errors later

**Recommendation:** Consider warning when env var is empty, or document this edge case.

---

## Verified: These Are NOT Issues

### TLS Configuration Tests Exist
Tests for TLS fields (cert, key, ca_bundle, verify_ssl, ciphers) exist in `tests/test_models.py` (lines 515-618).

### Validation Functions Are Tested
`validate_comparison_rules()` and `validate_cli_operation_ids()` have comprehensive tests in `tests/test_config_validation.py`.

---

## Recommendations

1. **Fix Issue 1:** Change regex to `r"\$\{([^}]*)\}"` and check for empty var_name, or add explicit check for `${}` pattern before substitution.

2. **Fix Issue 2:** Change headers check from `if override.headers` to `if override.headers is not None`.

3. **Add Unit Tests:** Create `tests/test_config_loader.py` with direct tests for:
   - `load_runtime_config` with various YAML edge cases
   - `_substitute_string` with malformed patterns
   - `get_operation_rules` with override scenarios
   - `resolve_comparison_rules_path` with relative/absolute paths

4. **Improve Error Message (Issue 4):** Check if param exists as attribute on FieldRule before getattr, provide different error for unknown params.

5. **Document or Warn (Issue 5):** Add note about empty env var behavior in docs/configuration.md.
