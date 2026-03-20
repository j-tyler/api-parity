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

## Phase 2: Implementation + Paranoid Review (Fresh Agent)

Spawn the `coder` agent:

```
Task(
  subagent_type="coder",
  prompt="<your implementation plan>

When implementation is complete, review all changes on this branch vs main. Give a paranoid review as if you were the pedantic reviewer protecting the repository from bugs and suboptimal changes.

Before reviewing, form a clear understanding of the intent of the change.

For each file changed:
1. Code Issues: Identify bugs, edge cases, security issues, or logic errors.
2. Documentation Quality: Make sure documentation explains WHY not WHAT.
3. Consistency: Check that changes are reflected in relevant project .md files.
4. Improvement Suggestions: Specific feedback on how changes could be better.
5. Technical Debt: Flag shortcuts, code smells, or incomplete work.

Before flagging: Trace the code path. Read tests related to the code paths together.
Number each issue sequentially (#1, #2, #3).

Report:
- Files changed
- Summary of work done
- All review findings (numbered)",
  description="Implement and review"
)
```

The agent implements the plan, then reviews its own work while context is fresh.

Wait for completion.

---

## Phase 3: Double Down (Coordinator)

Now YOU (the coordinator) take the agent's numbered findings and investigate each one with fresh eyes.

1. **Get the diff**: `git diff origin/main..HEAD`

2. **Double down on each finding**: For each numbered item from the agent's review, gather evidence by reading code and tracing logic. The agent wrote the code being reviewed — some "issues" may be intentional choices it made.

For each item, conclude with exactly one of:

**DOUBLE DOWN**: Evidence confirms the issue.
- Show the problematic code
- Explain why it's a problem
- Give ONE specific fix with reason why (NEVER say either solution A or solution B)

**RETRACT**: The agent was wrong.
- What it misunderstood
- Why the code is correct

No hedging. Every item gets DOUBLE DOWN or RETRACT.

3. **Retracted review**: For each issue you RETRACTED, consider whether a token-efficient refinement could prevent similar misunderstandings:

Options (in order of preference):
1. **Inline comment** — brief WHY explanation near code
2. **Code change** — rename variables/functions for clarity
3. **Docstring update** — clarify non-obvious behavior
4. **Project .md file** — only if it affects multiple components

For each retracted issue, output one of:
- **REFINE**: [specific change] — [why it helps]
- **NO CHANGE**: [why the misunderstanding was reviewer error, not code/doc deficiency]

4. **Propose changes**: Present a summary of all proposed changes to the user:
- Issues you DOUBLED DOWN on and the specific fix for each
- Items you identified as REFINE and the specific improvement for each

**STOP and wait for user approval before making any changes.** Do not edit or write any files until the user confirms which changes to proceed with.

---

## Phase 4: Commit and Complete

**Commit** with Linux Kernel style message:
- Subject: type prefix + concise summary (<50 chars)
- Body: what changed and why (wrapped at 72 chars)

**Push** to the specified branch.

**Report to user:**

### Summary
- What was implemented
- Files/branches affected

### Review Findings
- Issues agent found and fixed
- Issues you found and fixed

### Status
- Ready for review / remaining concerns

---

## Key Principles

1. **Sequential surprise**: Agent reviews immediately after implementing (no advance warning). It doesn't know a review is coming until implementation is done.

2. **Separation of concerns**: Agent does the paranoid review (finds issues), coordinator does the double-down (validates with fresh eyes). The reviewer is different from the coder.

3. **Human in the loop**: Coordinator proposes changes but waits for user approval before editing files.

4. **Extract value from mistakes**: False positives (retracted findings) become documentation/code improvements via the retracted review.

---

## Why This Design

**What each phase does:**
- Phase 2: Agent implements + paranoid review (finds potential issues while context is fresh)
- Phase 3: Coordinator double-downs on agent's findings (independent validation), then waits for human approval

**Why the coordinator does the double-down, not the agent:**
- Fresh eyes catch different things than the original author
- The agent is biased toward its own code — a separate reviewer is more likely to genuinely retract or confirm
- Keeps the agent prompt simple: implement, then review. No multi-phase complexity.

---

## Error Handling

If the coder agent fails:
1. Return to user with progress made and failure details
2. The user can then decide to retry or complete remaining phases manually
