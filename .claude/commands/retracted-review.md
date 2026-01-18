For each issue you RETRACTED, consider whether a **token-efficient refinement** could prevent similar misunderstandings:

**Options to consider (in order of preference):**
1. **Inline comment** — brief WHY explanation near code which has a WHY which is not obvious just by reading the code
2. **Code change** — rename variables/functions so related concepts are easier to connect or so they are less likely to be confused with unrelated concepts and files (e.g., `getABC()` not `get()`), restructure logic, add assertion
3. **Docstring update** — clarify non-obvious behavior
4. **Project .md file** — only if it affects multiple components

**For each retracted issue, output one of:**

- **REFINE**: [specific change] — [why it helps]
- **NO CHANGE**: [why the misunderstanding was reviewer error, not code/doc deficiency]

We DO NOT want to bloat docs or complicate code. Only suggest refinements where the confusion was reasonable given the current state of the code/docs.
