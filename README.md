# api-parity

Differential fuzzing tool for comparing two API implementations against an OpenAPI specification. Find where they differ, replay failures to verify fixes. Perfect for API rewrites where you need to know: does the new implementation behave exactly like the old one?

**Status:** Ready for use. Both `explore` and `replay` commands are fully implemented.

**Languages:** Python (primary), Go (CEL evaluator subprocess)

## Why

API migration is hard. You have a working API, you're rewriting it, and you need to know: does the new implementation behave exactly like the old one?

api-parity sends identical requests to both implementations, compares responses under configurable rules, and saves mismatches for analysis and replay.

## Installation

Requires **Python 3.10+** and **Go 1.21+**.

```bash
git clone https://github.com/j-tyler/api-parity.git
cd api-parity
./scripts/build.sh
```

## Quick Start

### 1. Create Runtime Config (config.yaml)

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

### 2. Create Comparison Rules (comparison_rules.json)

```json
{
  "version": "1",
  "default_rules": {
    "body": {
      "$.id": {"predefined": "uuid_format"},
      "$.created_at": {"predefined": "iso8601_format"},
      "$.updated_at": {"predefined": "iso8601_format"}
    }
  },
  "operation_rules": {
    "getUser": {
      "body": {
        "$.last_login": {"predefined": "iso8601_format"}
      }
    }
  }
}
```

### 3. Run

```bash
export PROD_TOKEN="your-prod-token"
export STAGING_TOKEN="your-staging-token"

# Find differences
api-parity explore \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts

# After fixing issues, verify fixes
api-parity replay \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --in ./artifacts \
  --out ./replay-results
```

## Core Workflow

1. **Explore** — Generate tests from OpenAPI spec, find mismatches
2. **Analyze** — Review mismatch bundles in `./artifacts/mismatches/`
3. **Fix** — Update comparison rules for expected differences, or fix API bugs
4. **Replay** — Re-run saved mismatches to verify fixes
5. **Repeat** — Until all show `FIXED` or are documented as acceptable

## CLI Reference

### explore

| Option | Description |
|--------|-------------|
| `--spec PATH` | OpenAPI spec file (required) |
| `--config PATH` | Runtime config file (required) |
| `--target-a NAME` | First target name (required) |
| `--target-b NAME` | Second target name (required) |
| `--out PATH` | Output directory (required) |
| `--seed INT` | Random seed for reproducibility. With `--stateful` and `--max-chains`, enables seed walking (tries incrementing seeds until enough unique chains are found) |
| `--max-cases INT` | Limit number of test cases |
| `--stateful` | Enable chain testing via OpenAPI links |
| `--max-chains INT` | Max chains in stateful mode (default: 20) |
| `--max-steps INT` | Max steps per chain (default: 6) |
| `--ensure-coverage` | Guarantee all operations tested (chains are probabilistic; this adds single-request tests for any operations chains missed) |
| `--exclude OPID` | Exclude operation (repeatable) |
| `--validate` | Validate config without executing |

### replay

| Option | Description |
|--------|-------------|
| `--config PATH` | Runtime config file (required) |
| `--target-a NAME` | First target name (required) |
| `--target-b NAME` | Second target name (required) |
| `--in PATH` | Input directory with bundles (required) |
| `--out PATH` | Output directory (required) |
| `--validate` | Validate config without executing |

**Replay classifications:** `FIXED` (now matches), `STILL MISMATCH` (same failure), `DIFFERENT MISMATCH` (fails differently)

### Other Commands

```bash
api-parity list-operations --spec openapi.yaml    # Show operations and links
api-parity graph-chains --spec openapi.yaml       # Visualize link graph (Mermaid)
api-parity lint-spec --spec openapi.yaml          # Check for api-parity issues
```

## Predefined Comparisons (Quick Reference)

| Name | Description |
|------|-------------|
| **Format Validation** |
| `uuid_format` | Both match UUID regex |
| `iso_timestamp_format` | Both match ISO 8601 datetime |
| `iso_date_format` | Both match YYYY-MM-DD |
| `url_format` | Both are valid URLs |
| `jwt_format` | Both are valid JWT format |
| `base64_format` | Both are valid base64 |
| **Numeric** |
| `exact_match` | Values are equal (`a == b`) |
| `numeric_tolerance` | `|a - b| <= tolerance` (param: `tolerance`) |
| `both_positive` | Both > 0 |
| `both_in_range` | Both in [min, max] (params: `min`, `max`) |
| **Timestamps** |
| `epoch_seconds_tolerance` | Within N seconds (param: `seconds`) |
| `epoch_millis_tolerance` | Within N milliseconds (param: `millis`) |
| **Strings** |
| `string_nonempty` | Both non-empty |
| `string_length_match` | Same length |
| `both_match_regex` | Both match pattern (param: `pattern`) |
| **Arrays** |
| `unordered_array` | Same elements, any order (unique only) |
| `array_length` | Same length |
| **Special** |
| `ignore` | Always passes (skip comparison) |
| `same_nullity` | Both null or both non-null |
| `type_match` | Same JSON type |

See [comparison-rules.md](docs/comparison-rules.md) for full reference with all parameters.

## TLS Configuration

For mTLS or custom CA bundles:

```yaml
targets:
  production:
    base_url: https://api.example.com
    cert: /path/to/client.crt      # Client certificate
    key: /path/to/client.key       # Client private key
    key_password: ${KEY_PASSWORD}  # If key is encrypted
    ca_bundle: /path/to/ca.pem     # Custom CA bundle
    verify_ssl: true               # Set false to skip verification
```

## Why Complete OpenAPI Links Matter

api-parity's stateful testing (`--stateful` mode) depends entirely on explicit OpenAPI links defined in your spec. Unlike some tools, api-parity **disables automatic link inference** — it won't guess relationships from parameter names or Location headers.

**This design is intentional:** You should be able to read your spec and know exactly which chains will be tested. Inferred links can create false positives or miss real API contracts.

**The consequence:** If a link isn't defined in your spec, api-parity cannot:
- Pass data between those operations (IDs, tokens, etc.)
- Test that sequence as a meaningful stateful chain
- Verify the relationship between those endpoints

**Example:** If `POST /orders` returns an order ID that should be used with `GET /orders/{id}` and `DELETE /orders/{id}`, you need explicit links:

```yaml
paths:
  /orders:
    post:
      operationId: createOrder
      responses:
        '201':
          description: Order created
          links:
            GetOrder:
              operationId: getOrder
              parameters:
                orderId: '$response.body#/id'
            DeleteOrder:
              operationId: deleteOrder
              parameters:
                orderId: '$response.body#/id'
```

Without these links, api-parity can still call `getOrder` and `deleteOrder`, but will use random IDs instead of the one returned by `createOrder` — making the test meaningless.

**Recommendation:** Run `api-parity lint-spec --spec your-spec.yaml` to identify missing links, unreachable operations, and other coverage gaps.

## Documentation

| Document | Description |
|----------|-------------|
| [Quickstart](docs/quickstart.md) | Get running in 5 minutes |
| [Configuration](docs/configuration.md) | Full config reference, TLS, rate limiting |
| [Comparison Rules](docs/comparison-rules.md) | All predefined comparisons and CEL syntax |
| [OpenAPI Links](docs/openapi-links.md) | Enable stateful chain testing |
| [Troubleshooting](docs/troubleshooting.md) | Common errors and fixes |

## Technical Reference

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System structure, components, data models |
| [DESIGN.md](DESIGN.md) | Design decisions and reasoning |
| [TODO.md](TODO.md) | Future work ideas, notes to remember |

## License

MIT License — see [LICENSE](LICENSE) for details.
