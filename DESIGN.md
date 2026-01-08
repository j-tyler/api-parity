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
