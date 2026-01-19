# Design Decisions

This document records architectural and design decisions made in the api-parity project. It preserves historical reasoning so AI agents and future contributors don't retread the same ground.

---

# Design Decision Format

Keywords: format structure template decision record
Date: 20260108

Each decision: H1 heading, Keywords line (for grep), Date (YYYYMMDD), then free-form paragraphs explaining problem/decision/reasoning. Chosen over structured ADR templates for natural readability.

---

# Hypothesis Seed Control via Internal Attribute

Keywords: hypothesis seed derandomize reproducibility internal random
Date: 20260119

Controlling Hypothesis randomness for dynamically-defined test functions requires setting `_hypothesis_internal_use_seed` on the function object in addition to `derandomize=True`. The `@seed()` decorator cannot be used because the test function is defined at runtime inside `generate()` and `generate_chains()`.

The key insight: `derandomize=True` alone makes ALL seed values produce identical results—it removes randomness but doesn't differentiate between seeds. The `_hypothesis_internal_use_seed` attribute is what Hypothesis's `@seed()` decorator sets internally to control which deterministic sequence is generated.

This is an internal Hypothesis API (note the `_hypothesis_internal_` prefix). It may change in future Hypothesis versions. If seed reproducibility breaks after a Hypothesis upgrade, check if this attribute was renamed or replaced.

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

# Handling additionalProperties in Schema Validation

Keywords: additionalProperties schema validation extra fields json-schema
Date: 20260114

When validating responses against the OpenAPI spec, respect the `additionalProperties` setting:

1. **`additionalProperties: false`** — Extra fields are a schema violation error. The spec explicitly forbids them.

2. **`additionalProperties: true`** — Extra fields are allowed per schema, but still compared between implementations. The spec says "extra fields are OK" but doesn't say "extra fields can differ between A and B."

3. **Not specified (default)** — Treat as `true` per JSON Schema default. Be lenient on schema validation but still compare extras between implementations.

This distinction matters because schema validity and implementation equivalence are separate concerns. A response can be valid (matches schema) but not equivalent (differs from other implementation). Example: if the spec allows extras and Target A returns `{id: 1, debug: "foo"}` while Target B returns `{id: 1, trace_id: "bar"}`, both are schema-valid but they're not equivalent—the LLM agent should know about this divergence.

For extra fields (not defined in spec), use equality comparison by default.

---

# jsonschema Library for Schema Validation

Keywords: jsonschema schema validation library python implementation
Date: 20260114

Chose `jsonschema` (v4.23.0) over alternatives for OpenAPI schema validation:

1. **Well-established** — Mature library with broad adoption and good documentation
2. **OpenAPI compatibility** — OpenAPI 3.x uses JSON Schema Draft 4-7, which jsonschema supports
3. **Error details** — Provides detailed error paths and messages for violations
4. **Trusted dependency** — Unlike some CEL Python libraries, jsonschema is a reliable package

Considered alternatives:
- `fastjsonschema` — Faster but less compatible with OpenAPI schemas
- Built-in `schemathesis` validation — Doesn't expose raw validation, only pass/fail

Schema validation is optional (enabled when SchemaValidator is passed to Comparator) to support replay mode which doesn't have the OpenAPI spec available.

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

# Explicit Links Only for Chain Generation

Keywords: stateful chains links inference algorithms schemathesis explicit
Date: 20260117

Chain generation uses only explicit OpenAPI links, not inferred relationships. Schemathesis 4.9+ includes inference algorithms that can discover chains from parameter name matching (e.g., POST /users returns `id`, GET /users/{userId} has matching parameter) or Location headers (HTTP 201 responses with Location header). These are explicitly disabled.

**Why disable inference:**

1. **Documented contracts only** — Inferred relationships may not reflect actual API contracts. A parameter named `userId` might accept a user ID, an admin ID, or a session ID. Only explicit links document the intended relationship.

2. **Predictable test coverage** — Users can look at their OpenAPI links to understand exactly which chains will be tested. Inference adds hidden test paths that aren't visible in the spec.

3. **Avoid false positives** — Inferred chains might exercise invalid transitions (e.g., using an order ID where a user ID is expected because both are UUIDs). These failures are noise, not real parity issues.

4. **Spec quality signal** — If the OpenAPI spec lacks links, that's a spec quality issue that should be fixed. Inference papers over incomplete specs.

**Implementation:** Schemathesis config disables `LOCATION_HEADERS` and `DEPENDENCY_ANALYSIS` inference algorithms. See `_create_explicit_links_only_config()` in `case_generator.py`.

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

---

# jsonpath-ng for JSONPath Extraction

Keywords: jsonpath jsonpath-ng jmespath extraction body fields
Date: 20260111

JSONPath extraction uses the `jsonpath-ng` library. Alternatives considered:

| Library | Pros | Cons |
|---------|------|------|
| jsonpath-ng | Full JSONPath spec, wildcards, filters | Heavier dependency |
| jmespath | Fast, AWS-backed | Different syntax (not JSONPath) |
| Manual | No dependency | Limited features, maintenance burden |

Chose jsonpath-ng because: (1) it implements the standard JSONPath syntax users expect, (2) supports all wildcard patterns (`[*]`, `..`, `[?()]`, slices), (3) provides parse-once-execute-many for caching compiled paths.

The Comparator caches compiled JSONPath expressions per path string. Cache size is bounded by the number of unique paths in user configuration.

---

# CEL Errors Treated as Mismatches

Keywords: cel error handling mismatch exception propagation
Date: 20260111

When CEL expression evaluation fails (syntax error, type error, unknown function), the Comparator records this as a mismatch with `rule: "error: <message>"` rather than raising an exception.

Rationale:
1. **Fail-safe behavior** — A broken comparison rule shouldn't crash the entire run. Record the error, continue comparing other fields.
2. **Visibility** — Errors appear in mismatch artifacts alongside real mismatches, making them visible for debugging.
3. **Consistency** — Both configuration errors (unknown predefined) and runtime errors (CEL type mismatch) are handled the same way.

This is distinct from `CELSubprocessError` (Go process crashed), which does propagate as an exception since it indicates infrastructure failure, not configuration/data issues.

---

# CLI Arguments Override Config File

Keywords: cli config precedence override arguments runtime
Date: 20260111

Every configuration option can be specified in the configuration file. If the same option is also passed as a CLI argument, the CLI argument takes precedence for that run.

**Why this design:**
- Config files capture stable, shared defaults (target URLs, auth headers, comparison rules)
- CLI flags enable one-off overrides without editing files (different seed, limited cases for debugging)
- Standard pattern familiar from tools like docker, kubectl, terraform

**Precedence order (highest to lowest):**
1. CLI arguments
2. Config file values
3. Built-in defaults

**Example:** Config file specifies `max_cases: 10000`. Running with `--max-cases 10` overrides just for that run. The config file remains unchanged.

**Implementation note:** Parse CLI args, load config file, merge with CLI taking precedence, validate the merged result. Validation happens once on the merged config, not separately.

---

# Session-Scoped Test Server Fixtures

Keywords: pytest fixtures session fixture_dual_mock_servers integration tests performance
Date: 20260112

The `fixture_dual_mock_servers` pytest fixture is session-scoped, meaning mock servers start once per test session rather than once per test function.

**Trade-off:** Test speed (~50% faster) vs test isolation (shared server state).

**Why this is acceptable:** Integration tests check CLI output strings (MATCH/MISMATCH), not server database contents. Tests don't assert on exact widget counts or depend on pristine server state. Widget accumulation from previous tests doesn't affect correctness.

**If isolation becomes necessary:** Either add a `POST /reset` endpoint to the mock server, or create a separate function-scoped fixture for tests that need pristine state.

---

# Dynamic Link Field Extraction

Keywords: links openapi fields extraction chains stateful dynamic parsing
Date: 20260112

Link field names are dynamically parsed from OpenAPI link expressions rather than hardcoded. At initialization, CaseGenerator extracts all `$response.body#/...` references from the spec and stores the JSONPointer paths (e.g., `id`, `resource_uuid`, `data/nested_id`). These paths are used in two places:

1. **Chain discovery** — Synthetic responses include placeholder values for all referenced fields so Schemathesis can resolve link expressions and discover chain paths.

2. **Chain execution** — The Executor receives the parsed `link_fields` set and extracts those specific fields from real responses for variable substitution in subsequent chain steps.

This design enables stateful chain testing with arbitrary field names. An API using `resource_uuid` instead of `id` works without configuration changes. The alternative (hardcoding common field names like `id`, `user_id`, `created_at`) would fail silently for any API using non-standard names.

**Implementation note:** The OpenAPI spec is parsed twice at startup—once by Schemathesis (for generation) and once manually (for link extraction). Schemathesis doesn't expose the raw spec dict through its API, so separate parsing is required. The cost is minimal (one extra file read at startup).

---

# Source-Only Distribution

Keywords: distribution packaging pypi installation build binary go cel
Date: 20260112

api-parity is distributed as source code only, not as a PyPI package. Users clone the repository and run `./scripts/build.sh` to install.

**Why not PyPI:**

1. **Go binary requirement** — The CEL evaluator is a Go binary that must be compiled for the user's platform. PyPI wheels can include platform-specific binaries, but this requires maintaining build infrastructure for Linux x64, macOS x64/arm64, Windows x64, and potentially more. This complexity isn't justified for a tool with a small user base.

2. **Simple alternative exists** — Users who can install Go (required for any serious backend work) can build from source in under a minute. The `build.sh` script handles everything.

3. **Dependency on Go at build time, not runtime** — Once built, the tool runs with Python + the compiled binary. Go is only needed once.

**Trade-offs accepted:**

- Higher friction for first-time installation (must have Go installed)
- No `pip install api-parity` convenience
- Users must `git pull` and rebuild for updates

**If adoption grows significantly**, reconsider pre-built binaries distributed via GitHub Releases (download binary matching platform, place in PATH). This is simpler than full PyPI wheel infrastructure.

---

# Pinned Dependencies for Reproducible Builds

Keywords: dependencies versions pinning reproducible builds pip go modules
Date: 20260112

All dependencies are pinned to exact versions. This is critical for a build-from-source project where users build at different times.

**Where pins are defined:**
- Python: `pyproject.toml` uses `==` version specifiers
- Go: `go.mod` specifies exact versions, `go.sum` contains cryptographic checksums

**Why exact pins, not ranges:**

1. **Reproducibility** — User building today gets same behavior as user building next month. Range specifiers (`>=4.8.0`) can silently pull newer versions with breaking changes.

2. **Debuggability** — When a user reports a bug, we know exactly what versions they have. No "what version of pydantic did pip resolve?" ambiguity.

3. **Security auditability** — Pinned versions can be scanned for known vulnerabilities. Floating ranges make auditing impossible.

4. **Build-from-source contract** — Since we don't publish binaries, the source repository IS the distribution. It must be self-contained and deterministic.

**Update process:**

1. Create branch for dependency updates
2. Update pins in `pyproject.toml` and/or `go.mod`
3. Run `go mod tidy` for Go
4. Run full test suite
5. If tests pass, merge and document changes

**Trade-offs accepted:**

- Manual effort to update dependencies
- Users don't automatically get security patches (must pull new version)
- Potential for stale dependencies if not actively maintained

The reproducibility guarantee outweighs these costs for a build-from-source tool.

---

# Binary Body Comparison via CEL

Keywords: binary body comparison base64 octet-stream non-json
Date: 20260117

Binary (non-JSON) response bodies are compared using the same CEL infrastructure as JSON fields. Binary content is stored as base64-encoded strings (already implemented in the Executor), and comparisons operate on these base64 strings.

**Why base64 strings for comparison:**

1. **Serialization requirement** — JSON doesn't support raw bytes, so mismatch bundles require encoding. Base64 is already used for storage.

2. **Reuse existing infrastructure** — CEL can compare strings. No new evaluation engine needed.

3. **Consistency** — Same comparison model as JSON fields: predefined rules or custom CEL expressions.

**Trade-offs accepted:**

1. **Unintuitive string operations** — `string_prefix` on base64 doesn't mean "first N bytes match" due to encoding boundaries. Users wanting byte-level operations should use `binary_exact_match` and accept all-or-nothing comparison.

2. **No hash comparison** — CEL doesn't have hashing functions. For large files where hash comparison would be efficient, users must accept comparing full base64 strings.

3. **Memory for large binaries** — Base64 is 33% larger than raw bytes. Very large files will consume proportionally more memory during comparison.

4. **Empty string is "non-empty" in CEL** — `size("")` returns 0, so `binary_nonempty` passes only for actual content. An empty `body_base64` field (`""`) is distinct from a missing field (`None`). In practice, responses with `Content-Length: 0` may have `body_base64: ""` rather than `body_base64: None`.

**Default behavior:** If `binary_rule` is not specified, binary bodies are not compared (match by default). This preserves backward compatibility—existing configs won't suddenly fail on binary endpoints. Users must explicitly configure `binary_rule` to enforce binary parity.

**Alternative considered:** Python-level byte comparison (decode base64, compare bytes). This would enable hash-based and byte-prefix comparisons but requires a separate code path outside CEL. The CEL approach was chosen for simplicity and consistency.

---

# Header-Based OpenAPI Link Support

Keywords: header link Location response chain stateful extraction array index
Date: 20260117

OpenAPI link expressions can reference values from response headers (`$response.header.Location`) in addition to body fields (`$response.body#/id`). The original implementation only supported body expressions, silently ignoring header links. This prevented chain generation through common patterns like `POST /resources` returning a `Location` header with the created resource URL.

**Design decisions:**

1. **Extracted key format:** Header values use `header/{name}` keys for all values as a list, and `header/{name}/{index}` for specific indexed access. This parallels body array semantics where `items` returns the array and `items/0` returns the first element.

2. **Case normalization:** Per HTTP spec (RFC 7230), header names are case-insensitive. Header names are normalized to lowercase when storing and matching. Both `$response.header.Location` and `$response.header.location` resolve to `header/location`.

3. **Multi-value headers with array semantics:** HTTP headers can have multiple values. Headers support the same array access semantics as body fields:
   - `$response.header.Set-Cookie` → extracts all values as list, stored at `header/set-cookie`
   - `$response.header.Set-Cookie[0]` → extracts first value, stored at `header/set-cookie/0`
   - `$response.header.Set-Cookie[1]` → extracts second value, stored at `header/set-cookie/1`

4. **HeaderRef dataclass:** Header references are stored as `HeaderRef` objects with `name` (lowercase for HTTP extraction), `original_name` (spec case for synthetic header generation), and optional `index`. Both name fields are required—`original_name` enables Schemathesis to resolve links during chain discovery.

5. **Synthetic headers in chain discovery:** During chain generation, `_synthetic_response()` generates synthetic header values for all referenced headers. For multi-value header access, generates enough synthetic values to satisfy the maximum requested index. All headers use generic placeholder values—no format assumptions are made (see "Schema-Driven Synthetic Value Generation" below).

6. **Expression storage in link_source:** The `_find_link_between()` function stores the original expression (`$response.body#/path` or `$response.header.HeaderName[index]`) in `link_source.field`. This enables replay to correctly identify extraction source and array index, even without the OpenAPI spec.

---

# Schema-Driven Synthetic Value Generation

Keywords: synthetic values format schema openapi headers body chain generation
Date: 20260118

**Status:** Phase 1 complete. Phase 2 (schema-aware generation) now implemented—see "Schema-Aware Synthetic Value Generation (Phase 2)" below.

Synthetic response generation during chain discovery must NOT assume any format for values. The OpenAPI spec is the sole authority on formats. The previous implementation made several assumptions that broke non-standard APIs:

1. **Location header assumed URL format** — Special-cased to generate `http://placeholder/resource/{uuid}`, but some APIs use non-URL formats (e.g., `/AAYAAhZP...` path-only values)
2. **All body fields assumed UUID format** — Generated `str(uuid.uuid4())` for all link-referenced fields regardless of actual type
3. **Hardcoded common field names** — Added `name`, `price`, `status`, `total`, `created_at`, `items` regardless of actual response schema

**Problem:** These assumptions work for "standard" REST APIs but fail for APIs that use different conventions. An API that uses `Location: /AAYAAhZP...` (path-only) instead of `Location: https://api.example.com/resources/uuid` would fail schema validation.

**Principle: Assume no format.** api-parity does not assume any format for any value. The OpenAPI spec defines formats; we just need placeholder values that Schemathesis can extract during chain discovery.

Note: The synthetic response uses `content-type: application/json` internally. This is NOT an assumption about the API's content type—it's a Schemathesis requirement for parsing the synthetic body dict. Actual API content types come from real HTTP responses during execution.

**Solution: Derive all formats from the OpenAPI spec.**

The OpenAPI spec already contains format information:
- Response schemas define field names, types, and formats (`format: uuid`, `format: uri`, `format: date-time`)
- Response headers section specifies header names and their schemas
- `pattern` fields provide regex patterns for validation

**Implementation approach (phased):**

**Phase 1 (Immediate): Remove special-casing**
- Remove the `Location` header special-case in `_generate_synthetic_headers()`
- Remove hardcoded field names (`name`, `price`, `status`, etc.) from `_generate_synthetic_body()`
- Use a generic non-empty placeholder (`uuid.uuid4()`) for all synthetic values
- We don't claim this is the "correct format" - it's just a placeholder that Schemathesis can extract
- The actual format is defined by the OpenAPI spec, not by this code

**Phase 2 (Future): Schema-aware generation**
When time permits, implement full schema-driven synthetic generation:
1. Extract response schema for the operation being synthesized
2. For each link-referenced body field, look up its schema and generate appropriate value:
   - `format: uuid` → Generate UUID
   - `format: uri` → Generate valid URI
   - `format: date-time` → Generate ISO timestamp
   - `type: integer` → Generate integer
   - `type: string` + `pattern` → Generate matching string (if possible)
   - Default → Generic placeholder
3. For each link-referenced header, look up its schema in response headers section

**Phase 2 dependencies:**
- Requires passing spec schema access to synthetic generation
- May need to parse response schemas from paths → responses → content → schema
- SchemaValidator already has this logic; could refactor for reuse

**Trade-offs:**

1. **Phase 1 trade-off:** Generic placeholders may not match some schema patterns (e.g., numeric IDs, specific patterns). This is acceptable because:
   - Synthetic values are placeholders for chain discovery, not real data
   - Schemathesis only needs to find the field and extract its value
   - Actual values come from real HTTP execution in the Executor
   - The format is defined by the spec, not assumed by our code

2. **Phase 2 complexity:** Full schema-aware generation adds complexity but provides:
   - Better compatibility with strict schema validation during chain discovery
   - More realistic synthetic data for edge cases

**Files affected:**
- `api_parity/case_generator.py` — `_generate_synthetic_headers()`, `_generate_synthetic_body()`
- Tests in `tests/integration/test_stateful_chains.py` — Update assertions if they check specific formats

---

# Schema-Aware Synthetic Value Generation (Phase 2)

Keywords: synthetic values schema enum chain generation constraint validation
Date: 20260118

**Phase 2 implemented:** Full schema-aware synthetic value generation now produces values that satisfy OpenAPI schema constraints during chain discovery.

**Problem solved:** Chain discovery fails when link-extracted values must satisfy target parameter constraints (especially enums). For example, an API with `library_id` constrained to `enum: [main_branch, downtown_branch]` would reject UUID placeholders, preventing chain discovery through that endpoint.

**Solution:** The `SchemaValueGenerator` class generates values satisfying schema constraints in priority order:

1. `enum` - return first enum value (highest priority)
2. `const` - return const value
3. `format: uuid` - generate UUID
4. `format: date-time` - generate ISO timestamp
5. `format: date` - generate ISO date
6. `format: uri` - generate placeholder URI
7. `format: email` - generate placeholder email
8. `type: integer` - return 1
9. `type: number` - return 1.0
10. `type: boolean` - return True
11. `type: string` - generate UUID string
12. `type: array` - generate single-item array
13. `type: object` - generate empty dict
14. Fallback - UUID string

**Key design decisions:**

1. **Enum always wins** - Enum constraint takes absolute priority over all other constraints. An enum field with `format: uuid` uses the enum value, not a generated UUID.

2. **Schema navigation via JSONPointer** - `navigate_to_field()` traverses object properties and array items following JSONPointer paths. Arrays are treated as homogeneous (index doesn't matter for schema lookup).

3. **$ref resolution** - References are resolved during navigation and generation, with cycle detection to prevent infinite recursion on circular schemas.

4. **Graceful fallback** - When no schema is found (operation not in spec, field not in response schema), falls back to UUID generation. This preserves backward compatibility with specs that don't define response schemas.

5. **First enum value used** - For determinism, always uses the first enum value. This is predictable and sufficient for chain discovery (Schemathesis just needs a valid value to resolve the link).

**Files affected:**
- `api_parity/schema_value_generator.py` — New module with `SchemaValueGenerator` class
- `api_parity/case_generator.py` — Uses schema-aware generation in `_generate_synthetic_body()`
- `tests/test_schema_value_generator.py` — Unit tests for all constraint types
- `tests/integration/test_enum_chain_generation.py` — Integration tests with enum-constrained spec
- `tests/fixtures/enum_chain_spec.yaml` — Test fixture demonstrating enum constraints in chains

---

# Spec-Based Status Code for Synthetic Responses

Keywords: status code synthetic response chain generation links PUT DELETE 201 202
Date: 20260118

During chain discovery, synthetic responses must use the correct status code to enable Schemathesis to find OpenAPI links. The original implementation used hardcoded logic: `status_code = 201 if case.method == "POST" else 200`. This broke chain discovery for operations with links on non-200 status codes.

**Problem scenarios:**
- PUT with links on 201 (update returns created resource)
- DELETE with links on 202 (async deletion returns status tracking)

When the synthetic response used 200, `_find_link_between()` couldn't find links defined on 201/202, breaking chain continuation.

**Solution:** The `_find_status_code_with_links()` method examines the OpenAPI spec to find which status codes have links defined for each operation. It returns the lowest 2xx status code with links, falling back to the original logic (201 for POST, 200 otherwise) when no links are found.

**Key behaviors:**
1. Searches all 2xx responses for the operation looking for links
2. Handles wildcard status codes like "2XX" (uses representative value 200)
3. Ignores "default" responses (could be any status)
4. Returns the minimum 2xx code with links (prefers lower codes for consistency)
5. Falls back to 201/200 when operation has no links or isn't found in spec

**Files affected:**
- `api_parity/case_generator.py` — Added `_find_status_code_with_links()` method in `ChainCapturingStateMachine`

---

# Link Attribution Searches Full Chain History

Keywords: _find_link_between, chain history, link attribution, prev_steps
Date: 20260118

Schemathesis can use extracted variables from ANY previous step when resolving links, not just the immediately previous step. The original implementation only checked the single previous operation for a matching link, causing valid links from earlier steps to be reported as "via unknown link (not in spec)" in `graph-chains --generated` output.

**Example:** In a chain `createOrder -> getOrderStatus -> updateOrder`, if `createOrder` has a link to `updateOrder` using `$response.body#/order_id`, and `getOrderStatus` does NOT link to `updateOrder`, the old code would fail to find the link because it only checked `getOrderStatus` (the immediately previous step).

**Solution:** Changed `_find_link_between()` to accept the full list of previous `(operation_id, status_code)` tuples and search them all, most recent first. This correctly attributes links regardless of how many steps intervene between source and target.

**Key behaviors:**
1. Maintains `prev_steps` list of all previous operations and their status codes
2. Searches in reverse order (most recent first) since links are more likely from recent operations
3. Returns on first matching link found
4. Falls back to `None` if no explicit link exists from any previous step

**Files affected:**
- `api_parity/case_generator.py` — Refactored `_find_link_between()` to accept chain history
