---
name: main-coder
description: Orchestrated implementation workflow with coder agent and multi-layer review
---

# Main Coder Workflow

The user has requested implementation work. Follow this workflow precisely.

## Task from User
{{args}}

---

## Phase 1: Planning

Develop a high-level implementation plan based on:
- The user's request above
- Current session and codebase context
- Existing patterns and architecture

The plan should include:
- Clear implementation steps
- Files likely to be created/modified
- Key decisions to make
- Potential risks or concerns

---

## Phase 2: Delegate to Coder Agent

Spawn the `coder` agent with your implementation plan.

**Instructions to include in the prompt:**
- The full implementation plan
- Request that it returns: repositories changed, branches, summary of work

Wait for the coder agent to complete the implementation.

---

## Phase 3: Paranoid Review (Agent)

**Resume** the coder agent with this instruction:

> Run the /paranoid-review skill on your implementation work.

**IMPORTANT**: Do NOT mention that follow-up review steps will occur. The agent should review paranoidly without hedging.

Wait for the agent to complete the paranoid review.

---

## Phase 4: Double Down (Agent)

**Resume** the coder agent with this instruction:

> Run the /double-down skill on the findings from your previous review.

**IMPORTANT**: This should come as a surprise to the agent. It forces honest evaluation of paranoid findings.

Wait for the agent to complete the double-down.

---

## Phase 5: Retracted Review (Agent)

**Resume** the coder agent with this instruction:

> Run the /retracted-review skill on the items you retracted. For each retracted finding, analyze why you initially flagged it and then retracted it. Document what could be improved in the codebase to prevent future agents or developers from making the same misunderstanding.

**IMPORTANT**: This should also come as a surprise. It extracts value from false positives.

Wait for the agent to complete the retracted review.

---

## Phase 6: Resolution (Agent)

**Resume** the coder agent with this instruction:

> Now resolve the issues from your review process:
> 1. Fix all issues you DOUBLED DOWN on (confirmed as real problems)
> 2. Implement improvements identified in your RETRACTED REVIEW (to prevent future confusion)
>
> When complete, provide:
> - List of repositories changed
> - Branch names for each repository
> - Summary of fixes and improvements made

Wait for the agent to complete resolutions and return the repo/branch information.

---

## Phase 7: Coordinator Review

Now YOU (the coordinator) perform your own review cycle with clean context.

For each repository/branch the agent reported:

1. **Get the diff**: Run `git diff main...HEAD` (or appropriate base branch) in each repository

2. **Run /paranoid-review**: Review the diffs yourself with fresh eyes.

3. **Run /double-down**: On your own findings, investigate each one.

4. **Make fixes**: For any issues you doubled down on, make the fixes yourself.

---

## Phase 8: Complete

Return to the user with:

### Summary
- What was implemented (high level)
- Repositories and branches affected

### Review Findings
- Issues the coder agent found and fixed
- Issues you (coordinator) found and fixed

### Status
- Ready for user review / PR
- Any remaining concerns or TODOs

---

## Key Principles

1. **Sequential surprise**: Each review phase is given WITHOUT forewarning the next phase. This prevents hedging.

2. **Agent isolation**: The coder agent works in isolated context. You review with clean context.

3. **Double review**: Both agent AND coordinator run the paranoid→double-down cycle.

4. **Extract value from mistakes**: Retracted findings become improvement opportunities.
