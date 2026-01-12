# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

---

## Advanced Authentication Support

v0 supports simple header-based auth configured manually in runtime config (see ARCHITECTURE.md config example with `Authorization: "Bearer ${API_TOKEN}"`). Future work for advanced auth schemes:
- Token refresh for OAuth2
- Multiple auth schemes per target
- Auth scheme differences between targets (e.g., target A uses API key, target B uses OAuth2)

---

## CLI Replay Implementation

The `explore` subcommand is fully implemented for both stateless and stateful modes.

The `replay` subcommand remains a placeholder stub. Implementation requires:
1. Loading mismatch bundles from disk
2. Re-executing saved request cases
3. Comparing new responses to detect if mismatches persist

---

## Specification Work Required

- **OpenAPI Spec as Field Authority** — JSON Schema validator choice, additionalProperties handling
- **Rate Limiting** — Executor does not implement rate limiting in v0

---

## Implementation Notes from Validation

Notes to remember when implementing (validated during Schemathesis prototype). API patterns and code examples are in DESIGN.md "Schemathesis as Generator (Validated)".

1. **Link provenance not exposed** — The `call()` method receives the case but not which link triggered it. Accept reporting "step N mismatch" rather than "link X failed."
2. **Duplicate chain sequences** — Hypothesis may generate duplicate operation sequences. Consider deduplication or increased `max_examples`.

---
