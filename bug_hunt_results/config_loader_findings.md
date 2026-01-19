# Bug Hunt: Config Loader Module

## Confirmed Bugs

### 1. `get_operation_rules` Headers Override Logic Bug (Line 163)

**Location:** `/home/user/api-parity/api_parity/config_loader.py`, line 163

**Code:**
```python
return OperationRules(
    status_code=override.status_code if override.status_code is not None else default.status_code,
    headers=override.headers if override.headers else default.headers,  # BUG
    body=override.body if override.body is not None else default.body,
)
```

**The Bug:** The `headers` field uses a truthiness check (`if override.headers`) while `status_code` and `body` use identity checks (`is not None`). Since `headers` is defined with `default_factory=dict`, an explicitly empty dict `{}` is falsy in Python.

**Consequence:** When a user explicitly sets `headers: {}` in operation rules to override and remove all header comparison rules, the code incorrectly falls back to the default headers instead of using the empty override.

**Reproduction:**
```python
from api_parity.models import OperationRules, FieldRule

default = OperationRules(headers={"content-type": FieldRule(predefined="ignore")})
override = OperationRules(headers={})  # User wants NO header rules

# Current buggy behavior
result_headers = override.headers if override.headers else default.headers
# result_headers == {"content-type": ...}  # Falls back to defaults!

# Correct behavior would be:
result_headers = override.headers  # Would be {} as user intended
```

**Impact:** Users cannot clear/override default header rules with an empty set. The override is silently ignored.

**Why This Is Hard to Detect:** The pydantic model uses `default_factory=dict`, so there is no way to distinguish "user didn't specify headers" from "user explicitly set headers to empty dict". Both result in `{}`. However, the inconsistency with how `body` and `status_code` are handled (using `is not None`) is still a bug - headers should be handled the same way for consistency, even if it means the "user wants no headers" case can't work either.

## Likely Bugs

### 2. Environment Variable Name Validation Is Too Permissive

**Location:** `/home/user/api-parity/api_parity/config_loader.py`, line 246

**Code:**
```python
pattern = re.compile(r"\$\{([^}]+)\}")
```

**Issue:** The regex `[^}]+` accepts any characters except `}` as a variable name, including:
- Spaces: `${VAR WITH SPACES}` extracts `"VAR WITH SPACES"`
- Shell-style defaults: `${VAR:-default}` extracts `"VAR:-default"`
- Invalid identifiers: `${123abc}` extracts `"123abc"`

**Consequence:** These invalid/malformed variable names will be looked up in `os.environ.get()`, which will almost certainly fail with the confusing error "Environment variable 'VAR WITH SPACES' is not set" instead of a more helpful "Invalid variable name syntax".

**Impact:** Confusing error messages when users have typos or expect shell-style default syntax.

### 3. No Validation That Environment Variable Values Are Non-Empty

**Location:** `/home/user/api-parity/api_parity/config_loader.py`, lines 250-253

**Code:**
```python
value = os.environ.get(var_name)
if value is None:
    raise ConfigError(f"Environment variable '{var_name}' is not set")
return value
```

**Issue:** An empty string `""` is a valid environment variable value and will be substituted without warning.

**Consequence:** If `API_KEY=""`, then `base_url: "https://api.example.com/${API_KEY}"` becomes `"https://api.example.com/"` which is likely wrong but produces no error.

**Impact:** Silent failures when environment variables are set but empty.

## Suspicious Code

### 4. Single-Pass Environment Variable Substitution

**Location:** `_substitute_env_vars` and `_substitute_string`

**Observation:** If an environment variable's value itself contains `${...}` syntax (e.g., `export OUTER='${INNER}'`), the nested reference is NOT expanded.

```python
os.environ["OUTER"] = "${INNER}"
os.environ["INNER"] = "value"
# _substitute_string("${OUTER}") returns "${INNER}", not "value"
```

**Assessment:** This may be intentional (single-pass is simpler and avoids infinite recursion), but is undocumented and could surprise users expecting shell-like recursive expansion.

### 5. Path Traversal Not Restricted

**Location:** `/home/user/api-parity/api_parity/config_loader.py`, lines 168-184

**Code:**
```python
def resolve_comparison_rules_path(config_path: Path, rules_ref: str) -> Path:
    rules_path = Path(rules_ref)
    if rules_path.is_absolute():
        return rules_path
    return (config_path.parent / rules_path).resolve()
```

**Observation:** No restriction on path traversal. `comparison_rules: ../../etc/passwd` resolves to `/etc/passwd`.

**Assessment:** This is likely acceptable since the user controls the config file, but worth noting for security-conscious deployments.

## Missing Test Coverage

### 1. No Unit Tests for `get_operation_rules`

There are no tests that verify the operation rules merging logic. The bug in headers handling (Confirmed Bug #1) would have been caught by a test like:

```python
def test_empty_headers_override():
    """Empty headers in override should replace defaults, not fall back."""
    default_rules = OperationRules(
        headers={"content-type": FieldRule(predefined="ignore")}
    )
    rules_file = ComparisonRulesFile(
        version="1",
        default_rules=default_rules,
        operation_rules={
            "createWidget": OperationRules(headers={})  # Explicitly empty
        },
    )

    result = get_operation_rules(rules_file, "createWidget")
    assert result.headers == {}  # Should be empty, not inherited
```

### 2. No Unit Tests for `_substitute_env_vars` Edge Cases

Tests should cover:
- Environment variable set to empty string
- Environment variable name with spaces
- Environment variable name with shell default syntax `${VAR:-default}`
- Nested/recursive substitution patterns
- Multiple substitutions in one string
- Substitution in non-string values (should be no-op)

### 3. No Unit Tests for `load_runtime_config` Edge Cases

Tests should cover:
- Empty YAML file
- YAML file containing only comments
- YAML file containing a list instead of a mapping
- YAML file containing a scalar instead of a mapping
- Valid YAML but invalid config structure
- Config with all optional fields omitted

### 4. No Unit Tests for `resolve_comparison_rules_path`

Tests should cover:
- Relative paths
- Absolute paths
- Paths with `..` components
- Symlinks (if relevant)

### 5. No Tests for Predefined Parameter Value Edge Cases

Tests should verify behavior when:
- Required parameter is set to `0` (numeric)
- Required parameter is set to empty string `""`
- Required parameter is set to `False`

These are all "falsy" values that pass the current `if param_value is None` check, which may or may not be the intended behavior.
