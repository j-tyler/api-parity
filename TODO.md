# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

---

## Advanced Authentication Support

v0 supports simple header-based auth configured manually in runtime config (see ARCHITECTURE.md config example with `Authorization: "Bearer ${API_TOKEN}"`). Future work for advanced auth schemes:
- Token refresh for OAuth2
- Multiple auth schemes per target
- Auth scheme differences between targets (e.g., target A uses API key, target B uses OAuth2)

---

## Validate Schemathesis Link Support

Before implementation proceeds too far, validate that Schemathesis's OpenAPI link traversal meets our requirements:
- Can it handle 6-step chains?
- Does it correctly extract variables from responses and inject into subsequent requests?
- What happens when link definitions are incomplete?

---

## User Documentation for OpenAPI Links

Document the level of detail required in OpenAPI link definitions for effective chain testing. Users need to understand that sparse link definitions produce shallow chains.

---

## Specification Work Required

Several sections in ARCHITECTURE.md are marked [NEEDS SPEC] or [CONCEPT] and need design work before implementation. See ARCHITECTURE.md for details and context on each:

- **Data Models** — Schema format (JSON Schema, TypeScript, Python dataclasses?)
- **Mismatch Report Bundle / diff.json** — Diff library/algorithm, header comparison rules
- **Runtime Configuration / Comparison Rules** — Inheritance model, array ordering, field-level functions
- **Error Classification** — Edge cases like A=500/B=400
- **Stateful Chains / Variable Extraction** — How Schemathesis exposes link data
- **Stateful Chains / Replay Behavior** — Unique field regeneration mechanism
- **OpenAPI Spec as Field Authority** — JSON Schema validator choice, additionalProperties handling

---
