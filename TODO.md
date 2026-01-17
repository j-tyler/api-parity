# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

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
