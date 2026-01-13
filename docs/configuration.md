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

# Optional: Rate limiting (not yet implemented)
rate_limit:
  requests_per_second: <number>

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

secrets:
  redact_fields:
    - "$.password"
    - "$.api_key"
    - "$.credentials.token"
    - "$.users[*].ssn"
```

## CLI Options

CLI arguments override config file values for that run.

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

### Examples

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

## Precedence

1. CLI arguments (highest)
2. Config file values
3. Built-in defaults (lowest)

## Validation

Use `--validate` to check configuration before running:
- Verifies target names exist
- Parses comparison rules
- Validates CEL expressions
- Checks OpenAPI spec is valid

No requests are made in validate mode, allowing safe pre-flight checks against production targets without side effects.
