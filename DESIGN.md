# Design Decisions

This document records architectural and design decisions made in the api-parity project. It preserves historical reasoning so AI agents and future contributors don't retread the same ground.

---

# Design Decision Format

Keywords: format structure template decision record
Date: 20260108

Design decisions use a simple, grep-friendly format. Each decision starts with an H1 heading containing a descriptive name. Below that, a Keywords line lists searchable terms for finding relevant decisions when this document grows large. The Date line uses YYYYMMDD format for easy chronological sorting. Finally, one or more paragraphs explain the problem, the decision made, and the reasoning behind it.

This format was chosen over more structured alternatives (like ADR templates with separate Context/Decision/Rationale sections) because it's more natural to write and read. The keywords line solves discoverability, the date provides ordering, and free-form paragraphs let the author explain things in whatever way makes sense for that particular decision.

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

Default error handling: 500-class errors are not recorded as mismatches (assumed transient/infrastructure). Differing 400-class errors between targets are recorded (indicates behavioral difference). Users can override these defaults in configuration. This prevents infrastructure noise from polluting mismatch artifacts while capturing meaningful client-error divergence. Note: Edge case where A returns 500 and B returns 400 is unresolved; see ARCHITECTURE.md "Error Classification" open questions.

---

# Eventual Consistency Out of Scope

Keywords: eventual consistency timing async replication
Date: 20260108

Eventually consistent APIs (where GET may return stale data for some time after CREATE/UPDATE) are not supported in v0. The tool assumes responses are immediately consistent. This simplifies comparison logic significantly. If eventual consistency becomes a requirement, we may add retry/polling mechanisms or per-endpoint timing tolerances later.

---

# Exit Code Conventions

Keywords: exit codes CLI return status
Date: 20260108

Exit codes follow Unix conventions: 0 for success (no mismatches), 1 for mismatches found, 2 for tool error. The original design proposed swapping 1 and 2, but this conflicts with standard tools like diff and grep. Following conventions reduces surprise for users integrating with CI/scripts.

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

Chains are generated live, not pre-generated offline. Each target executes the same sequence of operations but uses its own extracted response data for subsequent steps. If A's POST returns id "abc" and B's returns "xyz", A's chain continues with "abc" while B's continues with "xyz".

This was chosen over offline generation because chain steps depend on runtime response data (e.g., created resource IDs). Pre-generating chains would require mocking responses, which defeats the purpose of differential testing against real APIs.

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
