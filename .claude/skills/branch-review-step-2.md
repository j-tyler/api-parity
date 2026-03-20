# Step 2: Correctness Review

Give a paranoid review of each changed file as if you were a pedantic reviewer protecting this repository from bugs and suboptimal changes.

For each file changed, identify any bugs, edge cases, security issues, or logic errors you would flag on a PR.

**For executable code:** Trace the code path. Read tests related to the code under review. If you cannot show the trace, do not flag the issue. Include: the file, the code path you traced, what tests you read, and why it's a problem.

**For non-executable changes** (docs, config, prompts, workflows): Cite the specific text and explain what concrete problem it causes — a wrong instruction, a missing step, an internal contradiction, a regression from the previous version. If you cannot state the concrete problem, do not flag the issue.

**Output:** Number each issue sequentially (#1, #2, #3, etc.). These numbers will be referenced in later steps.

When complete, read and follow `.claude/skills/branch-review-step-3.md`.
