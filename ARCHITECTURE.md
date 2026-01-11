# Architecture

High-level architecture of api-parity for AI agents to efficiently understand the project without reading unnecessary code.

**Guiding principles:**
- Token-efficient but not at the expense of clarity
- Include: information that helps agents understand the system's structure and behavior
- Exclude: implementation details only needed when reading a specific code file

**Rule of thumb:** If content helps a new agent attain understanding of the project, it belongs here. If it's detail only relevant when working in a specific file, leave it to be learned from reading that file.

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

### Mode C: List Operations

Discovery command to show all operationIds from an OpenAPI spec along with their response links. Helps users identify operationIds for `--exclude` and `--operation-timeout` flags.

```
api-parity list-operations --spec openapi.yaml
```

### Exit Codes

All modes use standard UNIX exit codes:
- `0`: Successful completion
- Non-zero: Error (invalid arguments, file not found, spec parse error, etc.)

Finding mismatches during explore is expected behavior, not an error. Mismatch count is reported in output, not exit code.

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

## CLI Frontend [SPECIFIED]

The CLI (`api_parity/cli.py`) provides the main entry point with subcommand dispatch.

### Interface

```python
# Entry point via setuptools console_script
api-parity explore --spec openapi.yaml --config config.yaml --target-a prod --target-b staging --out ./artifacts
api-parity replay --config config.yaml --target-a prod --target-b staging --in ./artifacts/mismatches --out ./replay
api-parity list-operations --spec openapi.yaml
```

### Component Wiring

The `explore` subcommand orchestrates:

1. Load runtime config (`config_loader.load_runtime_config`)
2. Load comparison rules (`config_loader.load_comparison_rules`)
3. Validate targets (`config_loader.validate_targets`)
4. Initialize Case Generator (`CaseGenerator(spec_path, exclude_operations)`)
5. Initialize Executor (`Executor(target_a, target_b, timeout, operation_timeouts)`)
6. Initialize CEL Evaluator (`CELEvaluator()` with explicit `close()` in finally block)
7. Initialize Comparator (`Comparator(cel_evaluator, comparison_library)`)
8. Initialize Artifact Writer (`ArtifactWriter(output_dir, secrets_config)`)
9. Generate and execute cases, compare responses, write mismatches

### Error Handling

- Config/spec load errors: abort run with non-zero exit code
- Per-request errors (timeout, connection): skip test case, increment error count, continue
- CEL evaluation errors: record as mismatch with `rule: "error: ..."`, continue
- CEL subprocess crash: propagate exception, abort run

### Progress Reporting

Prints per-case progress during run, summary stats at end:

```
[1] createWidget: POST /widgets MATCH
[2] getWidget: GET /widgets/{id} MISMATCH: body differs at $.price

============================================================
Total cases: 2
  Matches:    1
  Mismatches: 1
  Errors:     0
Summary written to: ./artifacts/summary.json
```

---

## Case Generator [SPECIFIED]

The Case Generator (`api_parity/case_generator.py`) wraps Schemathesis to yield `RequestCase` objects from an OpenAPI specification.

### Interface

```python
class CaseGenerator:
    def __init__(self, spec_path: Path, exclude_operations: list[str] | None = None): ...
    def get_operations(self) -> list[dict[str, Any]]: ...
    def generate(self, max_cases: int | None = None, seed: int | None = None) -> Iterator[RequestCase]: ...
```

### Usage

```python
generator = CaseGenerator(Path("api.yaml"), exclude_operations=["debugEndpoint"])
for case in generator.generate(max_cases=100, seed=42):
    # case is a RequestCase ready for execution
    print(f"{case.method} {case.rendered_path}")
```

### Case Distribution

When `max_cases` is specified, cases are distributed across operations:
- If 100 max cases and 10 operations, each operation gets ~10 cases
- Generation stops when total reaches max_cases

### Seed Behavior

The `seed` parameter enables reproducible generation via Hypothesis `derandomize=True` mode. Same seed produces same test case sequence.

### Current Scope

Stateless generation only (single requests). Stateful chain generation via OpenAPI links is planned but not yet implemented.

---

## Executor [SPECIFIED]

The Executor (`api_parity/executor.py`) sends requests to both targets and captures responses using httpx.

### Interface

```python
class Executor:
    def __init__(
        self,
        target_a: TargetConfig,
        target_b: TargetConfig,
        default_timeout: float = 30.0,
        operation_timeouts: dict[str, float] | None = None,
    ): ...

    def execute(self, request: RequestCase) -> tuple[ResponseCase, ResponseCase]: ...
    def close(self) -> None: ...
```

Supports context manager protocol:

```python
with Executor(target_a, target_b) as executor:
    resp_a, resp_b = executor.execute(request_case)
```

### Execution Order

Requests execute serially: Target A first, then Target B. No concurrent requests (see DESIGN.md "Serialized Execution Only").

### Timeout Configuration

- `default_timeout`: applies to all operations (default 30s)
- `operation_timeouts`: per-operationId overrides (e.g., `{"slowEndpoint": 120.0}`)

### Error Handling

Connection errors, timeouts, and other request failures raise `RequestError`. The CLI catches these and records as errors (skipped test cases), not mismatches.

### Current Scope

Stateless execution only (`execute()`). Chain execution (`execute_chain()`) for stateful testing is planned but not yet implemented. Rate limiting is not implemented in v0.

---

## Artifact Writer [SPECIFIED]

The Artifact Writer (`api_parity/artifact_writer.py`) writes mismatch bundles to disk for replay and analysis.

### Interface

```python
class ArtifactWriter:
    def __init__(self, output_dir: Path, secrets_config: SecretsConfig | None = None): ...

    def write_mismatch(
        self,
        case: RequestCase,
        response_a: ResponseCase,
        response_b: ResponseCase,
        diff: ComparisonResult,
        target_a_info: TargetInfo,
        target_b_info: TargetInfo,
        seed: int | None = None,
    ) -> Path: ...

    def write_summary(self, stats: RunStats, seed: int | None = None) -> None: ...
```

### Bundle Naming

Bundles are named `<timestamp>__<operation_id>__<case_id>`:
- Timestamp: `YYYYMMDDTHHMMSS` in UTC
- Operation ID: sanitized (non-alphanumeric → underscore, max 50 chars)
- Case ID: first 8 characters of UUID

Example: `20260111T143052__createWidget__abc12345/`

### Atomic Writes

All files are written atomically: write to `.tmp` file, then rename. This prevents partial writes if the process is interrupted.

### Secret Redaction

If `secrets_config.redact_fields` is configured, JSONPath expressions are applied to redact sensitive values before writing. Redacted values are replaced with `"[REDACTED]"`.

```yaml
secrets:
  redact_fields:
    - "$.password"
    - "$.api_key"
```

Uses `jsonpath-ng` for JSONPath evaluation.

---

## Runtime Config Loading [SPECIFIED]

The Config Loader (`api_parity/config_loader.py`) handles loading YAML runtime configuration and JSON comparison rules.

### Interface

```python
def load_runtime_config(config_path: Path) -> RuntimeConfig: ...
def load_comparison_rules(rules_path: Path) -> ComparisonRulesFile: ...
def load_comparison_library(library_path: Path | None = None) -> ComparisonLibrary: ...
def get_operation_rules(rules_file: ComparisonRulesFile, operation_id: str) -> OperationRules: ...
def resolve_comparison_rules_path(config_path: Path, rules_ref: str) -> Path: ...
def validate_targets(config: RuntimeConfig, target_a_name: str, target_b_name: str) -> tuple[TargetConfig, TargetConfig]: ...
```

### Environment Variable Substitution

`${ENV_VAR}` patterns in YAML values are substituted before parsing:

```yaml
targets:
  production:
    base_url: https://api.example.com
    headers:
      Authorization: "Bearer ${API_TOKEN}"  # Replaced with os.environ["API_TOKEN"]
```

Implementation: regex replacement (`\$\{([^}]+)\}`) applied recursively to all string values after YAML parsing.

### Relative Path Resolution

The `comparison_rules` field is resolved relative to the config file's directory:

```yaml
# In /path/to/config.yaml
comparison_rules: ./rules/comparison_rules.json  # Resolves to /path/to/rules/comparison_rules.json
```

### Error Handling

All loading functions raise `ConfigError` for validation failures (missing files, invalid YAML/JSON, schema validation errors). The CLI catches these and exits with non-zero code.

---

## Comparator Component [SPECIFIED]

The Comparator (`api_parity/comparator.py`) compares two `ResponseCase` objects according to `OperationRules` and produces a `ComparisonResult`. It delegates CEL evaluation to the CEL Evaluator.

### Comparison Order

Comparison proceeds in order with short-circuit on first mismatch:

1. **Status Code** → 2. **Headers** → 3. **Body**

The `mismatch_type` field in `ComparisonResult` indicates which phase failed first. If status codes mismatch, headers and body are not compared.

### Error Handling

Rule errors (invalid JSONPath, unknown predefined, CEL evaluation failure) are recorded as mismatches with `rule: "error: ..."`, not raised as exceptions. This keeps one broken rule from stopping the run.

Infrastructure failures (`CELSubprocessError` from subprocess crash) propagate as exceptions.

### Interface

```python
class Comparator:
    def __init__(self, cel_evaluator: CELEvaluator, comparison_library: ComparisonLibrary): ...
    def compare(self, response_a: ResponseCase, response_b: ResponseCase, rules: OperationRules) -> ComparisonResult: ...
```

Caller owns CEL Evaluator lifecycle:

```python
with CELEvaluator() as cel:
    comparator = Comparator(cel, library)
    result = comparator.compare(response_a, response_b, rules)
```

See "Per-Endpoint Comparison Rules" for rule semantics (presence modes, predefined expansion, header handling).

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

### Lifecycle

- Started once at CLI startup (before explore/replay begins)
- Kept alive for duration of run
- Terminated on CLI exit (close stdin, wait for process)
- Restarted automatically if subprocess crashes (EOF detected on stdout)

---

## Internal Data Models [SPECIFIED]

Pydantic v2 models in `api_parity/models.py`. Key models:

| Model | Purpose |
|-------|---------|
| `RequestCase` | HTTP request (includes `path_template`, `path_parameters`, `rendered_path` for debugging) |
| `ResponseCase` | HTTP response captured from target |
| `ChainCase` / `ChainStep` | Chain template (no execution data) |
| `ChainExecution` / `ChainStepExecution` | Execution trace per target |
| `ComparisonResult` / `FieldDifference` | Comparison output |
| `RuntimeConfig` | Config file structure |
| `ComparisonRulesFile` / `OperationRules` | Comparison rules structure |

**Key design notes:**
- Header/query values are arrays (supports repeated params)
- `body` and `body_base64` are mutually exclusive
- Chain templates separate from execution traces (enables replay with fresh data)
- Each target maintains its own extracted variables during chain execution
- **Why three path fields?** `path_template` + `path_parameters` preserve which values caused failures; `rendered_path` gives the actual URL without computation

See `api_parity/models.py` for complete field definitions.

---

## Mismatch Report Bundle [SPECIFIED]

On mismatch, emit a bundle for replay and analysis. Differs from Schemathesis VCR cassettes—stores parallel execution on two targets.

```
mismatches/<timestamp>__<operation_id>__<case_id>/
  case.json        # RequestCase or ChainCase
  target_a.json    # StatelessExecution or ChainExecution
  target_b.json    # StatelessExecution or ChainExecution
  diff.json        # ComparisonResult
  metadata.json    # MismatchMetadata (tool version, targets, seed)
```

**diff.json structure:** `mismatch_type` (status_code|headers|body), `summary` (human-readable), `details` with per-component `match` boolean and `differences` array. Each difference has `path`, `target_a`, `target_b`, `rule`.

Models in `api_parity/models.py`.

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

JSON file defining how responses are compared. Model: `ComparisonRulesFile` in `api_parity/models.py`.

**Structure:**
- `default_rules` — applied to all operations
- `operation_rules` — per-operationId overrides (completely replaces default for keys it defines)
- Body rules nested under `body.field_rules` (see example config)

**Rule types:** Use predefined (expands to CEL at load time) or custom CEL expression. In CEL, `a` = Target A value, `b` = Target B value.

**Presence modes:** `parity` (default), `required`, `forbidden`, `optional` — checked before value comparison. Null values (`{"name": null}`) are present; missing fields (`{}`) are absent.

```json
{"presence": "required", "predefined": "uuid_format"}
{"presence": "optional", "predefined": "exact_match"}
{"presence": "forbidden"}
```
*(Comments not allowed in JSON; see example config for annotated version)*

**Key behaviors:**
- Unspecified fields: presence parity only (both have or both lack), values not compared
- Body rules: only apply to 2xx JSON responses
- Error responses: same status code class = parity, body not compared
- Headers: case-insensitive, multi-value uses first value only
- Wildcards (`[*]`): expand and compare by index

**References:**
- Predefineds: `prototype/comparison-rules/comparison_library.json`
- Example config: `tests/fixtures/comparison_rules.json`

### Error Classification

- Same status code class (both 4xx, both 5xx) → parity
- Different classes → mismatch

---

## Stateful Chains [SPECIFIED]

Chains are auto-discovered from OpenAPI links via Schemathesis `schema.as_state_machine()`. Validated: 6-step chains, 70+ unique sequences.

**Execution semantics:**
- Each target maintains its own extracted variables (A's POST returns `id: abc`, B's returns `id: xyz` → A's GET hits `/items/abc`, B's hits `/items/xyz`)
- Mismatch stops chain and records; same error on both = parity, chain continues
- Schemathesis handles variable extraction via OpenAPI link expressions

**Replay:** Re-executes full chain with fresh generated values (not original). Tests failure pattern, not exact bytes. See DESIGN.md "Replay Regenerates Unique Fields".

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

Mock FastAPI server (`tests/integration/mock_server.py`) with two variants for differential testing:
- **Variant A**: Standard behavior
- **Variant B**: Controlled differences (prices within tolerance, shuffled arrays, etc.)

**Key files:**
- `tests/fixtures/test_api.yaml` — OpenAPI spec exercising comparison scenarios
- `tests/fixtures/comparison_rules.json` — Rules for all predefined types
- `tests/conftest.py` — Pytest fixtures (`dual_servers`, `mock_server_a`, etc.)

---

## Sources

Architecture informed by analysis of:
- [Schemathesis GitHub](https://github.com/schemathesis/schemathesis) — Case class, Response class, VCR cassettes
- [Schemathesis PyPI](https://pypi.org/project/schemathesis/) — v4.8.0 features
- [Schemathesis stateful testing issue #864](https://github.com/schemathesis/schemathesis/issues/864) — CLI stateful approach
