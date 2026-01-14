# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

---

## Advanced Authentication Support

v0 supports simple header-based auth configured manually in runtime config (see ARCHITECTURE.md config example with `Authorization: "Bearer ${API_TOKEN}"`). Future work for advanced auth schemes:
- Token refresh for OAuth2
- Multiple auth schemes per target
- Auth scheme differences between targets (e.g., target A uses API key, target B uses OAuth2)

---

## Specification Work Required

### OpenAPI Spec as Field Authority

Validate responses against the OpenAPI schema before comparison. Design decisions are in DESIGN.md.

**Implementation tasks:**

1. **Choose JSON Schema validator** — Evaluate Python libraries (jsonschema, fastjsonschema, etc.) for OpenAPI 3.x compatibility and performance

2. **Extract response schema from spec** — For each operation+status_code, get the response schema from the OpenAPI spec

3. **Validate both responses** — Check each response against schema, report `schema_violation` category for failures

4. **Respect `additionalProperties`:**
   - `false` → Extra fields are schema violations
   - `true` or unspecified → Allow extras, but still compare them between A and B

5. **Compare extra fields** — Fields present in response but not in spec schema: compare with equality by default, allow user rules to override

6. **New mismatch categories:**
   - `schema_violation` — Response doesn't match spec (separate from comparison mismatch)
   - Existing `body` category for comparison mismatches between implementations

---

## Implementation Notes from Validation

Notes to remember when implementing (validated during Schemathesis prototype). API patterns and code examples are in DESIGN.md "Schemathesis as Generator (Validated)".

1. **Link provenance not exposed** — The `call()` method receives the case but not which link triggered it. Accept reporting "step N mismatch" rather than "link X failed."
2. **Duplicate chain sequences** — Hypothesis may generate duplicate operation sequences. Consider deduplication or increased `max_examples`.

---
