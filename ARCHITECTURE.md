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
- **[NEEDS IMPL]** — Specified and designed, awaiting implementation
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

## Execution Modes [IMPLEMENTED]

All three execution modes are fully implemented and tested.

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
  [--max-cases 1000] \
  [--stateful] \
  [--max-chains 20] \
  [--max-steps 6]
```

**Stateful mode:** When `--stateful` is passed, the tool generates multi-step chains using OpenAPI links instead of single requests. `--max-chains` controls how many chains to generate (default 20), and `--max-steps` controls maximum steps per chain (default 6).

### Mode B: Replay

Load previously saved mismatch bundles and re-execute them against both targets. Classify outcomes for regression tracking.

```
api-parity replay \
  --config runtime.yaml \
  --target-a <name> \
  --target-b <name> \
  --in ./artifacts \
  --out ./artifacts/replay
```

**Replay classifications:**
- `FIXED` — Previously mismatched, now matches (issue resolved)
- `STILL MISMATCH` — Same failure pattern persists (same mismatch_type and paths)
- `DIFFERENT MISMATCH` — Fails differently than before (rules changed or new issue)

**Output:** Console summary during run, plus `replay_summary.json` with counts and bundle lists for programmatic access.

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

─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                     Replay Mode Path
─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

┌─────────────────────────────────────────────────────────────────┐
│                      Bundle Loader                               │
│  - Discover mismatch bundles from previous runs                  │
│  - Load case data, metadata, original diff                       │
│  - Extract link_fields for chain replay                          │
└─────────────────────────────────────────────────────────────────┘
            │
            └──────────────────► (feeds into Executor above)
```

Note: CEL Evaluator shown with dashed border to indicate it's a subprocess, not in-process Python. Replay mode uses Bundle Loader instead of Case Generator.

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

Prints per-case progress during run, summary stats at end.

**Stateless mode:**
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

**Stateful mode:**
```
[Chain 1] createWidget → getWidget → updateWidget
  MATCH (all steps)
[Chain 2] createWidget → deleteWidget → getWidget
  MISMATCH at step 2 (getWidget): status_code differs
  Bundle: ./artifacts/mismatches/20260112T...

============================================================
Total chains: 2
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
    def generate_chains(self, max_chains: int | None = None, max_steps: int = 6, seed: int | None = None) -> list[ChainCase]: ...
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

### Chain Generation

The `generate_chains()` method uses Schemathesis's state machine to discover multi-step sequences from OpenAPI links. Chains are captured without making HTTP calls—the Executor handles actual execution.

**Dynamic field extraction:** Link field names are dynamically parsed from the OpenAPI spec at initialization. The CaseGenerator extracts all field paths referenced by link expressions (e.g., `$response.body#/resource_uuid`) and uses them for synthetic response generation during chain discovery. This enables chain generation with any field names, not just common ones like `id`.

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
        link_fields: set[str] | None = None,
        requests_per_second: float | None = None,
    ): ...

    def execute(self, request: RequestCase) -> tuple[ResponseCase, ResponseCase]: ...
    def execute_chain(self, chain: ChainCase, on_step: Callable[[ResponseCase, ResponseCase], bool] | None = None) -> tuple[ChainExecution, ChainExecution]: ...
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

### Chain Execution

The `execute_chain()` method executes multi-step chains against both targets. Each target maintains its own extracted variables—if Target A's POST returns `id: "abc"` and Target B's returns `id: "xyz"`, subsequent steps use the respective IDs. The optional `on_step` callback allows stopping at first mismatch (returns `False` to stop, `True` to continue).

**Dynamic field extraction:** The Executor receives a `link_fields` parameter (parsed from the OpenAPI spec by CaseGenerator) specifying which fields to extract from responses. This enables chain execution with any field names referenced by OpenAPI links, including nested paths like `data/nested_id`.

### Rate Limiting

The Executor supports rate limiting via `requests_per_second`. When configured:
- Enforces minimum interval between requests (`1 / requests_per_second` seconds)
- Thread-safe (uses a lock for concurrent access)
- First request never waits (no artificial startup delay)
- If `None`, no rate limiting is applied

Rate limit applies globally across all requests to both targets. Configure via `rate_limit.requests_per_second` in the runtime config file.

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

    def write_chain_mismatch(
        self,
        chain: ChainCase,
        execution_a: ChainExecution,
        execution_b: ChainExecution,
        step_diffs: list[ComparisonResult],
        mismatch_step: int,
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
# Loading functions
def load_runtime_config(config_path: Path) -> RuntimeConfig: ...
def load_comparison_rules(rules_path: Path) -> ComparisonRulesFile: ...
def load_comparison_library(library_path: Path | None = None) -> ComparisonLibrary: ...
def get_operation_rules(rules_file: ComparisonRulesFile, operation_id: str) -> OperationRules: ...
def resolve_comparison_rules_path(config_path: Path, rules_ref: str) -> Path: ...
def validate_targets(config: RuntimeConfig, target_a_name: str, target_b_name: str) -> tuple[TargetConfig, TargetConfig]: ...

# Cross-validation functions (return ValidationResult with warnings/errors)
def validate_comparison_rules(
    rules: ComparisonRulesFile,
    library: ComparisonLibrary,
    spec_operation_ids: set[str],
) -> ValidationResult: ...  # Validates operationIds exist, predefined names valid, required params present

def validate_cli_operation_ids(
    exclude_ops: list[str],
    operation_timeouts: dict[str, float],
    spec_operation_ids: set[str],
) -> ValidationResult: ...  # Validates --exclude and --operation-timeout operationIds exist
```

`ValidationResult` has `.is_valid` (True if no errors), `.warnings`, and `.errors` lists. Warnings are for non-fatal issues (e.g., operationId not found - rules ignored). Errors are for fatal issues (e.g., unknown predefined name).

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
    def __init__(
        self,
        cel_evaluator: CELEvaluator,
        comparison_library: ComparisonLibrary,
        schema_validator: SchemaValidator | None = None,  # Optional, for schema validation
    ): ...

    def compare(
        self,
        response_a: ResponseCase,
        response_b: ResponseCase,
        rules: OperationRules,
        operation_id: str | None = None,  # Needed for schema validation to run
    ) -> ComparisonResult: ...
```

Caller owns CEL Evaluator lifecycle:

```python
with CELEvaluator() as cel:
    comparator = Comparator(cel, library)
    result = comparator.compare(response_a, response_b, rules)
```

With schema validation (OpenAPI Spec as Field Authority):

```python
from api_parity.schema_validator import SchemaValidator
validator = SchemaValidator(spec_path)
with CELEvaluator() as cel:
    comparator = Comparator(cel, library, schema_validator=validator)
    result = comparator.compare(response_a, response_b, rules, operation_id="createWidget")
```

When `schema_validator` is provided, the Comparator validates each response against the OpenAPI schema before comparison. Schema violations are recorded as mismatches. The `operation_id` parameter is required for schema lookup when using schema validation.

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
    # Class constants
    MAX_RESTARTS = 3          # Restart attempts before giving up
    STARTUP_TIMEOUT = 5.0     # Seconds to wait for ready signal
    EVALUATION_TIMEOUT = 10.0 # Seconds per evaluate() call (Go has internal 5s limit)

    def __init__(self, binary_path: str | Path | None = None): ...
    def evaluate(self, expression: str, data: dict[str, Any]) -> bool: ...
    def close(self) -> None: ...
```

The Comparator calls `evaluate()` for each field comparison. It has no knowledge of the subprocess—just a function that takes an expression and data, returns a boolean. Use as context manager for automatic cleanup: `with CELEvaluator() as e: ...`

### Lifecycle

- Started once at CLI startup (before explore/replay begins)
- Kept alive for duration of run
- Terminated on CLI exit (close stdin, wait for process)
- Restarted automatically if subprocess crashes (EOF detected on stdout), up to `MAX_RESTARTS` times
- `CELSubprocessError` raised if subprocess fails to start or exceeds restart limit

---

## Bundle Loader [IMPLEMENTED]

The Bundle Loader (`api_parity/bundle_loader.py`) reads mismatch bundles from disk for replay mode. Fully implemented and tested. Supports both stateless and chain bundles.

### Interface

```python
# Discover all bundle directories in an artifacts path
bundles = discover_bundles(directory: Path) -> list[Path]

# Load a single bundle into memory
bundle = load_bundle(bundle_path: Path) -> LoadedBundle

# Detect bundle type without full load
bundle_type = detect_bundle_type(bundle_path: Path) -> BundleType

# Extract link_fields from chain case for Executor (replay needs this)
link_fields = extract_link_fields_from_chain(chain: ChainCase) -> set[str]
```

### LoadedBundle Structure

```python
@dataclass
class LoadedBundle:
    bundle_path: Path           # Where the bundle was loaded from
    bundle_type: BundleType     # STATELESS or CHAIN
    request_case: RequestCase | None  # For stateless bundles
    chain_case: ChainCase | None      # For chain bundles
    original_diff: dict[str, Any]     # Raw diff.json for comparison
    metadata: MismatchMetadata        # Run context
```

### Chain Replay Support

For chain replay, the Executor needs `link_fields` to extract variables from responses. Since replay doesn't have the OpenAPI spec, `extract_link_fields_from_chain()` analyzes the chain's `link_source` fields to determine which response fields need extraction. It converts JSONPath expressions (`$.id`, `$.data.items[0].id`) to JSONPointer format (`id`, `data/items/0/id`).

### Error Handling

- `BundleLoadError` raised for missing files, invalid JSON, or schema validation failures
- Bundle discovery is lenient: directories without `case.json` or `chain.json` are silently skipped
- Load errors are surfaced individually to allow partial replay of valid bundles

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
    # Optional mTLS configuration
    cert: /path/to/client.crt
    key: /path/to/client.key
    ca_bundle: /path/to/ca-bundle.crt
  staging:
    base_url: https://staging.api.example.com
    headers:
      Authorization: "Bearer ${STAGING_TOKEN}"
    verify_ssl: false  # Skip server certificate verification (use with caution)

comparison_rules: ./comparison_rules.json  # path to comparison rules file

rate_limit:
  requests_per_second: 10

secrets:
  redact_fields:
    - "$.password"
    - "$.token"
    - "$.api_key"
```

**TLS Configuration Options** (all paths support `${ENV_VAR}` substitution):
- `cert` / `key` — Client certificate and private key (PEM format) for mTLS. Both must be provided together.
- `key_password` — Password for encrypted private key. Supports `${ENV_VAR}` substitution for secure handling.
- `ca_bundle` — Custom CA bundle for server certificate verification. When set, `verify_ssl` is ignored.
- `verify_ssl` — Set to `false` to disable server certificate verification. Only applies when `ca_bundle` is not set. Default: `true`.
- `ciphers` — OpenSSL cipher string to restrict allowed TLS ciphers (e.g., `'ECDHE+AESGCM'`). Use when connecting to servers with specific cipher requirements or for security hardening.

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

Chains are auto-discovered from explicit OpenAPI links via Schemathesis `schema.as_state_machine()`. Validated: 6-step chains, 70+ unique sequences.

**Explicit links only:** Chain generation uses only explicit OpenAPI link definitions, not inferred relationships. Schemathesis inference algorithms (parameter name matching, Location headers) are disabled. This ensures chains follow documented API contracts, not guessed relationships. See DESIGN.md "Explicit Links Only for Chain Generation" for rationale.

**Execution semantics:**
- Each target maintains its own extracted variables (A's POST returns `id: abc`, B's returns `id: xyz` → A's GET hits `/items/abc`, B's hits `/items/xyz`)
- Mismatch stops chain and records; same error on both = parity, chain continues
- Schemathesis handles variable extraction via OpenAPI link expressions

**Replay:** Re-executes full chain with fresh generated values (not original). Tests failure pattern, not exact bytes. See DESIGN.md "Replay Regenerates Unique Fields".

---

## OpenAPI Spec as Field Authority [IMPLEMENTED]

Response fields not present in the OpenAPI spec are treated as mismatches (category: `schema_violation`). This forces the spec to accurately describe the API. See DESIGN.md "OpenAPI Spec as Field Authority" and "Handling additionalProperties in Schema Validation" for design decisions.

**Behavior:**
- Validate each response against the OpenAPI response schema for that operation+status_code
- `additionalProperties: false` → Extra fields are schema violations (errors)
- `additionalProperties: true` or unspecified → Extra fields allowed, but still compared between A and B
- Extra fields use equality comparison (custom rules not currently supported)
- Schema violations are a separate category from comparison mismatches

**Implementation Details:**
- **SchemaValidator Component** (`api_parity/schema_validator.py`): Extracts response schemas from OpenAPI spec and validates responses against them
- **JSON Schema Library:** `jsonschema` (v4.23.0) - well-established library with Draft4 support
- **Mismatch Type:** `SCHEMA_VIOLATION` in MismatchType enum
- **Comparison Phase:** Phase 0 (before status code comparison) validates both responses

**Interface:**
```python
from api_parity.schema_validator import SchemaValidator

validator = SchemaValidator(spec_path)
result = validator.validate_response(body, operation_id, status_code)
if not result.valid:
    for violation in result.violations:
        print(f"{violation.path}: {violation.message}")
```

**Replay Mode:** Schema validation is not performed during replay (no spec available).

**Limitations:**
- Recursive schema refs (e.g., `Node` with `children: $ref Node`) are detected and left unresolved to prevent infinite loops
- `extra_fields` tracking only considers root-level `additionalProperties`; nested restrictions are enforced by jsonschema validation

---

## Implementation Approach [SPECIFIED]

**Generator:** Schemathesis v4.9.1 — OpenAPI-driven fuzzing with stateful link support. See DESIGN.md "Schemathesis as Generator (Validated)" and "Explicit Links Only for Chain Generation" for integration details.

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

### Test Layers

Tests are organized into two layers with automatic pytest markers:

| Layer | Location | Marker | Characteristics |
|-------|----------|--------|-----------------|
| Unit | `tests/test_*.py` | `@pytest.mark.unit` | Fast, isolated, mock dependencies |
| Integration | `tests/integration/` | `@pytest.mark.integration` | Real subprocess, network, file I/O |

**Running by layer:**
```bash
pytest -m unit          # Fast unit tests only
pytest -m integration   # Integration tests only
pytest                  # All tests
```

Markers are applied automatically via `pytest_collection_modifyitems` in conftest.py based on file path.

### Test File Organization

Large test files are split by concern for smaller context loads:

**Comparator tests** (`tests/test_comparator_*.py`):
- `test_comparator_core.py` — NOT_FOUND sentinel
- `test_comparator_status.py` — Status code comparison, order
- `test_comparator_headers.py` — Header comparison, presence modes
- `test_comparator_body.py` — Body comparison, presence modes
- `test_comparator_jsonpath.py` — Wildcard paths, recursive descent
- `test_comparator_results.py` — Result structure, edge cases

**CLI tests** (`tests/test_cli_*.py`):
- `test_cli_explore.py` — Explore subcommand arguments
- `test_cli_replay.py` — Replay subcommand arguments
- `test_cli_list_ops.py` — List-operations subcommand
- `test_cli_common.py` — General parser, validators

**Integration tests** (`tests/integration/`):
- `test_explore_*.py` — End-to-end explore CLI scenarios
- `test_stateful_chains.py` — Chain generation and execution
- `test_comparator_cel.py` — Real CEL subprocess integration

Shared fixtures are in `tests/conftest.py` (global) and per-directory fixture files (`tests/comparator_fixtures.py`, `tests/integration/explore_helpers.py`).

### Port Allocation

Tests use `PortReservation` class for safer port allocation:
- Holds socket open until just before server starts
- Uses `SO_REUSEADDR` to minimize port exhaustion
- Reduces race conditions vs simple `find_free_port()`

---

## Sources

Architecture informed by analysis of:
- [Schemathesis GitHub](https://github.com/schemathesis/schemathesis) — Case class, Response class, VCR cassettes
- [Schemathesis PyPI](https://pypi.org/project/schemathesis/) — v4.9.1 features
- [Schemathesis stateful testing issue #864](https://github.com/schemathesis/schemathesis/issues/864) — CLI stateful approach
