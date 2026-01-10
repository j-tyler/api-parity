# TODO

Tasks for future development. Add items here when you identify work that shouldn't be done immediately but shouldn't be forgotten.

---

## Advanced Authentication Support

v0 supports simple header-based auth configured manually in runtime config (see ARCHITECTURE.md config example with `Authorization: "Bearer ${API_TOKEN}"`). Future work for advanced auth schemes:
- Token refresh for OAuth2
- Multiple auth schemes per target
- Auth scheme differences between targets (e.g., target A uses API key, target B uses OAuth2)

---

## ~~Validate Schemathesis Link Support~~ (COMPLETED 20260108)

Validation complete. Key findings now documented in DESIGN.md and ARCHITECTURE.md.

Results:
- ✅ 6-step chains validated (70+ unique operation sequences)
- ✅ Variable extraction works via state machine bundles and link definitions
- ✅ Incomplete links = shorter chains (expected behavior)
- ✅ GenerationMode.POSITIVE produces schema-valid data only
- ⚠️ Schemathesis continues after errors by default; api-parity stops on mismatch (see DESIGN.md)

---

## User Documentation for OpenAPI Links

Document the level of detail required in OpenAPI link definitions for effective chain testing. Users need to understand that sparse link definitions produce shallow chains.

---

## Specification Work Required

Several sections in ARCHITECTURE.md are marked [NEEDS SPEC] and need design work before implementation:

- **Data Models** — Schema format (JSON Schema, TypeScript, Python dataclasses?)
- **Mismatch Report Bundle / diff.json** — Diff library/algorithm, header comparison rules
- **Error Classification** — Edge cases like A=500/B=400
- **Stateful Chains / Replay Behavior** — Unique field regeneration mechanism
- **OpenAPI Spec as Field Authority** — JSON Schema validator choice, additionalProperties handling

**Resolved:**
- ~~Runtime Configuration / Comparison Rules~~ — Now [SPECIFIED]. See DESIGN.md "Comparison Rules Format", "CEL as Comparison Engine", "Predefined Comparison Library". Prototype at `prototype/comparison-rules/`.
- ~~Stateful Chains / Variable Extraction~~ — Handled by state machine bundles
- ~~Stateful Chains / Link-Based Generation~~ — Now [SPECIFIED] in ARCHITECTURE.md

---

## ~~CEL Runtime Validation~~ (SUPERSEDED 20260110)

Decision made: Use cel-go via Go subprocess. Python CEL libraries are not an option due to untrusted dependency constraints.

See DESIGN.md "CEL Evaluation via Go Subprocess" and "Stdin/Stdout IPC for CEL Subprocess" for full design. See ARCHITECTURE.md "CEL Evaluator Component" for implementation details.

---

## CEL Evaluator Implementation

Implement the Go subprocess CEL evaluator and Python integration.

**Go side (`cmd/cel-evaluator/main.go`):**
1. Read JSON from stdin line-by-line using `bufio.Scanner`
2. Parse expression and data from request
3. Compile and evaluate CEL expression with cel-go
4. Write JSON result to stdout, flush after each line
5. Handle errors gracefully (return error response, don't crash)
6. Send `{"ready":true}` on startup

**Python side (`api_parity/cel_evaluator.py`):**
1. Spawn subprocess with `subprocess.Popen(stdin=PIPE, stdout=PIPE)`
2. Wait for ready handshake
3. Implement `evaluate(expr: str, data: dict) -> bool` method
4. Flush after each write, use readline for responses
5. Handle subprocess crash (EOF detection, automatic restart)
6. Implement `close()` for clean shutdown

**Testing:**
1. Unit tests for Go evaluator (stdin/stdout mocking)
2. Unit tests for Python CELEvaluator class
3. Integration test: Python → Go → Python round-trip
4. Test all predefined expressions from `comparison_library.json`

**Build/Distribution:**
1. Create `go.mod` with cel-go dependency
2. Add build instructions (Makefile or setup.py integration)
3. Decide: build from source on install vs pre-built binaries

See ARCHITECTURE.md "CEL Evaluator Component" for protocol details.

---

## Implementation Notes from Validation

Notes to remember when implementing (validated during Schemathesis prototype). API patterns and code examples are in DESIGN.md "Schemathesis as Generator (Validated)".

1. **Link provenance not exposed** — The `call()` method receives the case but not which link triggered it. Accept reporting "step N mismatch" rather than "link X failed."
2. **Duplicate chain sequences** — Hypothesis may generate duplicate operation sequences. Consider deduplication or increased `max_examples`.

---
