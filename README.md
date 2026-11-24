# api-parity

Differential fuzzing tool for comparing two API implementations against an OpenAPI specification. Find where they differ, replay failures to verify fixes. Perfect for API rewrites where you need to know: does the new implementation behave exactly like the old one?

**Status:** Ready for use. `explore`, `replay`, and `merge` commands are fully implemented.

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

# Combine mismatches from multiple explore runs into one deduplicated set
api-parity merge \
  --in ./artifacts_run1 ./artifacts_run2 \
  --out ./regression-suite
```

## Core Workflow

1. **Explore** — Generate tests from OpenAPI spec, find mismatches
2. **Analyze** — Review mismatch bundles in `./artifacts/mismatches/`
3. **Fix** — Update comparison rules for expected differences, or fix API bugs
4. **Replay** — Re-run saved mismatches to verify fixes
5. **Merge** — Combine mismatches from multiple explore runs, deduplicating by failure pattern
6. **Repeat** — Until all show `FIXED` or are documented as acceptable

## CLI Reference

### explore

| Option | Description |
|--------|-------------|
| `--spec PATH` | OpenAPI spec file (required) |
| `--config PATH` | Runtime config file (required) |
| `--target-a NAME` | First target name (required) |
| `--target-b NAME` | Second target name (required) |
| `--out PATH` | Output directory (required) |
| `--seed INT` | Random seed for reproducibility. With `--stateful`, enables coverage-guided seed walking |
| `--stateful` | Enable chain testing via OpenAPI links |
| `--max-chains INT` | Max chains in stateful mode (default: 20) |
| `--max-steps INT` | Max steps per chain (default: 6) |
| `--min-hits-per-op INT` | Min unique chains per linked operation (default: 1, stateful mode) |
| `--min-coverage INT` | % of linked ops that must meet min-hits-per-op (default: 100, stateful mode) |
| `--ensure-coverage` | In stateful mode, send up to three standalone requests to each target for every non-excluded operation missed by generated chains, including operations that change data. Report operations still uncovered. |
| `--log-chains` | Write executed chains to chains.txt (stateful mode) |
| `--exclude OPID` | Exclude operation (repeatable) |
| `--timeout SECONDS` | Default timeout per API call (default: 30) |
| `--operation-timeout OPID:SEC` | Per-operation timeout (repeatable) |
| `--validate` | Validate config without executing |

### replay

| Option | Description |
|--------|-------------|
| `--config PATH` | Runtime config file (required) |
| `--target-a NAME` | First target name (required) |
| `--target-b NAME` | Second target name (required) |
| `--in PATH` | Input directory with bundles (required) |
| `--out PATH` | Output directory (required) |
| `--spec PATH` | Use this OpenAPI spec for every input bundle (optional) |
| `--ignore-session-cookies` | Treat inherited cookies as irrelevant when verifying a fix |
| `--validate` | Validate config without executing |

**Replay classifications:** `FIXED` (now matches), `STILL MISMATCH` (same failure), `DIFFERENT MISMATCH` (fails differently)

If neither target resolves any link parameter, or both lack the same required
linked parameter, explore stops before sending that step and counts the chain in
`chain_incomplete`. Replay reports `INCOMPLETE` and names the bundle in
`incomplete_bundles` if it stopped before comparing the saved failing step.
If that step now passes and a later link blocks, replay reports `FIXED` and
identifies the unexecuted step. A later observed mismatch is still reported.

Path bindings need a non-null, nonempty value. Generated chains also record
which linked query, header, and cookie parameters the target operation requires.
A missing optional binding can keep its discovery value when another binding
resolves. A binding resolved by one target retains the per-target comparison.

**Session-dependent results.** Explore keeps each target’s cookies across cases and
chains. By default, a mismatch bundle records which requests used an earlier
session and omits its cookie values. To save those values for replay on the same
origin, set `secrets.store_session_cookies: true` before running explore. This
can put live credentials in `case.json` or `chain.json`. A current `Cookie`
header in the target configuration can supply a fresh session at replay time.

Replay still sends the requests when a cookie is unavailable or the deployment
has moved. An observed mismatch is reported as usual. An apparent match is
`INCOMPLETE` if the original session was omitted, or if a restored or supplied
session now receives HTTP 401 or 403 where the original request received
neither. The console and `replay_summary.json` identify the affected bundles
and targets. Use `--ignore-session-cookies` when you know the inherited cookies
are incidental, such as a load-balancer cookie for a public endpoint.
A chain continues to acquire and rotate its own cookies during replay.

Replay uses the schema saved in each bundle. Pass `--spec corrected.yaml` to
apply one contract to the entire input suite, including older bundles. Explore
and replay omit the spec from artifacts when `secrets.redact_fields` is configured,
since it can contain sensitive examples or validation constraints. For these
runs, supply the spec externally with `--spec`.

If a bundle has no snapshot, supply its schema with `replay --spec PATH`.

### merge

Combine mismatch bundles from multiple explore runs into a single deduplicated directory. Bundles with the same failure pattern (operation, mismatch type, failing paths) are deduplicated, keeping the latest.

| Option | Description |
|--------|-------------|
| `--in PATH [PATH ...]` | Input directories from explore runs (required, one or more) |
| `--out PATH` | Output directory for merged bundles (required) |

Merge only accepts explore output directories. Replay output is rejected — replay is for verifying fixes, not for building regression suites. The merged output is directly replayable with `replay --in`.

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

## XML API Support

APIs that return XML responses (e.g., S3-compatible APIs) are supported. XML response bodies are automatically parsed into dicts when the `content-type` header contains "xml". Dict request bodies are serialized to XML when `media_type` contains "xml". The entire comparison pipeline (JSONPath rules, CEL expressions, artifact storage) works identically for XML and JSON APIs.

**Limitations:**

- **Single-vs-list ambiguity.** A single XML child element (e.g., one `<Contents>`) converts to a scalar dict value, not a one-element list. Multiple siblings with the same tag become a list. The `xml_to_dict` function supports a `force_list` parameter to override this, but it is not yet exposed through user configuration. Both targets get the same conversion, so parity comparison is unaffected.
- **OpenAPI XML annotations ignored.** `xml:name`, `xml:attribute`, `xml:wrapped`, `xml:prefix`, and `xml:namespace` annotations in the OpenAPI spec are not respected during request body serialization. Element names are taken directly from dict keys (which come from JSON Schema property names). This works for APIs like S3 where XML element names match JSON Schema property names. APIs that rely on these annotations to rename or restructure elements will produce incorrect request bodies. See [DESIGN.md](DESIGN.md) "XML Body Conversion" for full rationale.

## Technical Reference

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System structure, components, data models |
| [DESIGN.md](DESIGN.md) | Design decisions and reasoning |
| [TODO.md](TODO.md) | Future work ideas, notes to remember |

## License

MIT License — see [LICENSE](LICENSE) for details.
