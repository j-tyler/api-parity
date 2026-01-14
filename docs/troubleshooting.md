# Troubleshooting

Common errors and how to fix them.

## Installation Errors

### CEL evaluator binary not found

```
CELSubprocessError: CEL evaluator binary not found
```

**Cause:** Go binary wasn't built or isn't in the expected location.

**Fix:**
```bash
go build -o cel-evaluator ./cmd/cel-evaluator
```

The binary must be in the repository root (next to `pyproject.toml`).

### Go not found

```
[ERROR] Go not found. Install Go 1.21 or later
```

**Fix:** Install Go from https://go.dev/dl/ and ensure `go` is in your PATH.

### Python version too old

```
[ERROR] Python 3.10+ required, found 3.8.x
```

**Fix:** Install Python 3.10 or later. Consider using pyenv for version management.

## Configuration Errors

### Unknown predefined

```
Unknown predefined comparison: 'exact'
```

**Cause:** Typo in predefined name.

**Fix:** Check spelling. Valid names: `exact_match`, `ignore`, `uuid_format`, etc. See [comparison-rules.md](comparison-rules.md) for full list.

### Missing required parameter

```
Predefined 'numeric_tolerance' requires parameter 'tolerance'
```

**Cause:** Parameterized predefined missing required value.

**Fix:**
```json
{"predefined": "numeric_tolerance", "tolerance": 0.01}
```

### Target not found

```
Target 'prod' not found in config. Available: production, staging
```

**Cause:** CLI `--target-a` or `--target-b` doesn't match config file.

**Fix:** Use exact target name from config, or fix config.

### Environment variable not set

```
Environment variable PROD_TOKEN is not set
```

**Cause:** Config uses `${PROD_TOKEN}` but variable not exported.

**Fix:**
```bash
export PROD_TOKEN="your-token-here"
```

### operationId not found in spec (warning)

```
WARNING: [operation_rules] operationId 'createWidgett' not found in spec
```

**Cause:** Typo in operationId, or operation was removed from spec.

**Fix:** Check spelling against `api-parity list-operations --spec openapi.yaml`. Common typos:
- Extra letters (`createWidgett` vs `createWidget`)
- Case mismatch (`createwidget` vs `createWidget`)
- Underscore vs camelCase (`create_widget` vs `createWidget`)

**Note:** This is a warning, not an error. The rules for this operationId will be silently ignored, falling back to default_rules.

### Rules for operationId silently ignored

**Symptom:** You defined custom rules for an operation, but they're not being used.

**Cause:** The operationId in `operation_rules` doesn't match the spec exactly.

**Fix:** Run `--validate` to check for operationId mismatches:
```bash
api-parity explore --validate --spec openapi.yaml --config config.yaml ...
```

Look for warnings like: `operationId 'X' not found in spec`

## CEL Expression Errors

### Syntax error in expression

```
CEL syntax error: mismatched input 'x' expecting ')'
```

**Cause:** Invalid CEL syntax in custom expression.

**Fix:** Validate CEL syntax. Common issues:
- Missing quotes around strings
- Wrong operator (`=` instead of `==`)
- Unclosed parentheses

### Type error

```
CEL error: no such overload: int + string
```

**Cause:** Comparing incompatible types.

**Fix:** Ensure both values have compatible types, or add type conversion:
```json
{"expr": "string(a) == string(b)"}
```

### Evaluation timeout

```
rule: "error: evaluation timeout exceeded"
```

**Cause:** CEL expression took >5 seconds (likely infinite loop or pathological regex).

**Fix:** Simplify expression. Avoid expensive regex patterns on large strings.

## JSONPath Errors

### No matches found

```
JSONPath $.widgets[*].id returned no matches
```

**Cause:** Path doesn't match response structure.

**Fix:** Verify path against actual response. Common issues:
- Response is an array, not object (`$[*].id` not `$.items[*].id`)
- Field name mismatch (`$.widget_id` vs `$.widgetId`)
- Nested path wrong (`$.data.items` vs `$.items`)

### Syntax error

```
Invalid JSONPath: $.items[
```

**Cause:** Malformed JSONPath expression.

**Fix:** Validate JSONPath syntax. Must have matching brackets, valid operators.

## Request Errors

### Connection refused

```
Connection error: [Errno 111] Connection refused
```

**Cause:** Target server not running or wrong URL.

**Fix:** Verify target URL is correct and server is accessible.

### Timeout

```
Request timeout after 30s for POST /widgets
```

**Cause:** Server took too long to respond.

**Fix:** Increase timeout:
```bash
--timeout 60
--operation-timeout createWidget:120
```

### SSL error

```
SSL: CERTIFICATE_VERIFY_FAILED
```

**Cause:** Invalid or self-signed certificate.

**Fix:** api-parity does not disable SSL verification. For self-signed certificates, configure your system or environment to trust the certificate, or use a proper certificate for testing.

## Mismatch Analysis

### All timestamps mismatch

**Cause:** Comparing server-generated timestamps.

**Fix:** Use format validation instead of exact match:
```json
"$.created_at": {"predefined": "iso_timestamp_format"}
```

### All UUIDs mismatch

**Cause:** Comparing server-generated IDs.

**Fix:** Validate format, don't compare values:
```json
"$.id": {"predefined": "uuid_format"}
```

### Prices differ by tiny amounts

**Cause:** Floating-point representation differences.

**Fix:** Use tolerance:
```json
"$.price": {"predefined": "numeric_tolerance", "tolerance": 0.01}
```

### Array order differs

**Cause:** Servers return items in different order.

**Fix:** If order doesn't matter:
```json
"$.tags": {"predefined": "unordered_array"}
```

**Warning:** Only use for arrays with unique elements.

### Field present in one, missing in other

**Cause:** Different schema implementations.

**Fix:** If field is optional:
```json
"$.description": {"presence": "optional", "predefined": "exact_match"}
```

If field should exist in both, this is a real mismatch—investigate.

## Chain Testing Issues

### No chains generated

**Cause:** No OpenAPI links defined.

**Fix:** Add `links:` sections to your spec. See [openapi-links.md](openapi-links.md).

### Chains are very short

**Cause:** Links don't form connected graph.

**Fix:** Add more links, especially bidirectional (get→update, update→get).

### Chain stops unexpectedly

**Cause:** Early step had mismatch.

**Fix:** Review mismatch artifact for the failed step. Chains stop at first mismatch.

### Variable not substituted

**Cause:** Link expression doesn't match response.

**Fix:** Verify JSON Pointer path in link expression matches actual response field name.

## Replay Issues

### All bundles show DIFFERENT MISMATCH

**Cause:** Comparison rules changed between explore and replay runs.

**Explanation:** Replay classifies by failure pattern (mismatch_type and paths), not exact values. If rules changed, the same data may fail at different fields.

**Fix:** This is expected when updating rules. After finalizing rules, run a fresh `explore` to create a new baseline for future replay cycles.

### Bundle fails to load

```
ERROR: {bundle_path} - missing case.json
```

**Cause:** Bundle directory is incomplete or corrupted.

**Fix:** Re-run explore to regenerate bundles. If the issue persists, check disk space and permissions.

### No mismatch bundles found

```
No mismatch bundles found in ./artifacts
```

**Cause:** Input directory doesn't contain bundles, or bundles are in a subdirectory.

**Fix:** Replay looks for bundles in `{input}/mismatches/` or directly in `{input}`. Ensure you're pointing to the explore output directory, not a specific bundle.

```bash
# Correct - points to explore output directory
api-parity replay --in ./artifacts ...

# Wrong - points to a specific bundle
api-parity replay --in ./artifacts/mismatches/20260112T... ...
```

### Chain replay uses wrong variables

**Cause:** During replay, link_fields are extracted from the chain's stored `link_source` data, not the OpenAPI spec. If the chain was generated with a different spec version, field names may not match current responses.

**Fix:** Regenerate chains with the current spec using `explore --stateful`.

### replay_summary.json not written

**Cause:** Replay was interrupted before completion.

**Fix:** Re-run replay. The summary is written after all bundles are processed.

### FIXED count is 0 but issues were resolved

**Cause:** The original mismatch may have involved multiple fields. You fixed one issue, but another field (e.g., a timestamp) still mismatches because it lacks a comparison rule.

**Fix:**
1. Check if comparison rules cover all fields that originally mismatched
2. For volatile fields (timestamps, UUIDs), add format validation rules
3. Run `explore` again to get fresh baseline bundles

### Bundle count lower than expected

**Cause:** `discover_bundles()` silently skips directories that don't contain `case.json` or `chain.json`.

**Fix:** Compare expected vs actual: `ls ./artifacts/mismatches | wc -l` vs `total_bundles` in `replay_summary.json`. If they differ, check that each bundle directory contains the required files.

## Debug Tips

### Validate before running

```bash
api-parity explore --validate \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts
```

### Limit cases for debugging

```bash
api-parity explore \
  --max-cases 10 \
  --seed 42 \
  ...
```

### Check discovered operations

```bash
api-parity list-operations --spec openapi.yaml
```

### Examine mismatch artifacts

Each mismatch bundle contains:
- `case.json` — What was sent
- `target_a.json` / `target_b.json` — What came back
- `diff.json` — What differed and why
