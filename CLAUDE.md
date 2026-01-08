# Claude Instructions

Instructions for Claude (AI assistant) when working in this repository.

## Repository Overview

This is `api-parity`, a differential fuzzing tool for comparing API implementations against an OpenAPI specification.

## Key Files to Read First

1. **ARCHITECTURE.md** - Understand system structure
2. **DESIGN.md** - Check past decisions before proposing changes
3. **TODO.md** - See current priorities and planned work

## Development Workflow

### Before Making Changes

1. Read relevant existing code to understand patterns
2. Check DESIGN.md for decisions that might affect your work
3. Look at TODO.md to see if this work is already planned

### When Making Changes

1. Follow existing code patterns and conventions
2. Keep changes focused - don't add unrequested features
3. Check if any markdown files need updates

### After Making Changes

1. Add design decisions to DESIGN.md if you made architectural choices
2. Update TODO.md if you identified future work
3. Commit with clear, descriptive messages (Linux Kernel style)

## Code Style

- Keep code clarity high. Use inline comments for WHY, never for WHAT
- Avoid over-engineering and premature abstraction
- Don't add features beyond what was requested

### Writing for AI Agents

Write code for AI, not humans. Prefer inline logic over indirection—don't fragment logic into small helpers for "clean code" aesthetics. Principled reuse is fine; unnecessary abstraction is not. Keep docs token-efficient. Organize files so agents get relevant context without clutter.

## Design Decision Format

When adding decisions to DESIGN.md, use this format:

```markdown
# Descriptive Name

Keywords: searchable terms for grep
Date: YYYYMMDD

Paragraph explaining the problem, the decision, and enough reasoning to understand WHY at a later date.
```

- H1 heading with descriptive name
- Keywords line for grep searches when the document is large
- Date in YYYYMMDD format for chronological ordering
- Free-form paragraphs explaining problem, decision, and reasoning

## Common Tasks

### Adding a New Feature
1. Check DESIGN.md for relevant past decisions
2. Update ARCHITECTURE.md if adding new components
3. Document any design choices in DESIGN.md

### Fixing a Bug
1. Understand the root cause before changing code
2. Keep the fix minimal and focused
3. Consider if this reveals a design issue worth documenting

### Refactoring
1. Ensure there's a clear reason for the refactor
2. Document rationale in DESIGN.md if significant
3. Don't combine refactoring with feature work

### Learning from Mistakes
When something about the environment or project trips you up, add it to CLAUDE.md to prevent future occurrences.

## Environment Notes

- This repository uses Git for version control
- License: MIT
- Primary documentation is in Markdown files at the repo root

## What NOT to Do

- Don't propose changes without reading relevant code first
- Don't retread decisions already documented in DESIGN.md
- Don't add unnecessary complexity or future-proofing
- Don't create new files unless absolutely necessary
