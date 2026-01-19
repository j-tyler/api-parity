# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

---

## CRITICAL: Incomplete API Coverage in Chain Generation

**Status:** BLOCKING - Project does not work at its core purpose until fixed.

**Problem:** The `explore` command (and `graph-chains --generated`) uses Schemathesis's state machine for chain generation, which does NOT guarantee that all API operations are tested. Testing on `archive_gateway_spec.yaml` shows:

- **7 of 27 operations (26%) are never called** with a single seed
- **4 operations remain unreachable** even after 20 different seeds
- **3 operations are completely invisible** to Schemathesis (no rules generated)

**Root Cause:** Schemathesis uses Hypothesis's state machine testing, which is designed for **bug hunting** (find interesting failures fast), not **coverage** (test every path). It:

1. Creates rules only for operations involved in links
2. Explores probabilistically, favoring "interesting" paths
3. Terminates early when it thinks it's seen enough (~150 chains regardless of `max_chains`)

**Impact:** If parts of the API surface cannot be tested, the project fails at its core mission of comparing API implementations.

**Minimum Requirement:** Every API operation must be called at least once, through at least one chain. This is mission critical.

**Research Findings (2026-01-19):**

Searched Schemathesis GitHub issues, Hypothesis documentation, and Schemathesis configuration docs:

1. **No Schemathesis option exists to guarantee coverage.** The "coverage phase" is about boundary value testing, not operation coverage. No `--ensure-all-operations` flag or similar.

2. **Hypothesis explicitly does NOT guarantee all rules execute.** From docs: "At any given point a random applicable rule will be executed." Initialize rules are the only exception (guaranteed once).

3. **Known issue acknowledged.** [GitHub Issue #1405](https://github.com/schemathesis/schemathesis/issues/1405) discusses this exact problem: "The current state machine-based implementation randomizes the tested rules... but this looks like something that users want to run on every endpoint every time." Issue remains open.

4. **Orphan operations are invisible.** Operations without links have no rules generated. Schemathesis only creates RANDOM rules for operations that can be entry points AND have outgoing links.

**Confirmed: Schemathesis cannot solve this problem through configuration.**

**Recommended Solution: Deterministic Path Enumeration**

Proof of concept shows we can enumerate all link paths from the spec:
- 21 deterministic paths cover all 72 link transitions in archive_gateway_spec
- Graph traversal from entry points guarantees every operation is reached
- Use Schemathesis ONLY for data generation (strategies), not state machine exploration

Implementation approach:
1. Build link graph from OpenAPI spec
2. Find entry points (operations without required path parameters)
3. BFS/DFS to enumerate paths covering all operations
4. For each path, use Schemathesis strategies to generate request data
5. Execute paths with synthetic responses (like current `_synthetic_response`)

This separates concerns: deterministic path selection (guarantees coverage) + fuzzy data generation (Schemathesis's strength).

**Investigation Date:** 2026-01-19

---

## Advanced Authentication Support

v0 supports simple header-based auth configured manually in runtime config (see ARCHITECTURE.md config example with `Authorization: "Bearer ${API_TOKEN}"`). Future work for advanced auth schemes:
- Token refresh for OAuth2
- Multiple auth schemes per target
- Auth scheme differences between targets (e.g., target A uses API key, target B uses OAuth2)

---

## Completed Features

### graph-chains: Show Actual Generated Chains [DONE]

Added `--generated` flag to `graph-chains` command that shows actual chains Schemathesis generates, not just the static link graph from the OpenAPI spec.

**Implemented:**
- `--generated` flag to show actual chains from `CaseGenerator.generate_chains()`
- `--max-chains` and `--max-steps` options to mirror `explore` command
- `--seed` option for reproducible chain generation
- Link coverage summary comparing declared vs used links
- Wildcard status code matching (2XX, default) for link lookup

### OpenAPI Spec as Field Authority [DONE]

Validates responses against the OpenAPI schema before comparison. See DESIGN.md and ARCHITECTURE.md for design decisions and implementation details.

**Implemented:**
- SchemaValidator component (`api_parity/schema_validator.py`)
- JSON Schema validation using `jsonschema` library (v4.23.0)
- `SCHEMA_VIOLATION` mismatch type
- Phase 0 validation in Comparator (before status code comparison)
- `additionalProperties` handling: `false` = violation, `true`/unspecified = allowed but compared
- Extra fields comparison with equality

---

## Implementation Notes from Validation

Notes to remember when implementing (validated during Schemathesis prototype). API patterns and code examples are in DESIGN.md "Schemathesis as Generator (Validated)".

1. **Link provenance not exposed** — The `call()` method receives the case but not which link triggered it. Accept reporting "step N mismatch" rather than "link X failed."
2. **Duplicate chain sequences** — Hypothesis may generate duplicate operation sequences. Consider deduplication or increased `max_examples`.

---
