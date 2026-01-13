# Quickstart

Get api-parity running in 5 minutes.

## Prerequisites

- Python 3.10+
- Go 1.21+
- Two API implementations to compare
- OpenAPI spec describing both APIs

## Install

```bash
git clone https://github.com/j-tyler/api-parity.git
cd api-parity
./scripts/build.sh
```

## Create Config Files

### 1. Runtime Config (`config.yaml`)

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

### 2. Comparison Rules (`comparison_rules.json`)

Minimal starting point—compare everything exactly:

```json
{
  "version": "1",
  "default_rules": {
    "status_code": {"predefined": "exact_match"},
    "headers": {},
    "body": {"field_rules": {}}
  },
  "operation_rules": {}
}
```

With empty `field_rules`, body content is not compared—only that both responses have a body. Add field rules as you discover which fields need specific comparison logic.

## Run

```bash
# Set auth tokens
export PROD_TOKEN="your-prod-token"
export STAGING_TOKEN="your-staging-token"

# Run comparison
api-parity explore \
  --spec openapi.yaml \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --out ./artifacts
```

## Interpret Results

**Console output:**
```
MATCH   GET /widgets (listWidgets)
MISMATCH POST /widgets (createWidget) - body
MATCH   GET /widgets/{id} (getWidget)
```

**Mismatch artifacts** written to `./artifacts/mismatches/`:
```
20260112T143052__createWidget__abc123/
  case.json         # Request that was sent
  target_a.json     # Response from production
  target_b.json     # Response from staging
  diff.json         # What differed
  metadata.json     # Run context
```

## Next Steps

1. Review `diff.json` to understand mismatches
2. Add comparison rules for expected differences (see [comparison-rules.md](comparison-rules.md))
3. Re-run to verify rules work
4. Add OpenAPI links for stateful chain testing (see [openapi-links.md](openapi-links.md))

## Common First-Run Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| All timestamps mismatch | No rule for volatile fields | Add `{"predefined": "iso_timestamp_format"}` |
| All UUIDs mismatch | Comparing server-generated IDs | Add `{"predefined": "uuid_format"}` |
| Price mismatches by 0.001 | Floating point differences | Add `{"predefined": "numeric_tolerance", "tolerance": 0.01}` |
| `CEL evaluator binary not found` | Build incomplete | Run `go build -o cel-evaluator ./cmd/cel-evaluator` |
