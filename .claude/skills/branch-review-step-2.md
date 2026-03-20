# Step 2: Correctness Review

Give a paranoid review of each changed file as if you were a pedantic reviewer protecting this repository from bugs and suboptimal changes.

For each file changed, identify any bugs, edge cases, security issues, or logic errors you would flag on a PR.

**Before flagging an issue:** Trace the code path. Read tests related to the code under review. If you cannot show the trace, do not flag the issue.

**For each issue, include:**
- The file and code in question
- The code path you traced
- What tests you read
- Why it's a problem

**Output:** Number each issue sequentially (#1, #2, #3, etc.). These numbers will be referenced in later steps.

When complete, read and follow `.claude/skills/branch-review-step-3.md`.
