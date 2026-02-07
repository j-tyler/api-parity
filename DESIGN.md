# Design Decisions

Records architectural and design decisions with reasoning. Preserves historical context so AI agents and future contributors don't retread the same ground or accidentally reverse solved problems.

**Format:** H1 heading, Keywords (for grep), Date (YYYYMMDD), then reasoning.

---

# Core Approach: Differential Testing

Keywords: testing fuzz comparison parity
Date: 20260108

Send identical requests to both implementations and compare responses. Chosen over contract testing (validates against spec only, not between implementations) and record/replay (requires capturing production traffic). Differential fuzzing generates test cases from OpenAPI spec without manual test writing and finds edge cases humans miss.

---

# AI-Optimized Code and Documentation

Keywords: LLM AI agent code style documentation
Date: 20260108

Write code for AI agents, not humans. Prefer inline logic over indirection—avoid fragmenting logic into small helper methods just for "clean code" aesthetics. Principled reuse is fine; unnecessary abstraction is not. Keep documentation token-efficient and information-dense. This is why the codebase may look "verbose" by traditional standards.

---

# Serialized Execution Only

Keywords: concurrency ordering determinism
Date: 20260108

All requests execute serially—no concurrent requests. Concurrency introduces non-determinism that makes comparison unreliable. If targets respond differently due to race conditions, that's noise, not a parity signal. Performance cost is acceptable for correctness.

---

# OpenAPI Spec as Field Authority

Keywords: schema validation fields response
Date: 20260108

Response fields not in the OpenAPI spec are treated as mismatches. Forces the spec to describe the API accurately. The spec is truth for what fields exist; user rules define which fields to compare and how.

For `additionalProperties: false`, extra fields are violations. For `true` or unspecified, extras are allowed but still compared between targets.

**Key insight:** Schema validity and implementation equivalence are separate concerns. A response can be valid (matches schema) but not equivalent (differs from other implementation). Example: if the spec allows extras and Target A returns `{id: 1, debug: "foo"}` while Target B returns `{id: 1, trace_id: "bar"}`, both are schema-valid but they're not equivalent—the agent should know about this divergence.

---

# User-Defined Comparison Rules Required

Keywords: comparison rules configuration
Date: 20260108

Users must define comparison rules. The tool does not guess which fields are volatile (timestamps, UUIDs) or which differences are acceptable. Heuristic guessing would produce unreliable results and hide real mismatches.

The cost is upfront configuration work. The benefit is deterministic, understandable comparison behavior. Every mismatch the tool reports is a mismatch the user asked it to detect.

---

# CEL as Comparison Engine

Keywords: cel expression language comparison
Date: 20260110

All field comparisons evaluate as CEL expressions. CEL is battle-tested (Kubernetes, Firebase), safely sandboxed, has simple syntax LLMs generate reliably, and well-defined semantics.

Runtime is CEL-only—receives expressions and evaluates with `{a: target_a_value, b: target_b_value}`. Config loading expands predefined comparisons to CEL before runtime. This keeps runtime simple and lets the predefined library grow without runtime changes.

---

# CEL via Go Subprocess

Keywords: cel go subprocess python
Date: 20260110

CEL evaluates in a Go subprocess (cel-go), not Python. Python CEL libraries are untrusted dependencies. No Go equivalent to Schemathesis exists, so we can't go all-Go.

Architecture: Python handles CLI, Schemathesis, HTTP execution, artifacts. Go handles CEL only. The subprocess hides behind `CELEvaluator` class—if a trusted Python CEL library emerges, swap implementation without changing callers.

Uses stdin/stdout pipes with NDJSON (not Unix sockets) because we have single-client, and pipes auto-cleanup on crash.

**Considered alternatives:**
- **gRPC** — Overkill for single-client IPC, adds protobuf dependency
- **Unix sockets** — Doesn't auto-cleanup if Python crashes, more setup code
- **Embedded interpreter** — No trusted Python CEL implementation exists
- **All-Go** — No Go equivalent to Schemathesis for OpenAPI fuzzing

**Error handling:** The Go process returns `{"ok":false,"error":"..."}` for CEL errors (bad syntax, type errors). Python-side timeout (10s) handles hung evaluations. Subprocess crashes trigger auto-restart (max 3 times).

---

# Predefined Comparison Library

Keywords: predefined library expressions
Date: 20260110

Named comparisons that expand to CEL expressions during config loading. Each predefined has a fixed signature—parameterized comparisons require all parameters.

**How it works:**

User config references predefined by name:
```json
{"predefined": "numeric_tolerance", "tolerance": 0.01}
```

Config loader inlines to pure CEL before runtime:
```json
{"expr": "(a - b) <= 0.01 && (b - a) <= 0.01"}
```

This separation means runtime is simple (just CEL evaluation), and the predefined library can grow without runtime changes. Users can escape to custom CEL with `{"expr": "..."}` when predefineds don't suffice.

Location: `prototype/comparison-rules/comparison_library.json`.

---

# Schemathesis as Generator

Keywords: schemathesis generation fuzzing
Date: 20260108

Schemathesis v4.9.1 generates requests from OpenAPI specs. Validation confirmed it meets all requirements: case generation without HTTP calls, stateful chains via links, variable extraction between steps.

Use `GenerationMode.POSITIVE` for schema-valid data only. Default generation includes garbage for fuzz testing, which isn't useful for parity comparison.

**Key API patterns:**

```python
from schemathesis.openapi import from_path
from schemathesis.generation import GenerationMode

# Load spec
schema = from_path("spec.yaml")

# Generate individual cases (stateless)
for result in schema.get_all_operations():
    operation = result.ok()  # Unwrap Ok/Err
    strategy = operation.as_strategy(generation_mode=GenerationMode.POSITIVE)
    # Use Hypothesis to draw cases from strategy

# Generate chains (stateful)
state_machine = schema.as_state_machine()
# Subclass and override call() to execute against both targets
```

Case objects provide: `method`, `path`, `path_parameters`, `formatted_path`, `query`, `headers`, `cookies`, `body`, `media_type`, `as_transport_kwargs()`, `as_curl_command()`.

---

# Explicit Links Only for Chains

Keywords: stateful chains links inference
Date: 20260117

Chain generation uses only explicit OpenAPI links, not inferred relationships. Schemathesis can infer from parameter names or Location headers—disabled because:
- Inferred relationships may not reflect actual API contracts
- Users can see exactly which chains will be tested by reading their spec
- Inference might exercise invalid transitions (using order ID where user ID expected)
- Missing links is a spec quality issue that should be fixed, not papered over

---

# Live Chain Generation

Keywords: chains stateful execution generation
Date: 20260108

Chains are generated live, not pre-generated offline. Each target executes the same operation sequence but uses its own extracted response data for subsequent steps. If A's POST returns id "abc" and B's returns "xyz", A's GET uses "abc" while B's uses "xyz". Pre-generation would require mocking responses, defeating differential testing.

---

# Chain Stops at First Mismatch

Keywords: chains errors mismatch abort
Date: 20260110

When a chain step produces a mismatch, the chain stops. Subsequent steps are not executed.

Key distinction—**mismatch** vs **parity**:
- Both return 404 → parity, chain continues
- A returns 404, B returns 200 → mismatch, chain stops
- Both return 200 with same body → parity, chain continues
- Both return 200 with different body → mismatch, chain stops

Rationale: If targets diverge, they're in different states. Comparing subsequent steps produces noise. Stop at the first discrepancy, fix it, re-run to discover the next.

Note: Schemathesis continues chains after errors by default. api-parity overrides this to stop on mismatch.

---

# Replay Regenerates Unique Fields

Keywords: replay unique constraints
Date: 20260108

Replay regenerates data for unique fields rather than reusing original values. Prevents conflicts on CREATE operations. Consequence: if a specific unique value caused the mismatch, exact replay isn't possible. Replay tests failure patterns, not exact bytes.

---

# Override Semantics, Not Merge

Keywords: rules override inheritance
Date: 20260110

Operation rules completely override defaults for any key they define—no deep merging. Simpler mental model: you can read what applies to an operation without mentally merging nested objects.

**Example:**
```json
{
  "default_rules": {
    "headers": {
      "content-type": {"predefined": "exact_match"},
      "x-request-id": {"predefined": "uuid_format"}
    }
  },
  "operation_rules": {
    "createWidget": {
      "headers": {
        "location": {"predefined": "url_format"}
      }
    }
  }
}
```

For `createWidget`: only `location` is compared. The defaults for `content-type` and `x-request-id` are **not inherited** because `createWidget` defines its own `headers` block.

**Why no merging:** Deep merging creates ambiguity. Does the operation want to add to defaults or replace them? With override semantics, intent is explicit. If you want defaults plus extras, repeat the defaults.

---

# Comparison Rules: JSON for LLM Authorship

Keywords: format json yaml llm config
Date: 20260110

**Design constraint:** LLMs are the primary authors of comparison rules configs. Humans will read them for the 1% of cases LLMs can't automate. This inverts traditional config file priorities.

Comparison rules use JSON because: unambiguous parsing (no YAML coercion like `NO` → boolean), LLMs generate valid JSON more reliably, and JSON Schema validation is native.

---

# Error Classification Defaults

Keywords: errors status codes classification
Date: 20260108

Default error handling:
- Same 500-class on both targets: not recorded (assumed transient infrastructure noise)
- Differing 400-class errors between targets: recorded (indicates behavioral difference)
- Cross-class difference (A returns 500, B returns 400): mismatch (different status classes indicate behavioral difference, not infrastructure noise)

Users can override these defaults in configuration. This prevents infrastructure noise from polluting mismatch artifacts while capturing meaningful client-error divergence.

---

# Eventual Consistency Out of Scope

Keywords: eventual consistency timing
Date: 20260108

Eventually consistent APIs not supported in v0. Assumes responses are immediately consistent. If needed later, add retry/polling mechanisms.

---

# Secret Redaction is User-Configured

Keywords: secrets redaction security
Date: 20260108

Users define which fields contain secrets via JSONPath in config. No automatic detection. Redaction applies when writing artifacts. Users know their sensitive fields better than heuristics.

---

# Pydantic v2 for Data Models

Keywords: pydantic dataclass serialization
Date: 20260111

Models use Pydantic v2, not plain dataclasses or JSON Schema definitions.

**Why Pydantic over alternatives:**

| Requirement | Plain dataclasses | JSON Schema | Pydantic v2 |
|-------------|-------------------|-------------|-------------|
| Python objects | ✓ | ✗ (needs codegen) | ✓ |
| JSON serialization | Manual | N/A | Built-in |
| Validation on load | Manual | Separate library | Built-in |
| Computed fields | Property decorator | ✗ | `@computed_field` |
| Mutual exclusion | Manual | `oneOf` | `@model_validator` |

Key features used: `model_dump()` for artifact writing, `model_validate_json()` for replay loading, `@computed_field` for `rendered_path`, `@model_validator` for mutual exclusion (`body` vs `body_base64`). Type hints provide immediate feedback for LLM agents.

---

# jsonpath-ng for JSONPath

Keywords: jsonpath extraction
Date: 20260111

Uses `jsonpath-ng` for standard JSONPath syntax, all wildcard patterns, and parse-once-execute-many caching.

---

# CEL Errors as Mismatches, Not Exceptions

Keywords: cel error handling
Date: 20260111

CEL evaluation failures record as mismatch with `rule: "error: ..."` rather than raising exceptions. One broken rule shouldn't crash the run. Errors appear in artifacts for debugging. Infrastructure failures (`CELSubprocessError` from subprocess crash) do propagate.

---

# CLI Arguments Override Config File

Keywords: cli config precedence
Date: 20260111

Config files capture stable defaults. CLI flags enable one-off overrides without editing files. Precedence: CLI > config file > built-in defaults.

---

# Dynamic Link Field Extraction

Keywords: links fields extraction parsing
Date: 20260112

Link field names parsed dynamically from OpenAPI link expressions at init, not hardcoded. Enables any field name (`resource_uuid` instead of `id`) without configuration. Supports both body (`$response.body#/path`) and header (`$response.header.Location`) expressions.

The spec is parsed twice at startup—once by Schemathesis, once for link extraction—because Schemathesis doesn't expose raw spec. Cost is minimal (one file read).

---

# Source-Only Distribution

Keywords: distribution packaging pypi
Date: 20260112

Distributed as source, not PyPI package. Users clone and run `build.sh`. The CEL evaluator is a Go binary requiring platform-specific compilation. Building wheels for all platforms isn't justified for a small user base. Users who can install Go can build in under a minute.

---

# Pinned Dependencies

Keywords: dependencies versions reproducible
Date: 20260112

All dependencies pinned to exact versions. Critical for build-from-source where users build at different times. Range specifiers can pull newer versions with breaking changes. Pinning enables reproducibility, debuggability, and security auditing.

---

# Binary Body Comparison via CEL

Keywords: binary body base64 comparison
Date: 20260117

Binary responses stored as base64 strings and compared via CEL string operations.

**Why base64:** CEL operates on strings, not raw bytes. Base64 is 33% larger but avoids adding a separate binary comparison path—reuses existing CEL infrastructure.

**Why not compared by default:** Existing users have configs without `binary_rule`. Comparing binaries by default would break backward compatibility with false-positive mismatches. Explicit opt-in via `binary_rule` in comparison config.

Predefineds: `binary_exact_match`, `binary_length_match`, `binary_nonempty`.

---

# Schema-Aware Synthetic Generation

Keywords: synthetic values schema enum chain
Date: 20260118

**Problem:** Chain discovery generates synthetic responses to explore link paths. If a link passes a value to an enum-constrained parameter (e.g., `status` must be `active|pending|closed`), a generic UUID placeholder would be rejected by Schemathesis validation, breaking chain discovery.

**Solution:** `SchemaValueGenerator` produces values satisfying OpenAPI schema constraints:
- Enum: uses first allowed value
- Const: uses the const value
- Format (uuid/date-time/uri/email): generates compliant string
- Type: generates appropriate default

Graceful fallback to UUID when no schema constraint found.

---

# Spec-Based Status Code for Synthetic Responses

Keywords: status code synthetic links
Date: 20260118

Synthetic responses use the correct status code from the spec (not hardcoded 201/200). Links on 201 for PUT or 202 for DELETE wouldn't be found otherwise. `_find_status_code_with_links()` searches spec for lowest 2xx status with links.

---

# Hybrid Coverage Mode

Keywords: coverage guarantee operations stateful
Date: 20260119

Schemathesis's state machine is probabilistic, not exhaustive. 26-33% of operations may never be tested by chains due to orphans or deep chain depth. Added `--ensure-coverage` flag: runs chain generation, tracks coverage, runs single-request tests on uncovered operations. Guarantees 100% operation coverage.

---

# Chain Depth Coverage Lint

Keywords: lint chain depth bfs
Date: 20260119

`lint-spec` identifies operations reachable only at depth 3+ (rarely explored). Uses BFS from entry points. Reports potential link sources to create shortcuts. Complements `--ensure-coverage` with actionable spec improvements.

---

# Coverage Depth: Per-Operation Hit Targeting

Keywords: coverage depth min-hits-per-op min-coverage seed walking
Date: 20260207

Seed walking's original stopping criterion was "all linked operations covered at least once." This is necessary but not sufficient for thorough testing — an operation appearing in only 1 chain gets tested with just one set of fuzz inputs.

Added `--min-hits-per-op N` and `--min-coverage P` to control how deeply each operation is explored. The coverage target is: P% of linked operations must appear in N+ unique (deduplicated) chains. Default is N=1, P=100 (backward compatible).

Key design choices:
- **Hits count unique chains, not total appearances.** A chain [A, B, A] gives opA one hit, not two. Duplicate chains (same operation-ID sequence) are filtered.
- **When depth target set, max_chains defaults to unlimited.** Without this, the legacy default of 20 chains would prematurely stop seed walking before the depth target is met. Users can still set `--max-chains` as an explicit hard cap.
- **Partial coverage via min_coverage_pct.** Allows tolerating hard-to-reach operations (e.g., depth-5 chains) while still ensuring most of the API is well-tested.

---

# Incremental Seed Walking

Keywords: seed walking chains reproducibility
Date: 20260119

When `--seed N` and `--max-chains M` specified, if seed N produces fewer than M chains, try N+1, N+2, etc. (up to 100 attempts). Chains deduplicated by operation sequence. Still reproducible—starting seed determines entire sequence.

---

# Hypothesis Seed Control

Keywords: hypothesis seed derandomize reproducibility
Date: 20260119

Controlling Hypothesis randomness for dynamic test functions requires setting `_hypothesis_internal_use_seed` on the function plus `derandomize=True`. The `@seed()` decorator can't be used because functions are defined at runtime. `derandomize=True` alone makes all seeds identical—the internal attribute is what differentiates sequences. This is internal API; may change in future Hypothesis versions.

---

# Session-Scoped Test Fixtures

Keywords: pytest fixtures session performance
Date: 20260112

`fixture_dual_mock_servers` is session-scoped (servers start once per session, not per test). ~50% faster. Acceptable because integration tests check CLI output strings, not server state. Widget accumulation doesn't affect correctness.
