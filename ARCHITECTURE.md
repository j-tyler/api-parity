# Architecture

High-level architecture for AI agents to efficiently understand api-parity without reading implementation code.

**Purpose:** Include information that helps agents understand the system's structure and behavior. Exclude implementation details only needed when reading a specific file—those are learned by reading that file.

---

## Summary

api-parity compares two deployments of a "theoretically identical" HTTP API to discover differences. Uses an OpenAPI spec to generate requests (including stateful chains via links), executes them against two base URLs, compares responses under configurable rules, and emits mismatch bundles for analysis and replay.

## Goals

1. **Stateful request generation** — Sequences like Create → Get → Update → Delete via OpenAPI links
2. **Dual-host comparison** — Execute same request against both targets, compare responses
3. **Actionable artifacts** — Mismatch bundles with request, responses, diff, and context for replay

## Non-Goals

- Universal response equivalence without configuration
- Installing software on targets
- Mocking downstream dependencies
- Eventually consistent APIs

---

## Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Frontend                          │
│  Parse args, load config/spec, orchestrate components        │
└─────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┴─────────────────┐
            ▼                                   ▼
┌───────────────────────┐           ┌───────────────────────┐
│    Case Generator     │           │    Bundle Loader      │
│  (explore mode)       │           │  (replay mode)        │
└───────────────────────┘           └───────────────────────┘
            │                                   │
            └─────────────────┬─────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                         Executor                             │
│  Send request to Target A, then Target B                     │
│  For chains: each target uses its own extracted variables    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        Comparator                            │
│  Apply comparison rules, produce structured diff             │
│  Delegates CEL evaluation to CEL Evaluator                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
│               CEL Evaluator (Go subprocess)                  │
│  Evaluates CEL expressions via stdin/stdout NDJSON           │
└ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Artifact Writer                          │
│  Write mismatch bundles, redact secrets                      │
└─────────────────────────────────────────────────────────────┘
```

---

## CLI Commands

| Command | Purpose |
|---------|---------|
| `explore` | Generate tests from spec, find mismatches |
| `replay` | Re-execute saved bundles, classify results |
| `list-operations` | Show operationIds and links from spec |
| `graph-chains` | Visualize link graph or generated chains |
| `lint-spec` | Check spec for api-parity issues |

**Exit codes:** `0` = success, non-zero = error. Finding mismatches is expected behavior, not an error.

### Replay Classifications

When replaying saved bundles, each is classified:
- **FIXED** — Previously mismatched, now matches (issue resolved)
- **STILL MISMATCH** — Same failure pattern persists (same mismatch_type and paths)
- **DIFFERENT MISMATCH** — Fails differently than before (comparison rules changed, or a different bug surfaced)

### Lint-Spec Checks

`lint-spec` analyzes an OpenAPI spec for api-parity-specific issues:
- **Link connectivity** — Identifies isolated operations, chain terminators, entry points, invalid link targets
- **Explicit links warning** — Warns if spec has no explicit OpenAPI links (api-parity disables inference)
- **Chain depth coverage** — Identifies operations only reachable at depth 3+ (less likely to be explored)
- **Link expression coverage** — Categorizes expressions (`$response.body#/...`, `$response.header.*`, etc.)
- **Response schema coverage** — Reports operations missing 2xx response schemas
- **Duplicate link names** — Detects YAML duplicate keys that get silently overwritten

---

## Component Interfaces

### Case Generator

`api_parity/case_generator.py` — Wraps Schemathesis to generate test cases.

```python
class CaseGenerator:
    def __init__(self, spec_path: Path, exclude_operations: list[str] | None = None): ...
    def get_operations(self) -> list[dict[str, Any]]: ...
    def get_all_operation_ids(self) -> set[str]: ...
    def get_linked_operation_ids(self) -> set[str]: ...  # Ops that participate in links
    def get_link_fields(self) -> LinkFields: ...
    def generate(self, max_cases: int | None, seed: int | None) -> Iterator[RequestCase]: ...
    def generate_chains(self, max_chains: int | None, max_steps: int, seed: int | None) -> list[ChainCase]: ...
```

`get_linked_operation_ids()` returns operations that are source or target of at least one OpenAPI link. These are the operations Schemathesis can reach via its state machine. Operations not in this set are "orphans" — invisible to chain generation and only testable via `--ensure-coverage`. Used by the CLI for coverage-guided seed walking (see below).

Link field references are parsed from the OpenAPI spec at init. `LinkFields` contains:
- `body_pointers`: JSONPointer paths for body fields
- `headers`: `HeaderRef` objects for response headers

### Executor

`api_parity/executor.py` — Sends requests and captures responses.

```python
class Executor:
    def __init__(
        self,
        target_a: TargetConfig,
        target_b: TargetConfig,
        default_timeout: float = 30.0,
        operation_timeouts: dict[str, float] | None = None,
        link_fields: LinkFields | None = None,
        requests_per_second: float | None = None,
    ): ...
    def execute(self, request: RequestCase) -> tuple[ResponseCase, ResponseCase]: ...
    def execute_chain(self, chain: ChainCase, on_step: Callable | None) -> tuple[ChainExecution, ChainExecution]: ...
    # on_step(resp_a, resp_b) -> bool: return False to stop chain, True to continue
    def close(self) -> None: ...
```

Requests execute serially (A first, then B)—no concurrency, which would introduce non-determinism. For chains, each target maintains its own extracted variables (if A's POST returns `id: "abc"` and B's returns `id: "xyz"`, subsequent steps use respective IDs).

**Error handling:**
- Connection errors/timeouts: skip test case, increment error count, continue run
- CEL evaluation errors: record as mismatch with `rule: "error: ..."`, continue run
- CEL subprocess crash: propagate exception, abort run

### Comparator

`api_parity/comparator.py` — Compares responses under rules.

```python
class Comparator:
    def __init__(
        self,
        cel_evaluator: CELEvaluator,
        comparison_library: ComparisonLibrary,
        schema_validator: SchemaValidator | None = None,
    ): ...
    def compare(
        self,
        response_a: ResponseCase,
        response_b: ResponseCase,
        rules: OperationRules,
        operation_id: str | None = None,
    ) -> ComparisonResult: ...
```

**Comparison order:** Status code → Headers → Body. Short-circuits on first mismatch.

**Error handling:** Rule errors (invalid JSONPath, CEL failure) record as mismatch with `rule: "error: ..."`. Infrastructure failures (subprocess crash) propagate as exceptions.

### CEL Evaluator

`api_parity/cel_evaluator.py` — Go subprocess for CEL expression evaluation. Uses cel-go because Python CEL libraries are untrusted dependencies; uses stdin/stdout pipes because single-client IPC doesn't need sockets.

```python
class CELEvaluator:
    MAX_RESTARTS = 3
    STARTUP_TIMEOUT = 5.0
    EVALUATION_TIMEOUT = 10.0

    def __init__(self, binary_path: str | Path | None = None): ...
    def evaluate(self, expression: str, data: dict[str, Any]) -> bool: ...
    def close(self) -> None: ...
```

Build the binary: `go build -o cel-evaluator ./cmd/cel-evaluator`

**Protocol (NDJSON over stdin/stdout):**

Request:
```json
{"id": "req-1", "expression": "a == b", "data": {"a": 1, "b": 1}}
```

Response:
```json
{"id": "req-1", "ok": true, "result": true}
{"id": "req-2", "ok": false, "error": "undeclared reference to 'x'"}
```

Each message is one line. The Go evaluator has a 5-second timeout per expression.

### Bundle Loader

`api_parity/bundle_loader.py` — Loads mismatch bundles for replay.

```python
def discover_bundles(directory: Path) -> list[Path]: ...
def load_bundle(bundle_path: Path) -> LoadedBundle: ...
def detect_bundle_type(bundle_path: Path) -> BundleType: ...
def extract_link_fields_from_chain(chain: ChainCase) -> LinkFields: ...
```

### Artifact Writer

`api_parity/artifact_writer.py` — Writes mismatch bundles to disk.

```python
class ArtifactWriter:
    def __init__(self, output_dir: Path, secrets_config: SecretsConfig | None = None): ...
    def write_mismatch(self, case, response_a, response_b, diff, ...) -> Path: ...
    def write_chain_mismatch(self, chain, execution_a, execution_b, ...) -> Path: ...
    def write_summary(self, stats: RunStats, seed: int | None = None) -> None: ...
```

Bundles named `<timestamp>__<operation_id>__<case_id>`. All writes are atomic (write to `.tmp`, then rename) to prevent partial writes if interrupted.

### Config Loader

`api_parity/config_loader.py` — Loads and validates configuration.

```python
def load_runtime_config(config_path: Path) -> RuntimeConfig: ...
def load_comparison_rules(rules_path: Path) -> ComparisonRulesFile: ...
def load_comparison_library(library_path: Path | None = None) -> ComparisonLibrary: ...
def get_operation_rules(rules_file: ComparisonRulesFile, operation_id: str) -> OperationRules: ...
def validate_targets(config, target_a_name, target_b_name) -> tuple[TargetConfig, TargetConfig]: ...
def validate_comparison_rules(rules, library, spec_operation_ids) -> ValidationResult: ...
```

Environment variables (`${VAR}`) substituted in YAML values. Paths resolved relative to config file.

**Full config example:**

```yaml
targets:
  production:
    base_url: https://api.example.com
    headers:
      Authorization: "Bearer ${PROD_TOKEN}"
      X-Custom-Header: "value"
    # TLS options (all optional)
    cert: /path/to/client.crt
    key: /path/to/client.key
    key_password: ${KEY_PASSWORD}
    ca_bundle: /path/to/ca-bundle.pem
    verify_ssl: true
    ciphers: "ECDHE+AESGCM"

  staging:
    base_url: https://staging.example.com
    headers:
      Authorization: "Bearer ${STAGING_TOKEN}"

comparison_rules: ./comparison_rules.json

rate_limit:
  requests_per_second: 10  # 2 requests per test case (one per target), so 10 req/s ≈ 5 cases/s

secrets:
  redact_fields:
    - "$.password"
    - "$.api_key"
    - "$.users[*].ssn"
```

### Schema Validator

`api_parity/schema_validator.py` — Validates responses against OpenAPI schemas.

```python
class SchemaValidator:
    def __init__(self, spec_path: Path): ...
    def validate_response(self, body, operation_id, status_code) -> ValidationResult: ...
```

Uses `jsonschema` library. Extra fields with `additionalProperties: false` are violations.

---

## Data Models

Pydantic v2 models in `api_parity/models.py`.

| Model | Purpose |
|-------|---------|
| `RequestCase` | HTTP request with path template, parameters, rendered path |
| `ResponseCase` | Captured response (status, headers, body, elapsed) |
| `ChainCase` / `ChainStep` | Chain template without execution data |
| `ChainExecution` / `ChainStepExecution` | Execution trace per target |
| `ComparisonResult` / `FieldDifference` | Comparison output |
| `RuntimeConfig` / `TargetConfig` | Config file structure |
| `ComparisonRulesFile` / `OperationRules` | Comparison rules |

### RequestCase Fields

```python
class RequestCase:
    case_id: str                    # Unique identifier
    operation_id: str               # From OpenAPI spec
    method: str                     # GET, POST, etc.
    path: str                       # Path template: /widgets/{id}
    rendered_path: str              # Computed: /widgets/abc123
    path_parameters: dict[str, Any]
    query: dict[str, list[Any]]     # Query params (list values!)
    headers: dict[str, list[str]]   # Headers (list values!)
    body: Any | None                # JSON body
    body_base64: str | None         # Binary body (mutually exclusive)
    media_type: str | None          # Content-Type
```

### ResponseCase Fields

```python
class ResponseCase:
    status_code: int
    headers: dict[str, list[str]]   # List values for multi-value headers
    body: Any | None                # Parsed JSON or None
    body_base64: str | None         # Binary body as base64
    elapsed_seconds: float
    error: str | None               # Connection/timeout error
```

### ChainCase / ChainExecution

```python
class ChainCase:
    chain_id: str
    steps: list[ChainStep]          # Template steps

class ChainStep:
    request: RequestCase
    link_source: str | None         # e.g., "$response.body#/id"

class ChainExecution:
    chain_id: str
    steps: list[ChainStepExecution]
    stopped_at_step: int | None     # If chain stopped early

class ChainStepExecution:
    request: RequestCase            # Actual request sent
    response: ResponseCase
    extracted_values: dict          # Values extracted for next step
```

**Key design notes:**
- Header/query values are lists (supports repeated params)
- `body` and `body_base64` are mutually exclusive
- Chain templates separate from execution traces (enables replay)

---

## Mismatch Bundle Structure

```
mismatches/<timestamp>__<operation_id>__<case_id>/
  case.json        # RequestCase or ChainCase
  target_a.json    # Response/execution from Target A
  target_b.json    # Response/execution from Target B
  diff.json        # ComparisonResult
  metadata.json    # Run context (version, targets, seed)
```

**diff.json:** `mismatch_type` (status_code|headers|body|schema_violation), `summary`, `details` with per-component differences.

---

## Comparison Rules

JSON file referenced by runtime config.

```json
{
  "version": "1",
  "default_rules": { ... },
  "operation_rules": {
    "<operationId>": { ... }
  }
}
```

**Override semantics:** Operation rules completely replace defaults for any key they define (no deep merging).

**Rule types:**
- Predefined: `{"predefined": "uuid_format"}` — expands to CEL at load time
- Custom CEL: `{"expr": "a == b"}` — variables `a` (Target A) and `b` (Target B)

**Presence modes:** `required` (default), `optional`, `forbidden`, `parity`

**Body rules:** Only apply to 2xx JSON responses. Use `binary_rule` for non-JSON.

---

## Stateful Chains

Chains auto-discovered from explicit OpenAPI links via `schema.as_state_machine()`. Link inference algorithms are disabled—only explicit links are used, because inferred relationships may not reflect actual API contracts and users should be able to see exactly which chains will be tested by reading their spec.

**Execution semantics:**
- Each target maintains its own extracted variables
- If A's POST returns `id: "abc"` and B's returns `id: "xyz"`, subsequent steps use respective IDs
- Chain stops at first mismatch between targets (comparing subsequent steps after divergence produces noise)

### Link Creation vs Free Transitions

Understanding Schemathesis's state machine behavior is important for interpreting chain output:

**Link Creation (Inference)** — Schemathesis has built-in algorithms that automatically create links between operations even when not explicitly defined in the spec:
- Parameter name matching: `POST /users` returns `{id: 123}` and `GET /users/{userId}` exists → Schemathesis infers a link
- Location headers: `POST /resources` returns `Location: /resources/abc` → Schemathesis infers a link to `GET /resources/{id}`

api-parity **disables these inference algorithms** (see `_create_explicit_links_only_config()` in case_generator.py) because it only tests explicit OpenAPI links defined in the spec.

**Free Transitions** — Even with inference disabled, Schemathesis's state machine can still transition between any operations. The state machine explores the API by calling various operations regardless of links. Links only provide **variable passing** — they tell Schemathesis "when you call operation B after A, use values from A's response to fill B's parameters."

Without a link, Schemathesis can still call B after A, but generates random parameter values instead of extracting them from A's response.

**"Unknown Link" in Output** — When chains show "via unknown link (not in spec)", this is normal. It means Schemathesis transitioned between operations without using an explicit link. The state machine made a free transition, not a linked one.

**Implications for Coverage:**
1. Running more chains improves link coverage (probabilistic exploration)
2. Operations reachable only via specific links may be undertested
3. Specs with incomplete link definitions will have gaps in stateful testing

See DESIGN.md "Explicit Links Only for Chains" for the rationale behind disabling inference.

### Coverage-Guided Seed Walking

When `--seed` is provided, the CLI walks seeds (seed, seed+1, seed+2, ...) to accumulate chains. **Stopping is coverage-guided**: seed walking continues until the coverage target is met, then stops.

The coverage target is defined by two parameters:
- **`--min-hits-per-op N`** (default: 1) — each linked operation must appear in at least N unique (deduplicated) chains.
- **`--min-coverage P`** (default: 100) — P% of linked operations must meet the min-hits-per-op threshold.

Common configurations:

| Configuration | Meaning | Use case |
|---|---|---|
| `--min-hits-per-op 1 --min-coverage 100` | Every linked op in at least 1 chain (default) | Quick coverage check |
| `--min-hits-per-op 5 --min-coverage 100` | Every linked op in at least 5 unique chains | Deeper testing with diverse inputs |
| `--min-hits-per-op 5 --min-coverage 80` | 80% of linked ops at 5+ chains | Tolerates hard-to-reach operations |

The stopping conditions are checked in priority order:
1. **Coverage met** — min_coverage% of linked operations have min_hits_per_op hits (primary goal)
2. **Max chains** — accumulated `--max-chains` unique chain sequences (secondary limit, if set)
3. **Max seeds** — tried 100 seeds without meeting the above (hard safety limit)

When `--min-hits-per-op > 1` and `--max-chains` is not explicitly set, the chain count limit is removed (unlimited), so seed walking is driven entirely by the coverage depth target.

A "hit" counts the number of unique (deduplicated) chains containing an operation, not the number of times the operation appears within a single chain. Chain deduplication uses the operation-ID signature (the ordered sequence of operation IDs in the chain).

Coverage tracking uses `CaseGenerator.get_linked_operation_ids()` to know the target set. Operations are classified as:
- **Linked** — participates in at least one OpenAPI link (source or target). These get state machine rules and are reachable via chains.
- **Orphan** — no link involvement at all. Invisible to the state machine. Only testable via `--ensure-coverage`, which runs single-request tests with random parameters.

Progress is printed during seed walking:
```
# Default (min_hits_per_op=1):
  Seed 42: 150 chains, 12/14 linked operations covered
  Seed 43: +47 new chains, 14/14 linked operations covered
  Full linked coverage in 2 seed(s) (197 unique chains)

# With --min-hits-per-op 5:
  Seed 42: 150 chains, 8/14 ops at 5+ hits
  Seed 43: +47 new chains, 14/14 ops at 5+ hits
  Coverage target met in 2 seed(s) (197 unique chains, all at 5+ hits)
```

See TODO.md "Chain Coverage: Behavior and Spec Design Guidance" for empirical data on how many seeds different spec sizes require.

---

## Data Flow

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
                              Compare (rules + CEL)
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
                      Match                          Mismatch
                   (log only)                    (write bundle)
```

---

## Testing

Mock FastAPI server (`tests/integration/mock_server.py`) with two variants (A = standard, B = controlled differences).

**Key fixtures:**
- `tests/fixtures/test_api.yaml` — OpenAPI spec
- `tests/fixtures/comparison_rules.json` — Comparison rules
- `tests/conftest.py` — Pytest fixtures

**Test layers:**
- Unit tests: `tests/test_*.py` (`@pytest.mark.unit`)
- Integration tests: `tests/integration/` (`@pytest.mark.integration`)

**Always run tests with:** `python -m pytest tests/ -x -q --tb=short`
