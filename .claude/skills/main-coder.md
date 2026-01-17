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

When implementation is complete, run /paranoid-review on all your changes.

Report:
- Files changed
- Summary of work done
- All review findings (numbered)",
  description="Implement and review"
)
```

The agent implements the plan, then immediately reviews its own work while context is fresh.

Wait for completion. **Save the agentId** for Phase 3.

---

## Phase 3: Double-Down + Resolution (Resume 1)

**Resume** the agent:

```
Task(
  subagent_type="coder",
  resume="<agentId from Phase 2>",
  prompt="For each finding from your paranoid review:

1. Run /double-down - gather evidence to confirm or retract each issue
2. Run /retracted-review - for items you retracted, analyze why you flagged them and identify documentation/code improvements to prevent future confusion
3. Fix all issues you DOUBLED DOWN on
4. Make all improvements identified in your retracted review

Report:
- Which issues you doubled down on and how you fixed them
- Which issues you retracted and what improvements you made
- Final summary of all changes",
  description="Resolve and improve"
)
```

The agent validates its findings, fixes real issues, and extracts value from false positives.

Wait for completion.

---

## Phase 4: Coordinator Review

Now YOU (the coordinator) review with fresh context.

1. **Get the diff**: `git diff origin/main..HEAD`

2. **Run /paranoid-review**: Review all changes yourself

3. **Run /double-down**: Investigate each finding you made

4. **Make fixes**: Fix any issues you doubled down on

---

## Phase 5: Commit and Complete

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

1. **Sequential surprise**: Agent reviews immediately after implementing (no advance warning). Then surprised again with "now fix everything."

2. **Context preservation**: Agent has full history—remembers implementation choices when reviewing, remembers findings when fixing.

3. **Double review**: Agent reviews its work, then coordinator reviews independently with fresh eyes.

4. **Extract value from mistakes**: False positives (retracted findings) become documentation/code improvements.

---

## Why This Design

**Resume limit:** Agent resumes fail after ~2-3 sequential resumes (see CLAUDE.md "Task Tool Gotchas"). This workflow uses exactly 1 resume.

**What each phase does:**
- Phase 2: Implement + paranoid-review (agent finds potential issues)
- Phase 3: Double-down + retracted-review + fix (agent validates and resolves everything)
- Phase 4: Coordinator review (independent verification)

**What's preserved:**
- Agent implements without knowing review is coming in the prompt
- Agent does thorough review while implementation is fresh in context
- Agent must defend its findings before fixing
- Coordinator provides independent verification
- False positives improve the codebase

---

## Error Handling

If resume fails:
1. Verify you're using the correct agentId from Phase 2
2. Do NOT spawn a fresh agent—context preservation is essential
3. Return to user with progress made and failure details

The user can then decide to:
- Retry the workflow
- Complete remaining phases manually
- Investigate the failure
