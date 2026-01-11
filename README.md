# api-parity

Differential fuzzing tool for comparing two API implementations against an OpenAPI specification. Find where they differ, replay failures to verify fixes. Perfect for API rewrites.

## Status

**In development** — `explore` subcommand fully implemented with stateless test generation, dual-target execution, response comparison, and mismatch artifact writing. `replay` subcommand pending. See ARCHITECTURE.md and DESIGN.md for technical details.

**Languages:** Python (primary), Go (CEL evaluator subprocess)

## Why

API migration is hard. You have a working API, you're rewriting it, and you need to know: does the new implementation behave exactly like the old one? Existing tools solve pieces of this problem but don't combine them for migration workflows. api-parity focuses specifically on differential testing between two implementations with replayable failure artifacts.

## Quick Start

```bash
# Install (development - package not yet published to PyPI)
pip install -e .

# Explore: generate tests and find mismatches
api-parity explore \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts

# Replay: re-run saved mismatches to verify fixes
api-parity replay \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --in ./artifacts/mismatches \
  --out ./artifacts/replay
```

## Configuration

### Config File (YAML)

The `--config` file defines targets, comparison rules, and optional settings:

```yaml
# Required: Define your API targets
targets:
  production:
    base_url: https://api.example.com
    headers:
      Authorization: "Bearer ${PROD_API_TOKEN}"  # Environment variable substitution
  staging:
    base_url: https://staging.api.example.com
    headers:
      Authorization: "Bearer ${STAGING_API_TOKEN}"

# Required: Path to comparison rules (JSON)
comparison_rules: ./comparison_rules.json

# Optional: Rate limiting
rate_limit:
  requests_per_second: 10.0

# Optional: Redact sensitive fields in artifacts
secrets:
  redact_fields:
    - "$.password"
    - "$.api_key"
    - "$.credentials.token"
```

### Comparison Rules (JSON)

Defines how responses are compared. See `tests/fixtures/comparison_rules.json` for a complete example.

```json
{
  "version": "1",
  "default_rules": {
    "status_code": {"predefined": "exact_match"},
    "body": {
      "field_rules": {
        "$.id": {"predefined": "uuid_format"},
        "$.created_at": {"predefined": "ignore"},
        "$.price": {"predefined": "numeric_tolerance", "tolerance": 0.01}
      }
    }
  },
  "operation_rules": {
    "getUser": {
      "body": {
        "field_rules": {
          "$.last_login": {"predefined": "ignore"}
        }
      }
    }
  }
}
```

### Predefined Comparisons

Built-in comparisons available via `{"predefined": "name"}`. In all expressions, `a` = Target A value, `b` = Target B value.

| Name | Parameters | Description |
|------|------------|-------------|
| `ignore` | — | Always passes. Field is not compared. |
| `exact_match` | — | Values must be exactly equal (`a == b`). |
| `numeric_tolerance` | `tolerance` | Numbers equal within tolerance: `\|a - b\| <= tolerance` |
| `uuid_format` | — | Both values are valid UUID format. Values not compared. |
| `uuid_v4_format` | — | Both values are valid UUID v4 format. Values not compared. |
| `iso_timestamp_format` | — | Both values are ISO 8601 timestamps. Values not compared. |
| `epoch_seconds_tolerance` | `seconds` | Unix timestamps (seconds) within N seconds of each other. |
| `epoch_millis_tolerance` | `millis` | Unix timestamps (milliseconds) within N ms of each other. |
| `unordered_array` | — | Arrays have same elements, order ignored. **WARNING:** Doesn't handle duplicates correctly. |
| `array_length` | — | Arrays have same length. Contents not compared. |
| `array_length_tolerance` | `tolerance` | Array lengths differ by at most N elements. |
| `string_prefix` | `length` | First N characters match. |
| `string_nonempty` | — | Both strings are non-empty. Content not compared. |
| `both_match_regex` | `pattern` | Both values match the regex pattern. |
| `both_null` | — | Both values are null. |
| `both_null_or_equal` | — | Both null, or both non-null and equal. |
| `type_match` | — | Values have same type. Values not compared. |
| `both_positive` | — | Both values are positive numbers. |
| `same_sign` | — | Both values have same sign (positive, negative, or zero). |
| `both_in_range` | `min`, `max` | Both values fall within [min, max] inclusive. |

### Custom CEL Expressions

For comparisons not covered by predefined rules, use [CEL (Common Expression Language)](https://github.com/google/cel-spec). The expression must return a boolean (`true` = match, `false` = mismatch).

Available variables:
- `a` — Value from Target A
- `b` — Value from Target B

Examples:

```json
{"expr": "a == b"}
{"expr": "size(a) == size(b)"}
{"expr": "a.startsWith(b.substring(0, 5))"}
{"expr": "a > 0 && b > 0 && (a - b) <= 10"}
```

CEL reference: [cel-spec language definition](https://github.com/google/cel-spec/blob/master/doc/langdef.md)

## CLI Reference

### `api-parity list-operations`

List all operations from an OpenAPI spec with their operationIds and links. Use this to discover operationIds for `--exclude` or `--operation-timeout`.

| Option | Required | Description |
|--------|----------|-------------|
| `--spec PATH` | Yes | Path to OpenAPI specification file (YAML or JSON) |

Example output:

```
createWidget
  POST /widgets
  Links:
    201 → GetCreatedWidget → getWidget

getWidget
  GET /widgets/{id}

listWidgets
  GET /widgets

Total: 3 operations
```

### `api-parity explore`

Generate test cases from an OpenAPI spec and compare responses between two targets.

| Option | Required | Description |
|--------|----------|-------------|
| `--spec PATH` | Yes | Path to OpenAPI specification file (YAML or JSON) |
| `--config PATH` | Yes | Path to runtime configuration file (YAML) |
| `--target-a NAME` | Yes | Name of first target (must exist in config `targets` section) |
| `--target-b NAME` | Yes | Name of second target (must exist in config `targets` section) |
| `--out PATH` | Yes | Output directory for mismatch artifacts |
| `--seed INT` | No | Random seed for reproducible test generation |
| `--max-cases INT` | No | Maximum number of test cases to generate |
| `--validate` | No | Validate config and spec without executing requests |
| `--exclude OPERATION_ID` | No | Exclude an operation by operationId (can be repeated) |
| `--timeout SECONDS` | No | Default timeout for each API call (default: 30s) |
| `--operation-timeout OPERATION_ID:SECONDS` | No | Set timeout for a specific operation (can be repeated) |

### `api-parity replay`

Re-execute previously saved mismatch bundles to confirm whether issues persist.

| Option | Required | Description |
|--------|----------|-------------|
| `--config PATH` | Yes | Path to runtime configuration file (YAML) |
| `--target-a NAME` | Yes | Name of first target (must exist in config `targets` section) |
| `--target-b NAME` | Yes | Name of second target (must exist in config `targets` section) |
| `--in PATH` | Yes | Input directory containing mismatch bundles |
| `--out PATH` | Yes | Output directory for replay artifacts |
| `--validate` | No | Validate config and replay cases without executing requests |
| `--timeout SECONDS` | No | Default timeout for each API call (default: 30s) |
| `--operation-timeout OPERATION_ID:SECONDS` | No | Set timeout for a specific operation (can be repeated) |

### Configuration Precedence

Options can be specified in the config file or as CLI arguments. CLI arguments take precedence for that run, allowing one-off overrides without editing the config file.

## How It Works

1. Parse the OpenAPI specification
2. Generate requests (including stateful chains via OpenAPI links)
3. Send identical requests to both targets
4. Compare responses under user-defined rules
5. Write mismatch bundles for analysis and replay

## Output Format

When a mismatch is detected, api-parity writes a **mismatch bundle** — a directory containing all the information needed to understand and replay the failure.

### Bundle Structure

```
artifacts/mismatches/
  20260111T143052__createWidget__abc123/
    case.json         # The request that was sent
    target_a.json     # Response from Target A
    target_b.json     # Response from Target B
    diff.json         # Structured comparison result
    metadata.json     # Run context (tool version, targets, seed)
```

### File Contents

**case.json** — The request case (stateless) or chain case (stateful):

```json
{
  "case_id": "abc123",
  "operation_id": "createWidget",
  "method": "POST",
  "path_template": "/widgets",
  "path_parameters": {},
  "rendered_path": "/widgets",
  "query": {},
  "headers": {"content-type": ["application/json"]},
  "body": {"name": "Test Widget", "price": 19.99},
  "media_type": "application/json"
}
```

**target_a.json** / **target_b.json** — Captured response from each target:

```json
{
  "request": { ... },
  "response": {
    "status_code": 201,
    "headers": {"content-type": ["application/json"]},
    "body": {"id": "widget-001", "name": "Test Widget", "price": 19.99},
    "elapsed_ms": 45.2,
    "http_version": "1.1"
  }
}
```

**diff.json** — Structured comparison result showing what differed:

```json
{
  "match": false,
  "mismatch_type": "body",
  "summary": "Body mismatch at $.price: 19.99 vs 20.00",
  "details": {
    "status_code": {"match": true, "differences": []},
    "headers": {"match": true, "differences": []},
    "body": {
      "match": false,
      "differences": [
        {
          "path": "$.price",
          "target_a": 19.99,
          "target_b": 20.00,
          "rule": "numeric_tolerance"
        }
      ]
    }
  }
}
```

**metadata.json** — Run context for reproducibility:

```json
{
  "tool_version": "0.1.0",
  "timestamp": "2026-01-11T14:30:52Z",
  "seed": 42,
  "target_a": {"name": "production", "base_url": "https://api.example.com"},
  "target_b": {"name": "staging", "base_url": "https://staging.example.com"},
  "comparison_rules_applied": "default"
}
```

## Documentation

- **ARCHITECTURE.md** — System structure, data models, component design
- **DESIGN.md** — Decisions and reasoning
- **TODO.md** — Planned work and open questions

## License

MIT License - see [LICENSE](LICENSE) for details.
