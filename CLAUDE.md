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

## CRITICAL: Keep Documentation in Sync with Implementation

**This is a CRITICAL severity requirement. Outdated documentation creates wrong code.**

In an AI-agent-maintained codebase, each agent session starts with fresh context. Agents read ARCHITECTURE.md and DESIGN.md to understand the system before making changes. If documentation describes interfaces, parameters, or behaviors that don't match the actual code:

1. The next agent will write code that doesn't work
2. The agent will trust the docs and not verify against actual implementation
3. Wrong assumptions compound into architectural drift

**After ANY code change that affects documented interfaces:**

| Changed | Update |
|---------|--------|
| Constructor parameters | ARCHITECTURE.md interface section |
| Method signatures | ARCHITECTURE.md interface section |
| New design decisions | DESIGN.md with Keywords/Date/Reasoning |
| Terminology changes | All markdown files (grep to find all occurrences) |
| New features | ARCHITECTURE.md component behavior section |

**Documentation must explain WHY, not just WHAT:**

Documenting what code does is insufficient. The next agent can read the code to see what it does. What they cannot see is WHY it was designed that way. Without the reasoning:
- Agent sees "spec is parsed twice" and "fixes" it by removing the second parse
- Agent doesn't know Schemathesis doesn't expose raw spec, so the "fix" breaks link extraction
- Hours wasted debugging a "fix" that was actually a regression

**Where to put WHY:**
- **Inline comments** — Local implementation choices affecting one file. Why this algorithm, why this fallback, why this ordering.
- **DESIGN.md** — Significant decisions affecting multiple files or the system's architecture. Don't flood DESIGN.md with local implementation details.

**Example of documentation drift causing bugs:**
- ARCHITECTURE.md shows `Executor.__init__(target_a, target_b, timeout)`
- Implementation adds `link_fields` parameter
- Next agent reads ARCHITECTURE.md, writes code without `link_fields`
- Chain execution silently fails to extract variables

This is not a "nice to have"—it's as critical as writing correct code.

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

### Scanning Document Structure Before Reading

Before reading a long markdown file, scan its headings to understand structure:

```bash
grep -n "^##" docs/troubleshooting.md
```

This shows section names and line numbers without filling context with content. Use this to:
1. Decide if the document has what you need
2. Read only the relevant section with `Read` tool's `offset` and `limit` parameters
3. Avoid loading 200+ lines when you only need 20

Example: Looking for CEL errors in troubleshooting.md:
```bash
grep -n "^##" docs/troubleshooting.md
# Output shows section headings with line numbers
# Now read just that section using offset/limit instead of the whole file
```

### Custom Slash Commands

Commands are defined in `.claude/commands/*.md`. To run a command like `/foo`, read `.claude/commands/foo.md` and execute the logic manually.

## CRITICAL: No Blocking Code Without Timeouts

**This is a hard rule. No exceptions.**

Any code that can block indefinitely will eventually hang the entire process, making debugging impossible and requiring manual intervention. This includes:

- `subprocess.Popen.wait()` — use `wait(timeout=N)`
- `subprocess.Popen.communicate()` — use `communicate(timeout=N)`
- `file.read()` on pipes — use `select.select()` first
- `file.readline()` on pipes — use `select.select()` first
- `socket.recv()` — use `socket.settimeout()` or `select.select()`
- `queue.get()` — use `get(timeout=N)`
- Any blocking I/O on subprocess pipes

**Pattern for safe pipe reads:**
```python
import select

# WRONG - can hang forever
line = proc.stdout.readline()

# RIGHT - timeout prevents hang
ready, _, _ = select.select([proc.stdout], [], [], timeout_seconds)
if not ready:
    raise TimeoutError("read timeout")
line = proc.stdout.readline()  # Safe only if subprocess writes complete lines atomically
```

**Pattern for safe subprocess cleanup:**
```python
# WRONG - can hang forever
proc.wait()

# RIGHT - timeout with escalation to SIGKILL
proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass  # Process is unkillable, nothing more we can do
```

**If there's no timeout parameter available**, you must use a different approach:
1. Use `select.select()` with timeout before any blocking read
2. Use non-blocking I/O with polling
3. Use a separate thread with `threading.Timer` to kill the operation
4. Redesign to avoid the blocking call entirely

**When writing tests:** Use `PortReservation` (not `find_free_port()`) for server ports—`find_free_port()` has a race window where another process can grab the port before your server binds. Always use context managers for resource cleanup. See `tests/conftest.py` for patterns.

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

5. **InferenceConfig not in public API** — To disable inference algorithms for stateful testing, `InferenceConfig` is needed but not exported in `schemathesis.config.__all__`. Access it via indirection:
   ```python
   from schemathesis.config import StatefulPhaseConfig
   _InferenceConfig = type(StatefulPhaseConfig().inference)
   disabled = _InferenceConfig(algorithms=[])
   ```
   If this breaks in a future version, check if `InferenceConfig` was added to the public API.

6. **Non-ASCII in generated header values** — Hypothesis may generate non-ASCII characters (e.g., `\xaf`) for header values during fuzzing. HTTP headers must be ASCII per RFC 7230. The Executor sanitizes header values before sending by replacing non-ASCII characters with `?`. This prevents `UnicodeEncodeError` from httpx while preserving the test structure.

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

8. **Always use timeouts on blocking I/O** — See "CRITICAL: No Blocking Code Without Timeouts" above. The CELEvaluator uses `select.select()` before every `readline()` call with `EVALUATION_TIMEOUT = 10.0` seconds. This is not optional.

## Replay Command Gotchas

The replay command re-executes saved mismatch bundles. Key things to know:

1. **Classification is by failure pattern, not values** — `_is_same_mismatch()` compares `mismatch_type` and failing paths, not actual values. Two runs with different timestamps at `$.created_at` are "same mismatch" if both fail at that path.

2. **DIFFERENT MISMATCH is expected after rule changes** — If you add a rule for `$.id`, a bundle that previously failed at `$.id` might now fail at `$.other_field` or pass entirely. This is correct behavior, not a bug.

3. **Chain replay doesn't have the OpenAPI spec** — `extract_link_fields_from_chain()` parses link_source from stored chain data, not the spec. If spec field names changed, chain replay may extract wrong variables. Solution: regenerate chains with `explore --stateful`.

4. **Replay writes new bundles for persistent mismatches** — Bundles in `--out` directory are fresh captures from replay execution, not copies of input bundles. They contain current response data.

5. **Bundle discovery is lenient** — `discover_bundles()` silently skips directories without `case.json` or `chain.json`. It checks for `mismatches/` subdirectory first, then searches the input directory directly. If bundle count in summary is lower than expected, verify bundle directories contain the required files.

6. **ReplayStats tracks bundle names** — `fixed_bundles`, `persistent_bundles`, and `changed_bundles` lists contain bundle directory names (not full paths). Useful for reporting which specific issues were resolved.

7. **--in points to explore output, not a specific bundle**:
   ```bash
   # Correct
   api-parity replay --in ./artifacts ...

   # Wrong - points to a specific bundle
   api-parity replay --in ./artifacts/mismatches/20260112T... ...
   ```

## Running Tests

**Build the CEL evaluator before running tests.** Many tests require the Go CEL binary. Without it, tests fail with `CELSubprocessError: CEL evaluator binary not found`.

```bash
# Build CEL evaluator first
go build -o cel-evaluator ./cmd/cel-evaluator

# Run tests - ALWAYS use these flags
python -m pytest tests/ -x -q --tb=short
```

### CRITICAL: Always Use `-x -q --tb=short`

**This is mandatory.** Never run pytest without these flags:

| Flag | Purpose |
|------|---------|
| `-x` | Stop on first failure. Prevents cascading failures from filling context. |
| `-q` | Quiet mode. Shows dots instead of test names. Passing tests don't matter. |
| `--tb=short` | Short tracebacks. Shows only what's needed to diagnose failures. |

**Why this matters:** Verbose test output fills context with useless information. A full test run with `-v` can produce 500+ lines of passing test names. With `-x -q --tb=short`, a passing run is ~5 lines. A failing run shows only the failure.

```bash
# WRONG - fills context with noise
pytest tests/ -v

# WRONG - no stop on first failure, cascading failures fill context
pytest tests/ -q --tb=short

# RIGHT - minimal output, stops on first failure
python -m pytest tests/ -x -q --tb=short
```

Tests that require the CEL binary use the `cel_evaluator_exists` fixture or `pytestmark = pytest.mark.skipif(...)` to skip gracefully when the binary is missing. If you add new tests that use `CELEvaluator`, include this skip mechanism.

## What NOT to Do

- Don't propose changes without reading relevant code first
- Don't retread decisions already documented in DESIGN.md
- Don't add unnecessary complexity or future-proofing
- Don't create new files unless absolutely necessary
- Don't use Python CEL libraries (cel-python, common-expression-language) — untrusted dependencies
