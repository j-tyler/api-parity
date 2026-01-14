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

## Analyze Mismatch Bundles

Each bundle directory contains everything needed to understand the failure:

**diff.json** — Start here. Shows what failed and why:
```json
{
  "match": false,
  "mismatch_type": "body",
  "summary": "Body mismatch at $.price",
  "details": {
    "body": {
      "differences": [
        {"path": "$.price", "target_a": 19.99, "target_b": 20.00, "rule": "exact_match"}
      ]
    }
  }
}
```

**case.json** — The request that triggered the mismatch. Useful for reproducing manually.

**target_a.json / target_b.json** — Full responses for detailed comparison.

## Iterative Fix Workflow

The typical workflow cycles through: explore → analyze → fix → replay.

### Cycle 1: Initial Discovery

```bash
# Run exploration
api-parity explore --spec openapi.yaml --config config.yaml \
  --target-a production --target-b staging --out ./round1

# Review mismatches
ls ./round1/mismatches/
grep -h '"summary"' ./round1/mismatches/*/diff.json
```

### Cycle 2: Add Rules for Expected Differences

Update `comparison_rules.json` based on what you learned:

```json
{
  "version": "1",
  "default_rules": {
    "status_code": {"predefined": "exact_match"},
    "body": {
      "field_rules": {
        "$.id": {"predefined": "uuid_format"},
        "$.created_at": {"predefined": "iso_timestamp_format"},
        "$.updated_at": {"predefined": "iso_timestamp_format"}
      }
    }
  }
}
```

### Cycle 3: Replay to Verify

```bash
api-parity replay \
  --config config.yaml \
  --target-a production \
  --target-b staging \
  --in ./round1 \
  --out ./round1-replay
```

**Console output:**
```
[1] createWidget: POST /widgets FIXED
[2] getWidget: GET /widgets/{id} STILL MISMATCH: body mismatch at $.price
============================================================
Total bundles: 2
  Fixed (now match):     1
  Still mismatch:        1
  Different mismatch:    0
  Errors:                0

Fixed bundles:
  20260112T143052__createWidget__abc123
```

### Cycle 4: Investigate Remaining Issues

For `STILL MISMATCH` cases, examine the new bundles in `./round1-replay/mismatches/`:
- If expected difference: add more rules
- If real bug: fix the API implementation

Repeat until all show `FIXED` or are documented as acceptable.

## Replay Classifications

| Classification | Meaning | Action |
|---------------|---------|--------|
| `FIXED` | Was mismatch, now matches | Issue resolved |
| `STILL MISMATCH` | Same failure pattern | Investigate further |
| `DIFFERENT MISMATCH` | Fails differently | Rules changed or new issue |

Results are also written to `./round1-replay/replay_summary.json` for programmatic access.

## Next Steps

1. Review `diff.json` to understand mismatches
2. Add comparison rules for expected differences (see [comparison-rules.md](comparison-rules.md))
3. Run replay to verify rules work
4. Add OpenAPI links for stateful chain testing (see [openapi-links.md](openapi-links.md))

## Common First-Run Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| All timestamps mismatch | No rule for volatile fields | Add `{"predefined": "iso_timestamp_format"}` |
| All UUIDs mismatch | Comparing server-generated IDs | Add `{"predefined": "uuid_format"}` |
| Price mismatches by 0.001 | Floating point differences | Add `{"predefined": "numeric_tolerance", "tolerance": 0.01}` |
| `CEL evaluator binary not found` | Build incomplete | Run `go build -o cel-evaluator ./cmd/cel-evaluator` |
