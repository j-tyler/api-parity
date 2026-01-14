# Configuration

api-parity uses two configuration files:
1. **Runtime config** (YAML) — Targets, authentication, settings
2. **Comparison rules** (JSON) — How to compare responses

## Runtime Config

### Schema

```yaml
# Required: API targets to compare
targets:
  <name>:
    base_url: <string>           # Required. Base URL for this target.
    headers:                     # Optional. Headers sent with every request.
      <header-name>: <value>

# Required: Path to comparison rules file
comparison_rules: <path>

# Optional: Rate limiting
rate_limit:
  requests_per_second: <number>  # Maximum requests per second

# Optional: Redact sensitive fields in artifacts
secrets:
  redact_fields:
    - <jsonpath>
```

### Environment Variable Substitution

Use `${VAR_NAME}` syntax in any string value:

```yaml
targets:
  production:
    base_url: ${PROD_API_URL}
    headers:
      Authorization: "Bearer ${PROD_API_TOKEN}"
      X-API-Key: ${PROD_API_KEY}
```

Variables are resolved at config load time. Missing variables cause an error.

### Complete Example

```yaml
targets:
  production:
    base_url: https://api.example.com/v1
    headers:
      Authorization: "Bearer ${PROD_TOKEN}"
      X-Request-Source: api-parity

  staging:
    base_url: https://staging.api.example.com/v1
    headers:
      Authorization: "Bearer ${STAGING_TOKEN}"
      X-Request-Source: api-parity

comparison_rules: ./comparison_rules.json

# Limit to 10 requests per second to avoid overwhelming APIs
rate_limit:
  requests_per_second: 10

secrets:
  redact_fields:
    - "$.password"
    - "$.api_key"
    - "$.credentials.token"
    - "$.users[*].ssn"
```

## Rate Limiting

Configure `rate_limit.requests_per_second` to control how fast api-parity sends requests. This prevents overwhelming target APIs or triggering rate limits.

```yaml
rate_limit:
  requests_per_second: 10  # At most 10 requests/second
```

The rate limit applies globally across all requests (to both targets). If not specified, requests are sent as fast as possible.

**Note:** Each test case sends one request to each target (2 HTTP requests total). Setting `requests_per_second: 10` results in approximately 5 test cases per second.

## Progress Reporting

During explore and replay runs, api-parity prints progress to stderr every second:

```
[Progress] 45/100 cases (45.0%) | 5.2/s | ETA: 10s
```

The progress line shows:
- Completed / Total (if total is known)
- Current throughput rate
- Estimated time remaining (ETA)

If the run is interrupted (SIGINT/Ctrl+C), all mismatches found up to that point are preserved and the summary is still written with `"interrupted": true`.

## CLI Options

CLI arguments override config file values for that run.

### Explore Command

| CLI Flag | Description |
|----------|-------------|
| `--spec PATH` | OpenAPI spec file (required) |
| `--config PATH` | Runtime config file (required) |
| `--target-a NAME` | First target name from config (required) |
| `--target-b NAME` | Second target name from config (required) |
| `--out PATH` | Output directory for artifacts (required) |
| `--seed INT` | Random seed for reproducible generation |
| `--max-cases INT` | Limit number of test cases |
| `--validate` | Validate config without executing requests |
| `--exclude OPID` | Exclude operation by operationId (repeatable) |
| `--timeout SECONDS` | Default request timeout (default: 30) |
| `--operation-timeout OPID:SECONDS` | Per-operation timeout (repeatable) |
| `--stateful` | Enable stateful chain testing via OpenAPI links |
| `--max-chains INT` | Maximum chains to generate in stateful mode (default: 20) |
| `--max-steps INT` | Maximum steps per chain (default: 6) |

### Replay Command

| CLI Flag | Description |
|----------|-------------|
| `--config PATH` | Runtime config file (required) |
| `--target-a NAME` | First target name from config (required) |
| `--target-b NAME` | Second target name from config (required) |
| `--in PATH` | Input directory containing mismatch bundles (required) |
| `--out PATH` | Output directory for replay artifacts (required) |
| `--validate` | Validate config without executing requests |
| `--timeout SECONDS` | Default request timeout (default: 30) |
| `--operation-timeout OPID:SECONDS` | Per-operation timeout (repeatable) |

### List-Operations Command

| CLI Flag | Description |
|----------|-------------|
| `--spec PATH` | OpenAPI spec file (required) |

### Explore Examples

```bash
# Basic run
api-parity explore \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts

# Reproducible run with limited cases
api-parity explore \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts \
  --seed 42 \
  --max-cases 100

# Exclude slow operations, custom timeouts
api-parity explore \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts \
  --exclude generateReport \
  --exclude exportData \
  --timeout 10 \
  --operation-timeout processPayment:60

# Validate config without running
api-parity explore \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts \
  --validate
```

### Replay Examples

```bash
# Re-execute previously saved mismatches
api-parity replay \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --in ./artifacts \
  --out ./replay

# Replay with custom timeout
api-parity replay \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --in ./artifacts \
  --out ./replay \
  --timeout 60
```

## Precedence

1. CLI arguments (highest)
2. Config file values
3. Built-in defaults (lowest)

## Validation

Use `--validate` to check configuration before running. This performs comprehensive cross-validation between your config files and the OpenAPI spec.

### What Gets Validated

**Config structure:**
- Runtime config YAML syntax and required fields
- Comparison rules JSON syntax and schema
- Target names exist in config

**Cross-validation against OpenAPI spec:**
- `operationIds` in `operation_rules` exist in the spec (warns if not)
- `--exclude` operationIds exist in the spec (warns if not)
- `--operation-timeout` operationIds exist in the spec (warns if not)

**Comparison rules validation:**
- Predefined names are valid (errors if not: e.g., `"exact"` should be `"exact_match"`)
- Required parameters are present (errors if not: e.g., `numeric_tolerance` needs `tolerance`)

**Not validated:**
- Custom CEL expression syntax (in `"expr"` fields) — validated only at runtime when evaluated

### Output

```
Validating: spec=openapi.yaml, config=config.yaml
  Targets: production, staging
    production: https://api.example.com
    staging: https://staging.example.com
  Operations: 5
    createWidget (POST /widgets)
    getWidget (GET /widgets/{id})
    ...

Cross-validating configuration...

Warnings:
  WARNING: [operation_rules] operationId 'createWidgett' not found in spec. Rules for this operation will be ignored.

Errors:
  ERROR: [predefined] default_rules.body.field_rules[$.price]: Predefined 'numeric_tolerance' requires parameter 'tolerance' but it was not provided.

Validation failed
```

**Exit codes:**
- `0` — Validation passed (may have warnings)
- `1` — Validation failed (has errors)

No requests are made in validate mode, allowing safe pre-flight checks without side effects.
