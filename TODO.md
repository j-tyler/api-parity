# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

---

## Chain Coverage: Behavior and Spec Design Guidance

**Status:** Understood. Not a blocking issue — coverage is achievable with seed walking + `--ensure-coverage`. Document what spec authors should avoid.

**Investigation Date:** 2026-02-07

### How Chain Coverage Actually Works

Schemathesis generates ~150-200 chains per seed regardless of the `max_chains` parameter (Hypothesis treats `max_examples` as adaptive, not a hard cap). Coverage of linked operations depends on spec structure:

| Spec complexity | Seeds for full linked coverage |
|-----------------|-------------------------------|
| Small (6 linked ops, well-connected) | 1 seed, every time |
| Medium (14 linked ops, depth 3-4) | 1-4 seeds |
| Hard (18 linked ops, depth 5 linear chain) | 5-20 seeds, high variance |

### Two Categories of Uncovered Operations

**1. Orphan operations (no link involvement):** These get NO state machine rules and are completely invisible to chain generation. An operation is an orphan if it is neither the source nor the target of any OpenAPI link. The ONLY solution is `--ensure-coverage`, which runs single-request tests for uncovered operations. Examples: `GET /health`, `GET /search`, list endpoints with no outbound links.

**2. Deep-chain operations (linked but at depth 5+):** These HAVE state machine rules and ARE reachable, but require Hypothesis to randomly choose the right sequence of 5+ transitions. Schemathesis's free transitions (jumping to any operation without a link) make them reachable at any depth, but coverage is probabilistic and may take many seeds. The existing seed-walking feature handles this.

### What Spec Authors Should Avoid

**Avoid orphan operations.** Every operation should participate in at least one link — either as a source (has outbound links) or a target (another operation links to it). Operations with zero link involvement are invisible to stateful testing.

Common orphans and how to fix them:

| Orphan pattern | Fix |
|----------------|-----|
| `GET /items` (list endpoint, no links) | Add a link from a create/update response: `ListItems: {operationId: listItems}` |
| `GET /health` (utility endpoint) | Accept as orphan; `--ensure-coverage` handles it |
| `GET /search?q=...` (standalone query) | Add as link target from a create operation, or accept as orphan |

**Avoid deep linear chains without shortcuts.** If operation E is only reachable via A→B→C→D→E (depth 5), add a shortcut link from A→E or B→E to reduce depth. The `lint-spec` command's `deep-chain-depth-3` and `deep-chain-depth-4-plus` warnings identify these. Use `--ensure-coverage` as a fallback.

**Avoid assuming `max_chains` controls chain count.** Hypothesis generates ~150-200 chains per seed regardless. The parameter affects Hypothesis's adaptive algorithm but does not cap output. Don't set it low expecting fewer chains, and don't set it high expecting more.

### Evidence

Prototyped in `prototype/coverage-analysis/` with three specs (small, medium, hard). Key files:
- `measure_coverage.py` — Measures seeds-to-full-coverage across trials
- `test_parameters.py` — Tests effect of max_chains and max_steps
- `analyze_rules.py` — Confirms which operations get state machine rules
- `medium_api_spec.yaml` — 18-operation CRUD+workflow spec (depth 4)
- `hard_api_spec.yaml` — 19-operation spec with depth-5 chain, 8-way fan-out, non-standard status codes, isolated clusters

Findings confirmed by inspecting Schemathesis's state machine rule generation: operations get rules if and only if they participate in at least one link (as source or target).

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
