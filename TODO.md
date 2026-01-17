# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

---

## graph-chains: Show Actual Generated Chains

The current `graph-chains` command shows the static link graph extracted from the OpenAPI spec—which operations define links to which other operations. This does NOT show what chains Schemathesis will actually generate during `explore --stateful`.

**Gap:** The link graph shows potential connections, but Schemathesis chain generation involves:
- Hypothesis exploration of the state space
- Parameter generation and link variable substitution
- Filtering based on response status codes
- Chain depth limits (`--max-steps`)

A spec might define links that Schemathesis cannot actually traverse (e.g., link parameters don't match, response schemas don't provide expected fields). Users debugging "why doesn't explore find chains?" need to see what Schemathesis actually generates, not just what the spec declares.

**Proposed enhancement:** Add `--generated` flag to `graph-chains` that:
1. Uses `CaseGenerator.generate_chains()` to produce actual chains
2. Shows the generated chain sequences (not just the link graph)
3. Highlights which links were actually used vs. declared but unused

This would provide visibility into the gap between "spec says X" and "Schemathesis does Y".

---

## Advanced Authentication Support

v0 supports simple header-based auth configured manually in runtime config (see ARCHITECTURE.md config example with `Authorization: "Bearer ${API_TOKEN}"`). Future work for advanced auth schemes:
- Token refresh for OAuth2
- Multiple auth schemes per target
- Auth scheme differences between targets (e.g., target A uses API key, target B uses OAuth2)

---

## Completed Features

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
