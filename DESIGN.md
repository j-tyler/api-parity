# Design Decisions

This document records architectural and design decisions made in the api-parity project. It preserves historical reasoning so AI agents and future contributors don't retread the same ground.

---

# Design Decision Format

Keywords: format structure template decision record
Date: 20260108

Each decision: H1 heading, Keywords line (for grep), Date (YYYYMMDD), then free-form paragraphs explaining problem/decision/reasoning. Chosen over structured ADR templates for natural readability.

---

# Differential Testing Approach

Keywords: testing fuzz comparison parity verification
Date: 20260108

The core approach for verifying API parity is differential fuzzing: send identical requests to both API implementations and compare responses. This was chosen over contract testing alone (which only validates against a spec, not between implementations) and record/replay testing (which requires capturing production traffic). Differential fuzzing generates test cases from the OpenAPI spec, so it doesn't require manual test writing and can find edge cases humans would miss.

---

# AI-Optimized Code and Documentation

Keywords: LLM AI agent code style documentation tokens context
Date: 20260108

Write code for AI agents, not humans. Prefer inline logic over indirection—avoid fragmenting logic into small helper methods within files or across files just for "clean code" aesthetics. Principled reuse of common methods is fine; unnecessary abstraction is not. Keep documentation token-efficient and information-dense. Organize files so agents get relevant context without clutter.

---

# Serialized Execution Only

Keywords: concurrency parallelism ordering execution determinism
Date: 20260108

All requests execute serially—no concurrent requests to either target. Concurrency introduces non-determinism that makes differential comparison unreliable. If target A and B respond differently due to race conditions or timing, that's not a meaningful parity signal. Serial execution eliminates this noise. Performance cost is acceptable for the correctness guarantee.

---

# OpenAPI Spec as Field Authority

Keywords: comparison validation schema fields response openapi
Date: 20260108

Response fields not present in the OpenAPI spec are treated as mismatches. This forces the spec to accurately describe the API and prevents undocumented fields from silently diverging between implementations. The spec becomes the source of truth for what fields exist; user-provided comparison rules become the source of truth for which fields to compare and how.

---

# User-Defined Comparison Rules Required

Keywords: comparison rules configuration per-endpoint diff
Date: 20260108

Users must define per-endpoint comparison rules. The tool does not guess which fields are volatile (timestamps, UUIDs) or which differences are acceptable. Heuristic guessing would produce unreliable results and hide real mismatches. The cost is upfront configuration work, but the benefit is deterministic, understandable comparison behavior. Every mismatch the tool reports is a mismatch the user asked it to detect.

---

# Chain Generation via Schemathesis Links

Keywords: stateful chains links schemathesis generation sequences
Date: 20260108

Stateful request chains (create→get→update→delete patterns) are auto-discovered by Schemathesis from OpenAPI links, not manually specified. This minimizes configuration and leverages Schemathesis's existing link traversal. The tradeoff is that chain coverage depends on how thoroughly the OpenAPI spec defines links. User documentation must explain that detailed link definitions in the spec are required for effective chain testing. Target chain depth is at least 6 steps (e.g., create→get→update→get→delete→get).

---

# Replay Regenerates Unique Fields

Keywords: replay idempotency unique constraints data generation
Date: 20260108

Replay mode regenerates data for fields with uniqueness constraints rather than reusing original values. This prevents conflicts when replaying CREATE operations. The consequence: if a specific value of a unique field caused the original mismatch, that exact case cannot be replayed—replay tests the pattern of the failure, not the exact bytes. This is an acceptable limitation; the alternative (requiring manual cleanup between runs) is worse for automation.

---

# Error Classification Defaults

Keywords: errors 500 400 status codes classification recording
Date: 20260108

Default error handling: 500-class errors are not recorded as mismatches (assumed transient/infrastructure). Differing 400-class errors between targets are recorded (indicates behavioral difference). Users can override these defaults in configuration. This prevents infrastructure noise from polluting mismatch artifacts while capturing meaningful client-error divergence. Cross-class differences (e.g., A returns 500, B returns 400) are mismatches—different status code classes indicate behavioral difference, not infrastructure noise.

---

# Eventual Consistency Out of Scope

Keywords: eventual consistency timing async replication
Date: 20260108

Eventually consistent APIs (where GET may return stale data for some time after CREATE/UPDATE) are not supported in v0. The tool assumes responses are immediately consistent. This simplifies comparison logic significantly. If eventual consistency becomes a requirement, we may add retry/polling mechanisms or per-endpoint timing tolerances later.

---

# Secret Redaction is User-Configured

Keywords: secrets redaction security credentials configuration
Date: 20260108

Users define which fields contain secrets via configuration. The tool does not attempt to detect secrets automatically. Redaction applies when writing artifacts to disk (mismatch bundles). This keeps the redaction logic simple and predictable—the user knows their API's sensitive fields better than any heuristic could guess.

---

# Schemathesis as Generator (Validated)

Keywords: schemathesis generator fuzzing openapi tooling validation hypothesis
Date: 20260108

Schemathesis (v4.8.0) is the chosen request generator. Validation completed 20260108 confirms it meets all critical requirements. Pin this version for API stability.

**Stateless test generation:**
```python
from schemathesis.openapi import from_path
from schemathesis.generation.modes import GenerationMode

schema = from_path("spec.yaml")
for result in schema.get_all_operations():
    operation = result.ok()  # Results are wrapped, unwrap with .ok()
    strategy = operation.as_strategy(generation_mode=GenerationMode.POSITIVE)
    # Use Hypothesis @given(case=strategy) to generate cases
```

Case objects contain: `method`, `path`, `path_parameters`, `query`, `headers`, `cookies`, `body`, `media_type`, `formatted_path`. Use `case.as_transport_kwargs()` for ready-to-use HTTP client parameters.

**Stateful chain generation:**
```python
from schemathesis.specs.openapi.stateful import OpenAPIStateMachine

schema = from_path("spec.yaml")
OriginalSM = schema.as_state_machine()

class DualTargetStateMachine(OpenAPIStateMachine, OriginalSM):
    def validate_response(self, response, case, **kwargs):
        pass  # Skip built-in validation, we do our own comparison

    def call(self, case, **kwargs):
        # Execute against Target A, then Target B
        # Compare responses - if mismatch, stop chain (raise or record)
        # If match, return A's response for chain continuation
```

The `call()` method must return a `schemathesis.core.transport.Response` object with fields: `status_code`, `headers` (dict with list values), `content` (bytes), `request` (PreparedRequest), `elapsed`, `verify`, `http_version`.

State machine auto-discovers transitions from OpenAPI links. Note: Schemathesis continues chains after errors by default; api-parity overrides this to stop on mismatch (see "Chain Stops at First Mismatch").

---

# Live Chain Generation

Keywords: chains stateful execution generation live
Date: 20260108

Chains are generated live, not pre-generated offline. Each target executes the same operation sequence but uses its own extracted response data for subsequent steps. If A's POST returns id "abc" and B's returns "xyz", A's GET uses "abc" while B's uses "xyz". Pre-generation would require mocking responses, defeating differential testing.

---

# Use GenerationMode.POSITIVE

Keywords: generation mode positive negative fuzz valid invalid
Date: 20260108

Use `GenerationMode.POSITIVE` when generating test cases. This produces schema-valid data only.

Default generation includes negative/fuzz testing (invalid data like `%C3%8F%C2%93...` for UUID fields). While useful for security testing, api-parity's goal is comparing behavior on valid inputs. If both targets reject garbage the same way, that's not a meaningful parity signal.

Validated: POSITIVE mode generated 30/30 valid UUIDs; NEGATIVE mode generated 0/30 valid (all garbage).

---

# Chain Stops at First Mismatch

Keywords: chains errors mismatch abort stop parity
Date: 20260110

When a chain step produces a mismatch between targets, the chain stops. Subsequent steps are not executed.

Key distinction—**mismatch** vs **error**:
- Both return 404 → parity, chain continues
- A returns 404, B returns 200 → mismatch, chain stops
- Both return 200 with same body → parity, chain continues
- Both return 200 with different body → mismatch, chain stops

Rationale: If targets diverge, they're in different states. Comparing subsequent steps produces noise. Stop at the first discrepancy, fix it, re-run to discover the next.

**Note:** Schemathesis continues after errors by default. api-parity overrides this to stop on mismatch.

---

# Comparison Rules Format: JSON for LLM Authorship

Keywords: comparison rules format json yaml llm agent config
Date: 20260110

Comparison rules use JSON format, optimized for LLM writing and human reading.

**Design constraint:** LLMs are the primary authors of comparison rules configs. Humans will read them for the 1% of cases LLMs can't automate. This inverts traditional config file priorities.

**Why JSON over YAML:**
- Unambiguous parsing (no YAML type coercion surprises like `NO` → boolean)
- LLMs generate valid JSON more reliably than YAML
- JSON Schema validation is native
- Human readability is "good enough" for occasional reading

**Structure principles:**
- Explicit field names (`ignored_paths` not `ignore`)
- No shorthand forms (always object syntax, never scalar shortcuts)
- Self-documenting through verbose naming
- One level of inheritance max (operation rules → default rules)
- Override semantics, not merge (simpler mental model)

See `prototype/comparison-rules/` for working implementation.

---

# CEL as Comparison Engine

Keywords: cel expression language comparison evaluation runtime
Date: 20260110

All field comparisons evaluate as CEL (Common Expression Language) expressions at runtime. CEL was chosen because:

- Battle-tested in production (Kubernetes, Firebase, Envoy)
- Safe evaluation (not Turing-complete, no arbitrary code execution)
- Simple syntax that LLMs can generate reliably
- Well-defined semantics across implementations

**Runtime is CEL-only.** The runtime receives expressions like `(a - b) <= 0.01 && (b - a) <= 0.01` and evaluates them with bindings `{a: value_from_target_a, b: value_from_target_b}`. The runtime has no knowledge of "comparison types" or special cases—just CEL evaluation.

**Config loading inlines predefined comparisons to CEL** before the config reaches the runtime. This separation means:
1. Runtime code is simple (just CEL evaluation)
2. Predefined library can grow without runtime changes
3. Custom expressions and predefined use identical code paths

---

# Predefined Comparison Library

Keywords: predefined library comparisons expressions cel templates
Date: 20260110

A predefined comparison library ships with api-parity. It contains named comparisons that expand to CEL expressions during config loading.

**Location:** `prototype/comparison-rules/comparison_library.json`. Self-documenting with descriptions for each predefined.

**Design: No optional parameters.** Each predefined has a fixed signature. Parameterized comparisons require all parameters. If you want different behavior, use a different predefined or write custom CEL.

```json
{
  "predefined": {
    "ignore": {
      "description": "Always passes. Field is not compared.",
      "params": [],
      "expr": "true"
    },
    "numeric_tolerance": {
      "description": "Numbers are equal within tolerance.",
      "params": ["tolerance"],
      "expr": "(a - b) <= tolerance && (b - a) <= tolerance"
    }
  }
}
```

**User config references predefined by name:**
```json
{"predefined": "numeric_tolerance", "tolerance": 0.01}
```

**Config loader inlines to pure CEL:**
```json
{"expr": "(a - b) <= 0.01 && (b - a) <= 0.01"}
```

**Escape hatch:** Users can write custom CEL directly with `{"expr": "..."}` when predefined comparisons don't suffice.

**Validation:** JSON Schema enforces that predefined names are valid and required parameters are present.

See `prototype/comparison-rules/` for working implementation with validation and inlining.

---

# CEL Evaluation via Go Subprocess

Keywords: cel go subprocess ipc python hybrid architecture
Date: 20260110

CEL expressions are evaluated by a Go subprocess running cel-go, not a Python library. This decision was driven by dependency constraints: cel-python and similar Python CEL libraries cannot be used because they are untrusted dependencies.

**Why not all-Python with a CEL alternative?**
CEL alternatives (JsonLogic, JMESPath, RestrictedPython) lack CEL's combination of: sandboxed execution, expressive syntax, user-defined functions, and battle-tested security model. Replacing CEL would trade a solved problem for an unsolved one.

**Why not all-Go replacing Schemathesis?**
No Go equivalent to Schemathesis exists. Go has OpenAPI parsers (kin-openapi) and property testing (rapid), but no integrated tool providing: OpenAPI-driven generation, stateful chains via links, Hypothesis-style shrinking. Rebuilding this would be massive scope creep.

**Architecture:**
- Python: Main application (CLI, Schemathesis integration, HTTP execution, artifact writing)
- Go: Single-purpose CEL evaluator subprocess (`cmd/cel-evaluator/main.go`)
- Interface: `CELEvaluator` class in Python (`api_parity/cel_evaluator.py`), calls subprocess

The Go subprocess is a focused helper behind a clean interface. The implementation detail (subprocess vs hypothetical future Python CEL library) is hidden from the rest of the codebase. See ARCHITECTURE.md "CEL Evaluator Component" for the IPC protocol.

---

# Pydantic v2 for Data Models

Keywords: pydantic dataclass model serialization validation schema json
Date: 20260111

Data models (RequestCase, ResponseCase, ChainCase, etc.) use Pydantic v2, not plain dataclasses or JSON Schema definitions.

**Why Pydantic over alternatives:**

| Requirement | Plain dataclasses | JSON Schema | Pydantic v2 |
|-------------|-------------------|-------------|-------------|
| Python objects | ✓ | ✗ (needs codegen) | ✓ |
| JSON serialization | Manual | N/A | Built-in |
| Validation on load | Manual | Separate library | Built-in |
| Computed fields | Property decorator | ✗ | `@computed_field` |
| Mutual exclusion | Manual | `oneOf` | `@model_validator` |

**Key features used:**

- `model_dump()` / `model_dump_json()` for artifact writing
- `Model.model_validate_json()` for replay bundle loading
- `@computed_field` for `rendered_path` (derived from template + parameters)
- `@model_validator` for mutual exclusion (`body` vs `body_base64`)
- Type hints for LLM agent ergonomics (immediate feedback on field types)

**CEL evaluator integration:** The CEL evaluator expects `dict[str, Any]` for data bindings. Pydantic's `model_dump()` produces this directly.

---

# Stdin/Stdout IPC for CEL Subprocess

Keywords: ipc stdin stdout pipes subprocess protocol ndjson unix sockets
Date: 20260110

The Python↔Go CEL evaluator uses stdin/stdout pipes with newline-delimited JSON (NDJSON), not Unix domain sockets.

**Why stdin/stdout over Unix sockets:**

| Criterion | Pipes | Unix Sockets |
|-----------|-------|--------------|
| Setup complexity | Low (subprocess.Popen) | Medium (socket file, connect polling) |
| Cleanup on crash | Automatic (process dies = EOF) | Manual (stale socket files) |
| Cross-platform | Excellent | Good (no abstract namespace on macOS) |
| Multi-client | No | Yes |
| Our requirement | Single client | Single client |

Sockets win for multi-client scenarios. We have single-client (one Python process, one Go subprocess), so pipes are simpler. Both sides buffer I/O—flush after each write. EOF signals subprocess crash.

See ARCHITECTURE.md "IPC Protocol" for the message format.
