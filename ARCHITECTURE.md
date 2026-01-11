# Architecture

High-level architecture of api-parity, optimized for AI agents to quickly understand the codebase.

## Specification Status Key

Each section is marked with its completeness:
- **[SPECIFIED]** — Ready for implementation
- **[NEEDS SPEC]** — Concept is clear, but format/schema needs definition before implementation
- **[CONCEPT]** — Idea only, needs design work before implementation

---

## Summary [SPECIFIED]

api-parity is a local CLI that compares two deployments of a "theoretically identical" HTTP API to discover where they differ. This is especially useful when re-implementing an existing API whose true contract includes unknown quirks. The CLI uses an OpenAPI spec to generate requests (including stateful chains via OpenAPI links), executes them against two base URLs, compares responses under configurable rules, and emits high-quality mismatch artifacts that downstream tooling (including AI agents) can use to build permanent test fixtures.

## Goals [SPECIFIED]

1. **Stateful request generation** — Generate request sequences like `Create → Get → Update → Delete` using OpenAPI links.

2. **Dual-host execution and comparison** — For each generated request (or chain step), call both targets and compare status code, headers, and body.

3. **Actionable mismatch artifacts** — On mismatch, produce an output bundle describing the request(s), runtime context, both responses, structured diff, and enough information for replay. The CLI does not generate test code; it produces machine-readable artifacts.

## Non-Goals (v0) [SPECIFIED]

- Universal response equivalence without configuration
- Installing software on either target deployment
- Mocking/stubbing downstream dependencies
- Eventually consistent APIs (see DESIGN.md)

---

## Execution Modes [SPECIFIED]

### Mode A: Explore

Use OpenAPI-driven generation to create many test cases (including chained cases via links). Compare responses between two targets. When mismatch is found, emit a Mismatch Report Bundle.

```
api-parity explore \
  --spec openapi.yaml \
  --config runtime.yaml \
  --target-a <name> \
  --target-b <name> \
  --out ./artifacts \
  [--seed 123] \
  [--max-cases 1000]
```

### Mode B: Replay

Load previously saved mismatch bundles and re-execute them against both targets. Confirm whether mismatches still exist (regression tracking).

```
api-parity replay \
  --config runtime.yaml \
  --target-a <name> \
  --target-b <name> \
  --in ./artifacts/mismatches \
  --out ./artifacts/replay
```

### Exit Codes

- `0`: No mismatches (or all replays match)
- `1`: Mismatches found
- `2`: Tool error

---

## Component Architecture [SPECIFIED]

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI Frontend                              │
│  - Parse args                                                    │
│  - Load OpenAPI spec                                             │
│  - Load runtime config                                           │
│  - Dispatch to Explore or Replay mode                            │
│  - Start/stop CEL Evaluator subprocess                           │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Case Generator                              │
│  - Generate stateless requests from OpenAPI                      │
│  - Generate stateful chains using OpenAPI links                  │
│  - Implementation: Schemathesis (see DESIGN.md)                  │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Executor                                 │
│  - Send request to Target A, capture response                    │
│  - Send same request to Target B, capture response               │
│  - Serial execution only (no concurrency)                        │
│  - For chains: use Target A's response data for A's next step,   │
│    Target B's response data for B's next step                    │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Comparator                                │
│  - Apply per-endpoint comparison rules (user-defined)            │
│  - Produce structured diff with field-level details              │
│  - Delegates CEL evaluation to CEL Evaluator                     │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
│                   CEL Evaluator (Go subprocess)                  │
│  - Runs cel-go for expression evaluation                         │
│  - Stdin/stdout IPC with NDJSON protocol                         │
│  - Stateless: (expr, data) → bool                                │
└ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Artifact Writer                              │
│  - Write mismatch bundles to disk                                │
│  - Redact configured secret fields                               │
│  - Write run logs and summary stats                              │
└─────────────────────────────────────────────────────────────────┘
```

Note: CEL Evaluator shown with dashed border to indicate it's a subprocess, not in-process Python.

---

## CEL Evaluator Component [SPECIFIED]

The CEL Evaluator is a Go subprocess that evaluates CEL expressions for the Comparator. See DESIGN.md "CEL Evaluation via Go Subprocess" and "Stdin/Stdout IPC for CEL Subprocess" for rationale.

### Why a Subprocess

Python CEL libraries are unavailable (untrusted dependencies). The reference CEL implementation is cel-go. A subprocess isolates Go code behind a simple interface.

### Building

```bash
go build -o cel-evaluator cmd/cel-evaluator
```

### Interface

Python side (`api_parity/cel_evaluator.py`):
```python
class CELEvaluator:
    def evaluate(self, expression: str, data: dict[str, Any]) -> bool: ...
    def close(self) -> None: ...
```

The Comparator calls `evaluate()` for each field comparison. It has no knowledge of the subprocess—just a function that takes an expression and data, returns a boolean. Use as context manager for automatic cleanup: `with CELEvaluator() as e: ...`

### IPC Protocol

Communication uses stdin/stdout with newline-delimited JSON.

**Startup:**
1. Python spawns `./cel-evaluator` with `stdin=PIPE, stdout=PIPE`
2. Go writes `{"ready":true}\n`
3. Python reads ready message, proceeds

**Request/Response:**
```
Python: {"id":"<uuid>","expr":"a == b","data":{"a":1,"b":1}}\n
Go:     {"id":"<uuid>","ok":true,"result":true}\n
```

**Error:**
```
Python: {"id":"<uuid>","expr":"undefined_var","data":{}}\n
Go:     {"id":"<uuid>","ok":false,"error":"undeclared reference to 'undefined_var'"}\n
```

### Lifecycle

- Started once at CLI startup (before explore/replay begins)
- Kept alive for duration of run
- Terminated on CLI exit (close stdin, wait for process)
- Restarted automatically if subprocess crashes (EOF detected on stdout)

---

## Internal Data Models [SPECIFIED]

Python classes (Pydantic v2) representing HTTP requests and responses as they flow through the tool. The Executor creates these when sending requests; the Artifact Writer serializes them to mismatch bundles on disk.

### RequestCase

Represents one HTTP request that can be executed, recorded, and replayed.

| Field | Type | Description |
|-------|------|-------------|
| case_id | string | Unique identifier for traceability (maps to Schemathesis `X-Schemathesis-TestCaseId`) |
| operation_id | string | OpenAPI operationId |
| method | string | HTTP method |
| path_template | string | Path with parameter placeholders, e.g., `/items/{id}` |
| path_parameters | object | Parameter values, e.g., `{"id": "abc123"}` |
| rendered_path | string | Fully rendered path (computed from template + parameters) |
| query | object | Query parameters (arrays for repeated params) |
| headers | object | Request headers (arrays for repeated headers) |
| cookies | object | Cookies (separate from headers for clarity) |
| body | any | Body as JSON value if parseable |
| body_base64 | string | Body as base64 if binary (mutually exclusive with body) |
| media_type | string | Content-Type, e.g., `application/json` |

**Header and query structure:** Values are arrays to support repeated parameters (e.g., `{"Accept": ["application/json", "text/plain"]}`).

**Why all three path fields?** `path_template` + `path_parameters` preserve which values caused failures; `rendered_path` gives the actual URL without computation. `operation_id` is required for comparison rule lookup.

**Example:**
```json
{
  "case_id": "test-abc123",
  "operation_id": "createItem",
  "method": "POST",
  "path_template": "/categories/{category}/items",
  "path_parameters": {"category": "electronics"},
  "rendered_path": "/categories/electronics/items",
  "query": {"notify": ["true"]},
  "headers": {"Content-Type": ["application/json"], "Accept": ["application/json"]},
  "cookies": {},
  "body": {"name": "Widget", "price": 9.99},
  "media_type": "application/json"
}
```

### ResponseCase

Represents one HTTP response captured from a target.

| Field | Type | Description |
|-------|------|-------------|
| status_code | integer | HTTP status code |
| headers | object | Response headers (arrays for repeated headers, lowercase keys) |
| body | any | Body as JSON value if parseable |
| body_base64 | string | Body as base64 if binary |
| elapsed_ms | number | Response time in milliseconds |
| http_version | string | Protocol version, e.g., `1.1` |

### ChainCase

Represents a stateful sequence of requests (template only, no execution data).

| Field | Type | Description |
|-------|------|-------------|
| chain_id | string | Unique identifier |
| steps | array | Ordered list of ChainStep |

### ChainStep

One step in a chain (template only).

| Field | Type | Description |
|-------|------|-------------|
| step_index | integer | 0-based position in chain |
| request_template | RequestCase | The request template (path_template populated, path_parameters empty until execution) |
| link_source | object or null | Which previous step and field provides data for this step |

**Note:** `ChainCase` and `ChainStep` describe the chain template. Execution data (actual requests sent, responses received, extracted variables) is stored separately in `target_a.json` and `target_b.json` in the mismatch bundle. This separation keeps the template reusable for replay while storing target-specific execution traces.

### ChainExecution

Execution trace for one target (stored in bundle's `target_a.json` / `target_b.json` for chains).

| Field | Type | Description |
|-------|------|-------------|
| steps | array | Ordered list of ChainStepExecution |

### ChainStepExecution

Execution of one step on one target.

| Field | Type | Description |
|-------|------|-------------|
| step_index | integer | Matches ChainStep.step_index |
| request | RequestCase | The actual request sent (all fields populated) |
| response | ResponseCase | The response received |
| extracted | object | Variables extracted for subsequent steps |

**Chain execution semantics:** Both targets execute the same chain operations, but each uses its own extracted data. If POST returns `{"id": "abc"}` on Target A and `{"id": "xyz"}` on Target B, subsequent GET uses `/items/abc` for A and `/items/xyz` for B.

### Resolved Questions

1. **Schema format:** Pydantic v2 models. See DESIGN.md "Pydantic v2 for Data Models" for rationale.

2. **Variable extraction:** Handled by Schemathesis state machine internals. The `extracted` field in ChainStepExecution stores values pulled from responses per OpenAPI link definitions. We don't need to implement custom extraction.

---

## Mismatch Report Bundle [SPECIFIED]

On mismatch, produce a bundle containing everything needed for replay and analysis. This format intentionally differs from Schemathesis's VCR cassettes because we need to store parallel execution on two targets, not sequential interactions with one.

### Bundle Structure

```
mismatches/
  <timestamp>__<operation_id>__<case_id>/
    case.json           # The RequestCase or ChainCase
    target_a.json       # Request sent + response received from A
    target_b.json       # Request sent + response received from B
    diff.json           # Structured comparison result
    metadata.json       # Run context
```

### case.json

For stateless tests: A single `RequestCase` object.

For stateful tests: A `ChainCase` object with all steps and their link relationships.

### target_a.json / target_b.json

For stateless tests:
```json
{
  "request": { /* RequestCase */ },
  "response": { /* ResponseCase */ }
}
```

For stateful chains (uses ChainExecution structure):
```json
{
  "steps": [
    {
      "step_index": 0,
      "request": { /* RequestCase with all fields populated */ },
      "response": { /* ResponseCase */ },
      "extracted": { "item_id": "abc123" }
    },
    {
      "step_index": 1,
      "request": { /* RequestCase using extracted item_id */ },
      "response": { /* ResponseCase */ },
      "extracted": {}
    }
  ]
}
```

### diff.json [SPECIFIED]

Structured comparison result. Not a traditional diff—records which comparisons passed/failed and the values involved.

```json
{
  "mismatch_type": "body",
  "summary": "Response body field 'status' differs: 'active' vs 'pending'",
  "details": {
    "status_code": { "match": true },
    "headers": { "match": true },
    "body": {
      "match": false,
      "differences": [
        {
          "path": "$.status",
          "target_a": "active",
          "target_b": "pending",
          "rule": "exact_match"
        }
      ]
    }
  }
}
```

**Fields:**
- `mismatch_type`: First component that failed (`status_code`, `headers`, `body`)
- `summary`: Human-readable one-liner for logs
- `details`: Per-component results with `match` boolean
- `differences`: Array of failed comparisons with JSONPath, both values, and which rule failed

Header differences use the same format with header name as path. Body comparison only applies to 2xx JSON responses (see "When body comparison applies" in Per-Endpoint Comparison Rules).

### metadata.json

```json
{
  "tool_version": "0.1.0",
  "timestamp": "2026-01-08T12:00:00Z",
  "seed": 12345,
  "target_a": {
    "name": "production",
    "base_url": "https://api.example.com"
  },
  "target_b": {
    "name": "staging",
    "base_url": "https://staging.api.example.com"
  },
  "comparison_rules_applied": "default"
}
```

---

## Runtime Configuration [SPECIFIED]

The `--config` file supplies target definitions, comparison rules reference, and execution settings.

### Required Fields

```yaml
targets:
  production:
    base_url: https://api.example.com
    headers:
      Authorization: "Bearer ${API_TOKEN}"  # env var substitution
  staging:
    base_url: https://staging.api.example.com
    headers:
      Authorization: "Bearer ${STAGING_TOKEN}"

comparison_rules: ./comparison_rules.json  # path to comparison rules file

rate_limit:
  requests_per_second: 10

secrets:
  redact_fields:
    - "$.password"
    - "$.token"
    - "$.api_key"
```

The `comparison_rules` field references a separate JSON file (see "Per-Endpoint Comparison Rules" below). Keeping rules in a separate file allows reuse across different runtime configs and keeps concerns separated.

### Per-Endpoint Comparison Rules [SPECIFIED]

A JSON file you write to tell api-parity how responses should be compared. You define rules per OpenAPI `operationId`—the tool does not guess which fields are timestamps, UUIDs, or otherwise volatile.

**File structure:**
- `default_rules` — applied to all operations
- `operation_rules` — per-operationId overrides (completely replaces default for any key it defines)
- `field_rules` — JSONPath → comparison rule mapping

**Specifying comparisons:** Use a predefined from `prototype/comparison-rules/comparison_library.json` or write custom CEL. Predefineds expand to CEL at load time.

```json
{"predefined": "numeric_tolerance", "tolerance": 0.01}   // use predefined
{"expr": "a.startsWith(b.substring(0, 5))"}              // custom CEL
```

In CEL expressions, `a` is the value from Target A, `b` is from Target B.

**JSONPath semantics:** Paths with wildcards (`[*]`) expand to match each element—the rule applies to each matched pair in array order.

Example: Given `$.users[*].score` with `{"predefined": "numeric_tolerance", "tolerance": 0.05}`:
- A has `$.users` = `[{"score": 0.95}, {"score": 0.87}]`
- B has `$.users` = `[{"score": 0.96}, {"score": 0.88}]`
- Compares: `$.users[0].score` (0.95 vs 0.96 ✓), `$.users[1].score` (0.87 vs 0.88 ✓)

Array length mismatches are caught by presence parity on missing indices. To compare an array as a whole (e.g., unordered), target the array directly: `"$.users": {"predefined": "unordered_array"}`.

**Default behavior for unspecified fields:** Fields without explicit `field_rules` entries are compared for presence parity only—both responses must have the field, or both must lack it. Values are not compared. This lets fields the user doesn't care about be whatever, as long as they're whatever in both.

**Field presence:** Controls existence requirements (checked before value comparison):

| Presence | Meaning |
|----------|---------|
| `parity` | Both have field, or both lack it (default) |
| `required` | Both must have field |
| `forbidden` | Both must lack field |
| `optional` | Compare if both have field; pass if either lacks it |

**Null handling:** `{"name": null}` is "present"; `{}` is "missing". CEL receives the actual `null` value.

```json
{"presence": "required", "predefined": "uuid_format"}     // must exist, check format
{"presence": "forbidden"}                                  // must not exist
{"presence": "optional", "predefined": "exact_match"}     // compare only if present
```

Default is `parity`. Specifying only `presence` (no `predefined` or `expr`) skips value comparison.

**When body comparison applies:** Body rules only apply to successful (2xx) responses with JSON content. Error responses (4xx, 5xx) are compared by status code only—if both targets return the same status code class, that's parity. Body content of error responses is not compared.

**Header comparison:** Headers use explicit rules, same as body fields. Each header you care about gets a rule; unlisted headers are ignored. Header names are case-insensitive.

```json
"headers": {
  "content-type": {"predefined": "exact_match"},
  "x-request-id": {"predefined": "uuid_format"},
  "cache-control": {"predefined": "exact_match"}
}
```

**Multi-value headers:** When a header has multiple values (e.g., `Set-Cookie`), only the first value is compared. In CEL expressions, `a` and `b` are the first header values from Target A and B respectively. This is a known limitation—if you need to compare all values of a multi-value header, the workaround is to check specific values exist in application logic rather than in api-parity rules.

**Full example:**
```json
{
  "version": "1",
  "default_rules": {
    "status_code": {"predefined": "exact_match"},
    "headers": {
      "content-type": {"predefined": "exact_match"}
    },
    "body": {
      "field_rules": {}
    }
  },
  "operation_rules": {
    "createUser": {
      "body": {
        "field_rules": {
          "$.request_id": {"predefined": "ignore"},
          "$.id": {"presence": "required", "predefined": "uuid_format"},
          "$.created_at": {"predefined": "epoch_seconds_tolerance", "seconds": 5},
          "$.balance": {"predefined": "numeric_tolerance", "tolerance": 0.01},
          "$.legacy_field": {"presence": "forbidden"},
          "$.nickname": {"presence": "optional", "predefined": "exact_match"}
        }
      }
    }
  }
}
```

**Common patterns:**
- Ignore volatile fields: `{"predefined": "ignore"}`
- Tolerate float rounding: `{"predefined": "numeric_tolerance", "tolerance": 0.01}`
- Unordered arrays: `{"predefined": "unordered_array"}` (unique elements only)
- Format-only check: `{"predefined": "uuid_format"}` or `{"predefined": "iso_timestamp_format"}`
- Require field exists: `{"presence": "required"}` (value not compared)
- Forbid deprecated field: `{"presence": "forbidden"}`
- Compare only if present: `{"presence": "optional", "predefined": "exact_match"}`
- Unordered array with volatile nested fields—use custom CEL to compare only stable fields:
  ```json
  "$.users": {"expr": "size(a) == size(b) && a.all(ax, b.exists(bx, ax.id == bx.id && ax.name == bx.name))"}
  ```

See `prototype/comparison-rules/comparison_library.json` for all available predefineds.

### Error Classification [SPECIFIED]

Error responses follow the status code class rule from "When body comparison applies":
- Same status code class (e.g., both 4xx, both 5xx) → parity
- Different status code classes (e.g., 4xx vs 5xx, or 2xx vs 4xx) → mismatch

**Additional behavior for 5xx:**
- Both return 5xx → parity, but not recorded (assumed transient/infrastructure).
- One returns 5xx, other returns different class → mismatch, recorded.

**Configurable overrides:**
```yaml
error_classification:
  ignore_when_both: [500, 502, 503, 504]  # skip test case if both return these
```

This keeps infrastructure noise out of artifacts while catching real behavioral differences.

---

## Stateful Chains [SPECIFIED]

### Link-Based Generation

Chains are auto-discovered from OpenAPI links by Schemathesis via `schema.as_state_machine()`. The state machine automatically creates transitions for each link defined in the spec. Validated: up to 6-step chains, 70+ unique operation sequences generated.

The OpenAPI spec must define links with sufficient detail to express data flow. Sparse link definitions produce shallow chains.

**Example OpenAPI link:**
```yaml
paths:
  /items:
    post:
      operationId: createItem
      responses:
        '201':
          links:
            GetItem:
              operationId: getItem
              parameters:
                id: '$response.body#/id'
```

### Variable Extraction [SPECIFIED]

Schemathesis handles variable extraction automatically via OpenAPI link expressions (e.g., `$response.body#/id`). The state machine maintains "bundles" that store extracted values between steps.

When executing chains for api-parity:
1. Execute step on Target A, extract variables from A's response
2. Execute same operation on Target B, extract variables from B's response
3. Compare responses—if mismatch, stop chain and record
4. If parity, continue; each target's next step uses its own extracted variables

Both targets follow the same chain of operations, but with their own data. If A's POST returns `{"id": "abc"}` and B's returns `{"id": "xyz"}`, A's GET hits `/items/abc` while B's hits `/items/xyz`.

**Mismatch vs error:** If both return 404, that's parity—chain continues. If A returns 404 and B returns 200, that's a mismatch—chain stops.

### Replay Behavior [SPECIFIED]

Replay re-executes the full chain, including CREATE operations. Uses fresh Schemathesis generation (not original values) to avoid uniqueness constraint failures. See DESIGN.md "Replay Regenerates Unique Fields".

**Consequence:** Replay tests the failure pattern, not exact bytes. If a specific value caused the mismatch, replay may not reproduce it exactly—but it tests the same operation with valid data.

---

## OpenAPI Spec as Field Authority [NEEDS SPEC]

Response fields not present in the OpenAPI spec are treated as mismatches (category: `schema_violation`). This forces the spec to accurately describe the API.

**Open Questions:**
1. Which JSON Schema validator to use?
2. How to handle `additionalProperties: true` in the spec?
3. Should unknown fields be warnings or errors?

---

## Implementation Approach [SPECIFIED]

**Generator:** Schemathesis v4.8.0 — OpenAPI-driven fuzzing with stateful link support. Validated 20260108; see DESIGN.md "Schemathesis as Generator (Validated)" for integration details.

**HTTP client:** Any client supporting explicit headers, repeated query parameters, and raw body bytes.

**Artifacts:** Filesystem bundles as described above.

---

## Data Flow Summary [SPECIFIED]

```
OpenAPI Spec ──→ Schemathesis ──→ RequestCase/ChainCase
                                         │
                    ┌────────────────────┴────────────────────┐
                    ▼                                         ▼
              Target A                                   Target B
              (execute)                                  (execute)
                    │                                         │
                    └────────────────────┬────────────────────┘
                                         ▼
                              Compare (per-endpoint rules)
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
                      Match                          Mismatch
                   (log only)                    (write bundle)
```

---

## Testing Infrastructure [SPECIFIED]

Integration tests use a mock FastAPI server that implements a test OpenAPI spec designed to exercise all comparison scenarios.

### Test Fixtures

| File | Purpose |
|------|---------|
| `tests/fixtures/test_api.yaml` | OpenAPI spec with volatile fields, numeric tolerances, unordered arrays, chains |
| `tests/fixtures/comparison_rules.json` | Comparison rules exercising all predefined types |

### Mock Server Variants

The mock server (`tests/integration/mock_server.py`) runs in two variants:

- **Variant A** (`--variant a`): Standard behavior
- **Variant B** (`--variant b`): Controlled differences for testing comparison logic
  - Prices differ by 0.001 (within 0.01 tolerance)
  - Arrays shuffled (tests unordered comparison)
  - User scores differ slightly (tests numeric tolerance)

### Running Tests

```bash
# All tests
pytest

# Integration tests only
pytest tests/integration/

# CEL evaluator tests only
pytest tests/test_cel_evaluator.py
```

### Pytest Fixtures

- `mock_server_a` / `mock_server_b`: Single server on random port (function-scoped)
- `dual_servers`: Both variants for differential testing (function-scoped)
- `test_api_spec` / `comparison_rules_path`: Paths to fixture files (session-scoped)

Each test gets fresh server processes via subprocess isolation.

---

## Sources

Architecture informed by analysis of:
- [Schemathesis GitHub](https://github.com/schemathesis/schemathesis) — Case class, Response class, VCR cassettes
- [Schemathesis PyPI](https://pypi.org/project/schemathesis/) — v4.8.0 features
- [Schemathesis stateful testing issue #864](https://github.com/schemathesis/schemathesis/issues/864) — CLI stateful approach
