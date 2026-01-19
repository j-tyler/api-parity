---
description: Paranoid review of all changes on this branch vs main
---

Compare all changes on this branch to the main branch. Give a paranoid review of each change as if you were the pedantic reviewer protecting the repository from bugs and suboptimal changes.

**First**: Before reviewing, form a clear understanding of the intent of the change. What problem is being solved? What is the approach? Include this understanding at the END of your response under a "## Understanding of Change" section so the user can verify you understood correctly.

For each file changed:

1. **Code Issues**: Identify any bugs, edge cases, security issues, or logic errors you would flag on a PR.

2. **Documentation Quality**: Make sure documentation explains WHY not WHAT wherever needed. Flag any documentation that merely restates the obvious without providing reasoning or context.

3. **Consistency**: Check that any changes of note are reflected in relevant project .md files (ARCHITECTURE.md, DESIGN.md, TODO.md, CLAUDE.md).

4. **Improvement Suggestions**: Provide specific feedback on how the changes could be even better.

5. **Technical Debt**: Flag any shortcuts, code smells, or incomplete work that should be tracked. These must be added to TODO.md if not fixed in the same branch.

Be thorough and critical. The goal is to catch issues before they become problems and avoid accumulating technical debt.

**Before flagging**: Trace the code path. Read tests related to the code paths together.

**Important:** Number each issue sequentially (e.g., #1, #2, #3) for easy reference in follow-up commands like `/double-down`.
