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

Spawn the `coder` agent with your implementation plan using the Task tool:

```
Task(
  subagent_type="coder",
  prompt="<your implementation plan>",
  description="Implement <feature>"
)
```

**Instructions to include in the prompt:**
- The full implementation plan
- Request that it returns: repositories changed, branches, summary of work

Wait for the coder agent to complete the implementation.

**IMPORTANT**: The Task tool returns an `agentId` (e.g., `agentId: ad696ca`). Save this ID—you need it to resume the agent in subsequent phases.

---

## Phase 3: Paranoid Review (Agent)

**Resume** the coder agent using the `resume` parameter with the agentId from Phase 2:

```
Task(
  subagent_type="coder",
  resume="<agentId from Phase 2>",
  prompt="Run the /paranoid-review skill on your implementation work.",
  description="Paranoid review"
)
```

The resumed agent has **full context** from Phase 2—it remembers all its implementation work, tool calls, and reasoning.

**IMPORTANT**: Do NOT mention that follow-up review steps will occur. The agent should review paranoidly without hedging.

Wait for the agent to complete the paranoid review. Save the returned agentId for Phase 4.

---

## Phase 4: Double Down (Agent)

**Resume** the coder agent (using agentId from Phase 3):

```
Task(
  subagent_type="coder",
  resume="<agentId from Phase 3>",
  prompt="Run the /double-down skill on the findings from your previous review.",
  description="Double-down review"
)
```

**IMPORTANT**: This should come as a surprise to the agent. It forces honest evaluation of paranoid findings.

Wait for the agent to complete the double-down. Save the returned agentId for Phase 5.

---

## Phase 5: Retracted Review (Agent)

**Resume** the coder agent (using agentId from Phase 4):

```
Task(
  subagent_type="coder",
  resume="<agentId from Phase 4>",
  prompt="Run the /retracted-review skill on the items you retracted. For each retracted finding, analyze why you initially flagged it and then retracted it. Document what could be improved in the codebase to prevent future agents or developers from making the same misunderstanding.",
  description="Retracted review"
)
```

**IMPORTANT**: This should also come as a surprise. It extracts value from false positives.

Wait for the agent to complete the retracted review. Save the returned agentId for Phase 6.

---

## Phase 6: Resolution (Agent)

**Resume** the coder agent (using agentId from Phase 5):

```
Task(
  subagent_type="coder",
  resume="<agentId from Phase 5>",
  prompt="Now resolve the issues from your review process:
1. Fix all issues you DOUBLED DOWN on (confirmed as real problems)
2. Implement improvements identified in your RETRACTED REVIEW (to prevent future confusion)

When complete, provide:
- List of repositories changed
- Branch names for each repository
- Summary of fixes and improvements made",
  description="Resolve review findings"
)
```

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

## Phase 8: Commit and Complete

**Commit all changes** with a git commit message in similar style as seen in the Linux Kernel:
- Subject line: type prefix (feat/fix/refactor/docs), concise summary under 50 chars
- Blank line
- Body: explain what changed and why, wrapped at 72 chars

**Push to the branch** specified in the task instructions.

**Return to the user with:**

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

---

## How Agent Resume Works

**Context preservation:** When you resume an agent, it retains its full conversation history—all previous tool calls, file reads, edits, and reasoning. The agent picks up exactly where it stopped.

**Agent transcripts:** Stored at `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl`

**Why this matters for the workflow:**
- Phase 3 (paranoid review): Agent can reference its own implementation choices
- Phase 4 (double-down): Agent must defend findings it just made
- Phase 5 (retracted review): Agent analyzes its own false positives
- Phase 6 (resolution): Agent has full context of what to fix and why

---

## Error Handling: Resume Failures

If a resume call fails, **never spawn a fresh agent**. The workflow depends on preserved context for the "surprise" review phases. Try these approaches in order:

### 1. Retry with exponential backoff
Transient API errors may resolve on retry:
```
# Wait 2s, retry
# Wait 4s, retry
# Wait 8s, retry
```

### 2. Verify the agentId
Check that you're using the correct agentId from the previous phase's output. The ID is returned at the end of each Task result (e.g., `agentId: ad696ca`).

### 3. Check agent transcript exists
Transcripts are stored at `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl`. If missing, the agent cannot be resumed.

### 4. Known limitation: Subagent resume bug
There's a known issue (GitHub #11712) where agent transcripts don't store user prompts, only assistant responses and tool results. This can cause resume failures or context drift after multiple resumes.

### If all attempts fail

**Do not proceed with a fresh agent.** Return to the user with:

1. **Progress made** — What phases completed successfully
2. **Current state** — What changes exist in the working directory
3. **Failure details** — Which phase failed to resume, error messages, agentId attempted
4. **Recommendation** — User can choose to:
   - Retry the workflow from the beginning
   - Manually complete remaining phases
   - Investigate the resume failure

The workflow's value depends on context preservation. Running phases with fresh agents defeats the purpose of the sequential surprise review pattern.
