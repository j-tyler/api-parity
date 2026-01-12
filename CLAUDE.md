# Claude Instructions

Instructions for Claude (AI assistant) when working in this repository.

## Repository Overview

This is `api-parity`, a differential fuzzing tool for comparing API implementations against an OpenAPI specification.

## Document Purposes

Each root markdown file has a specific purpose. Put content in the right place:

| File | Purpose | Content Examples |
|------|---------|------------------|
| **README.md** | Brief project overview for humans | What it does, how to run it, license |
| **CLAUDE.md** | Instructions for AI assistants | Workflow, gotchas, environment notes |
| **ARCHITECTURE.md** | Technical system structure (for agents) | Components, data models, data flow, interfaces |
| **DESIGN.md** | Decisions and reasoning | Why choices were made, tradeoffs considered |
| **TODO.md** | Future work items | Planned features, known issues, spec work needed |

**Rule of thumb:**
- "What is this project?" → README
- "How do I work in this repo?" → CLAUDE
- "How does the system work?" → ARCHITECTURE
- "Why was it built this way?" → DESIGN
- "What might we do later?" → TODO

### ARCHITECTURE.md Content Guidelines

ARCHITECTURE.md helps new agents efficiently understand the project without clogging context windows with unnecessary code. Be token-efficient but not at the expense of clarity.

**Include in ARCHITECTURE.md:**
- Component responsibilities and boundaries
- Data flow between components
- Interfaces (what inputs/outputs, how to instantiate)
- Behavior that affects multiple components or callers
- Error handling philosophy (what propagates vs what's handled)

**Leave in code (don't document in ARCHITECTURE.md):**
- Internal implementation details (caching strategies, sentinel values, internal helpers)
- Information already documented elsewhere (don't duplicate)
- Details only relevant when modifying that specific file

**Test:** Would a new agent working on a *different* component benefit from knowing this? If yes, document it. If only useful when reading *this* file, leave it in code.

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

See DESIGN.md "AI-Optimized Code and Documentation" for the full rationale. In brief: prefer inline logic over indirection, avoid unnecessary abstraction, keep docs token-efficient.

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
- **Python** is the primary implementation language
- **Go** is used for the CEL evaluator subprocess only (see ARCHITECTURE.md "CEL Evaluator Component")

### Git Commands

The `main` branch exists only on the remote, not locally. Use `origin/main` for comparisons:

```bash
# Correct - compare to remote main
git diff origin/main..HEAD
git log --oneline origin/main..HEAD

# Wrong - fails with "unknown revision"
git diff main..HEAD
```

### Reading Files and Running Commands

- **Read files** with the `Read` tool, not `cat` or shell commands
- **Search file contents** with `Grep`, not `grep` or `rg`
- **Find files by pattern** with `Glob`, not `find` or `ls`
- **Edit files** with `Edit` tool for surgical changes, `Write` for full rewrites
- **Run shell commands** with `Bash` tool for git, python, pip, etc.
- **Explore codebase** with `Task` tool (subagent_type=Explore) for open-ended searches

### Custom Slash Commands

Commands are defined in `.claude/commands/*.md`. To run a command like `/foo`, read `.claude/commands/foo.md` and execute the logic manually.

## Schemathesis Gotchas

These issues were discovered during prototype validation. Don't repeat them:

1. **Wrapped results** — `schema.get_all_operations()` returns wrapped `Ok/Err` results. Call `.ok()` to unwrap:
   ```python
   for result in schema.get_all_operations():
       operation = result.ok()  # Don't forget this!
   ```

2. **Response constructor** — `schemathesis.core.transport.Response` requires specific fields:
   ```python
   Response(
       status_code=200,
       headers={'content-type': ['application/json']},  # List values!
       content=b'{"id": "abc"}',  # Bytes, not str
       request=prepared_request,  # PreparedRequest object required
       elapsed=0.1,
       verify=False,
       http_version='1.1',
   )
   ```

3. **Header values are lists** — Response headers dict must have list values, not strings.

4. **Override validate_response()** — Must override and return `pass` to skip built-in schema validation (we do our own comparison).

## Comparison Rules Gotchas

These issues were discovered during comparison rules design. Don't repeat them:

1. **Override semantics, not merge** — Operation rules completely override default rules for any key they define. There is no deep merging of nested objects.

2. **`unordered_array` doesn't handle duplicates** — The expression `a.all(x, x in b)` passes for arrays with different duplicate counts (e.g., `[1,1,2]` matches `[1,2,2]`). Only use for arrays with unique elements.

3. **Escape strings in CEL expressions** — When inlining string parameters, escape backslashes and quotes. A regex pattern like `foo"bar` must become `"foo\"bar"` in CEL.

4. **No `inherit_defaults` field** — Operation rules don't have an explicit inheritance flag. They always implicitly inherit unless they override.

5. **Multi-value headers use first value only** — Don't assume header values are strings—they're lists internally. Only the first value is used for comparison.

## CEL Evaluator Gotchas

These patterns apply when working with the Go CEL subprocess:

1. **Flush after every write** — Both Python and Go buffer I/O. Without explicit flush, messages sit in userspace buffers:
   ```python
   proc.stdin.write(json.dumps(req) + '\n')
   proc.stdin.flush()  # Required!
   ```
   ```go
   writer.WriteString(resp + "\n")
   writer.Flush()  // Required!
   ```

2. **Newline-delimited JSON** — Each message is one line. Use `readline()` in Python, `bufio.Scanner` in Go. Never embed raw newlines in JSON values (they must be escaped as `\n`).

3. **Handle subprocess death** — If Go process crashes, Python sees EOF on stdout. Detect and restart:
   ```python
   line = proc.stdout.readline()
   if not line:  # EOF = subprocess died
       self._restart_subprocess()
   ```
   Limit restarts (e.g., 3 attempts) to avoid infinite loops if the binary is broken.

4. **Capture stderr** — Use `stderr=PIPE` or `stderr=DEVNULL`. Leaving stderr inherited pollutes CLI output if Go logs or panics.

5. **Correlate by ID** — Always match response `id` to request `id` for debugging and log correlation.

6. **CEL errors are not Python exceptions** — A malformed expression returns `{"ok":false,"error":"..."}`, not a crash. Handle both cases:
   ```python
   if not result['ok']:
       raise CELEvaluationError(result['error'])
   return result['result']
   ```

7. **Expression timeout** — The Go CEL evaluator has a 5-second timeout per expression (see `evaluationTimeout` in `cmd/cel-evaluator/main.go`). If evaluation exceeds this, it returns `{"ok":false,"error":"evaluation timeout exceeded"}`. The Comparator treats this as a mismatch with `rule: "error: evaluation timeout exceeded"`. No special handling needed—timeout errors flow through the normal error path.

## Running Tests

**Build the CEL evaluator before running tests.** Many tests require the Go CEL binary. Without it, tests fail with `CELSubprocessError: CEL evaluator binary not found`.

```bash
# Build CEL evaluator first
go build -o cel-evaluator ./cmd/cel-evaluator

# Then run tests
pytest tests/
```

Tests that require the CEL binary use the `cel_evaluator_exists` fixture or `pytestmark = pytest.mark.skipif(...)` to skip gracefully when the binary is missing. If you add new tests that use `CELEvaluator`, include this skip mechanism.

## What NOT to Do

- Don't propose changes without reading relevant code first
- Don't retread decisions already documented in DESIGN.md
- Don't add unnecessary complexity or future-proofing
- Don't create new files unless absolutely necessary
- Don't use Python CEL libraries (cel-python, common-expression-language) — untrusted dependencies
