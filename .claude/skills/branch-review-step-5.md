# Step 5: Retracted Review

For each issue you RETRACTED, consider whether a token-efficient refinement could prevent similar misunderstandings.

**Options to consider (in order of preference):**
1. **Inline comment** — brief WHY explanation near code where the WHY is not obvious just by reading
2. **Code change** — rename variables/functions so related concepts are easier to connect or less likely to be confused with unrelated concepts (e.g., `getABC()` not `get()`)
3. **Docstring update** — clarify non-obvious behavior
4. **Project .md file** — only if it affects multiple components

**For each retracted issue, output one of:**

- **REFINE**: [specific change] — [why it helps]
- **NO CHANGE**: [why the misunderstanding was reviewer error, not a code/doc deficiency]

Do not bloat docs or complicate code. Only suggest refinements where a change will reduce chance of confusion for the next agent who won't have your context window.

---

## Propose Changes

Present a summary of all proposed changes to the user:

1. Issues you DOUBLED DOWN on and the specific fix for each
2. Items you identified as REFINE and the specific improvement for each

**STOP and wait for user approval before making any changes.** Do not edit or write any files until the user confirms which changes to proceed with.
