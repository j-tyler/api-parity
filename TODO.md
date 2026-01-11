# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

---

## Advanced Authentication Support

v0 supports simple header-based auth configured manually in runtime config (see ARCHITECTURE.md config example with `Authorization: "Bearer ${API_TOKEN}"`). Future work for advanced auth schemes:
- Token refresh for OAuth2
- Multiple auth schemes per target
- Auth scheme differences between targets (e.g., target A uses API key, target B uses OAuth2)

---

## User Documentation for OpenAPI Links

Document the level of detail required in OpenAPI link definitions for effective chain testing. Users need to understand that sparse link definitions produce shallow chains.

---

## CLI Explore/Replay Implementation

The CLI argument parsing is complete, but `explore` and `replay` subcommands are placeholder stubs that print configuration and exit. Implementation requires:

1. Config file loading and merging with CLI args
2. Integration with Case Generator (Schemathesis)
3. Integration with Executor
4. Integration with Comparator and Artifact Writer

See ARCHITECTURE.md CLI Frontend [NEEDS SPEC] for design questions to resolve before implementation.

---

## Specification Work Required

- **OpenAPI Spec as Field Authority** — JSON Schema validator choice, additionalProperties handling

See ARCHITECTURE.md for component-level `[NEEDS SPEC]` items: CLI Frontend, Case Generator, Executor, Artifact Writer, Runtime Config Loading.

---

## Implementation Notes from Validation

Notes to remember when implementing (validated during Schemathesis prototype). API patterns and code examples are in DESIGN.md "Schemathesis as Generator (Validated)".

1. **Link provenance not exposed** — The `call()` method receives the case but not which link triggered it. Accept reporting "step N mismatch" rather than "link X failed."
2. **Duplicate chain sequences** — Hypothesis may generate duplicate operation sequences. Consider deduplication or increased `max_examples`.

---
