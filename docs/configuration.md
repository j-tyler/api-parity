# Configuration

api-parity uses two configuration files:
- **Runtime config** (YAML) — Targets, authentication, rate limits, secrets
- **Comparison rules** (JSON) — How to compare responses (see [comparison-rules.md](comparison-rules.md))

## Runtime Config

### Minimal Example

```yaml
targets:
  production:
    base_url: https://api.example.com
    headers:
      Authorization: "Bearer ${PROD_TOKEN}"
  staging:
    base_url: https://staging.example.com
    headers:
      Authorization: "Bearer ${STAGING_TOKEN}"

comparison_rules: ./comparison_rules.json
```

### Full Schema

```yaml
# Required: API targets to compare
targets:
  <name>:
    base_url: <string>           # Required
    headers:                     # Optional: sent with every request
      <header-name>: <value>
    # TLS options (all optional)
    cert: <path>                 # Client certificate (PEM)
    key: <path>                  # Client private key (PEM)
    key_password: <string>       # Password for encrypted key
    ca_bundle: <path>            # Custom CA bundle
    verify_ssl: <bool>           # Skip verification (default: true)
    ciphers: <string>            # OpenSSL cipher string

# Required: path to comparison rules JSON
comparison_rules: <path>

# Optional: rate limiting
rate_limit:
  requests_per_second: <number>

# Optional: redact fields in artifacts
secrets:
  redact_fields:
    - <jsonpath>
```

### Environment Variables

Use `${VAR_NAME}` in any string value:

```yaml
targets:
  production:
    base_url: ${PROD_API_URL}
    headers:
      Authorization: "Bearer ${PROD_TOKEN}"
```

Missing variables cause an error at load time.

### TLS Configuration

| Option | Description |
|--------|-------------|
| `cert` / `key` | Client certificate and key for mTLS (both required together) |
| `key_password` | Password for encrypted private key |
| `ca_bundle` | Custom CA bundle for server verification |
| `verify_ssl` | Set `false` to skip server certificate verification |
| `ciphers` | OpenSSL cipher string (e.g., `'ECDHE+AESGCM'`) |

### Rate Limiting

```yaml
rate_limit:
  requests_per_second: 10  # Maximum 10 req/sec
```

Applies globally across all requests. Each test case sends 2 HTTP requests (one per target), so `10` req/sec means ~5 test cases/sec.

### Secret Redaction

```yaml
secrets:
  redact_fields:
    - "$.password"
    - "$.api_key"
    - "$.users[*].ssn"
```

Redacted values are replaced with `"[REDACTED]"` in mismatch bundles.

---

## CLI Reference

### `api-parity explore`

Generate test cases and compare responses.

```bash
api-parity explore \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts
```

| Option | Required | Description |
|--------|----------|-------------|
| `--spec PATH` | Yes | OpenAPI spec file |
| `--config PATH` | Yes | Runtime config file |
| `--target-a NAME` | Yes | First target name |
| `--target-b NAME` | Yes | Second target name |
| `--out PATH` | Yes | Output directory |
| `--seed INT` | No | Random seed for reproducibility |
| `--max-cases INT` | No | Limit test cases |
| `--validate` | No | Validate without executing |
| `--exclude OPID` | No | Exclude operation (repeatable) |
| `--timeout SECONDS` | No | Default timeout (default: 30) |
| `--operation-timeout OPID:SEC` | No | Per-operation timeout (repeatable) |
| `--stateful` | No | Enable chain testing via OpenAPI links |
| `--max-chains INT` | No | Max chains in stateful mode (default: 20) |
| `--max-steps INT` | No | Max steps per chain (default: 6) |
| `--log-chains` | No | Write chains to chains.txt |
| `--ensure-coverage` | No | Test all operations (stateful mode) |

**Seed walking:** When `--seed N` and `--max-chains M` are both specified, if seed N produces fewer than M unique chains, the system automatically tries seeds N+1, N+2, etc. (up to 100 attempts) until M chains are accumulated.

### `api-parity replay`

Re-execute saved mismatches to verify fixes.

```bash
api-parity replay \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --in ./artifacts \
  --out ./replay-results
```

| Option | Required | Description |
|--------|----------|-------------|
| `--config PATH` | Yes | Runtime config file |
| `--target-a NAME` | Yes | First target name |
| `--target-b NAME` | Yes | Second target name |
| `--in PATH` | Yes | Input directory with bundles |
| `--out PATH` | Yes | Output directory |
| `--validate` | No | Validate without executing |
| `--timeout SECONDS` | No | Default timeout (default: 30) |
| `--operation-timeout OPID:SEC` | No | Per-operation timeout (repeatable) |

**Replay classifications:**
- `FIXED` — Previously mismatched, now matches
- `STILL MISMATCH` — Same failure pattern persists
- `DIFFERENT MISMATCH` — Fails differently (rules changed or new issue)

### `api-parity list-operations`

Show operations from OpenAPI spec.

```bash
api-parity list-operations --spec openapi.yaml
```

### `api-parity graph-chains`

Visualize OpenAPI link relationships.

```bash
# Static link graph (Mermaid)
api-parity graph-chains --spec openapi.yaml

# Actual generated chains
api-parity graph-chains --spec openapi.yaml --generated
```

| Option | Required | Description |
|--------|----------|-------------|
| `--spec PATH` | Yes | OpenAPI spec file |
| `--exclude OPID` | No | Exclude operation (repeatable) |
| `--generated` | No | Show actual chains, not static graph |
| `--max-chains INT` | No | Max chains with `--generated` |
| `--max-steps INT` | No | Max steps with `--generated` |
| `--seed INT` | No | Seed for `--generated` |

### `api-parity lint-spec`

Check OpenAPI spec for api-parity issues.

```bash
api-parity lint-spec --spec openapi.yaml
api-parity lint-spec --spec openapi.yaml --output json
```

**Exit codes:** `0` = no errors, `1` = errors found

---

## Precedence

1. CLI arguments (highest)
2. Config file values
3. Built-in defaults (lowest)

---

## Validation

Use `--validate` for pre-flight checks without making requests:

```bash
api-parity explore --validate --spec openapi.yaml --config config.yaml ...
```

**Validates:**
- Config file syntax and required fields
- Target names exist
- Predefined comparison names are valid
- Required parameters present
- operationIds in rules/CLI exist in spec (warns if not)

**Not validated:** Custom CEL expressions (validated at runtime)

---

## Progress Reporting

During runs, progress prints every 10 seconds:

```
[Progress] 45/100 cases (45.0%) | 5.2/s | ETA: 10s
```

If interrupted (Ctrl+C), all mismatches found up to that point are preserved.
