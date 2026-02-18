# Claude Instructions

Instructions for Claude when working in this repository.

## Repository Overview

`api-parity` — Differential fuzzing tool for comparing API implementations against an OpenAPI specification. Python (primary), Go (CEL evaluator subprocess).

## Document Purposes

Each root markdown file has a specific purpose. Put content in the right place:

| File | Purpose | Content Examples |
|------|---------|------------------|
| **README.md** | Brief project overview | What it does, how to install, how to run |
| **CLAUDE.md** | Instructions for AI assistants | Workflow, gotchas, environment notes |
| **ARCHITECTURE.md** | System structure | Components, data models, data flow |
| **DESIGN.md** | Decisions and reasoning | Why choices were made, tradeoffs considered |
| **TODO.md** | Future work ideas | Things that came up, shouldn't be forgotten |

**Rule of thumb:**
- "What is this project?" → README
- "How do I work in this repo?" → CLAUDE
- "How does the system work?" → ARCHITECTURE
- "Why was it built this way?" → DESIGN
- "What might we do later?" → TODO

### What Goes in ARCHITECTURE.md

**Include:**
- Component responsibilities and boundaries
- Data flow between components
- How to instantiate and use components
- Behavior that affects multiple components or callers
- Error handling philosophy (what propagates vs what's handled)

**Leave in code:**
- Internal implementation details (caching, sentinel values, internal helpers)
- Details only relevant when modifying that specific file

**Test:** Would a new agent working on a *different* component benefit from knowing this? If yes, document it. If only useful when reading *this* file, leave it in code.

---

## Development Workflow

### Before Changes
1. Read relevant code to understand patterns
2. Check DESIGN.md for decisions that might affect your work
3. Check TODO.md for related ideas or notes

### When Making Changes
1. Follow existing code patterns and conventions
2. Keep changes focused — don't add unrequested features
3. Update markdown files to reflect your changes

### After Changes
1. Add design decisions to DESIGN.md if you made architectural choices
2. Add to TODO.md if ideas came up that shouldn't be forgotten
3. Commit with clear messages (Linux Kernel style)

### Common Tasks

**Adding a New Feature:**
1. Check DESIGN.md for relevant past decisions
2. Update ARCHITECTURE.md if adding new components
3. Document any design choices in DESIGN.md

**Fixing a Bug:**
1. Understand the root cause before changing code
2. Keep the fix minimal and focused
3. Consider if this reveals a design issue worth documenting

**Learning from Mistakes:**
When something about the environment or project trips you up, add it to CLAUDE.md to prevent future occurrences.

---

## CRITICAL: Keep Docs in Sync

**This is a CRITICAL severity requirement. Outdated documentation creates wrong code.**

Each agent session starts with fresh context. Agents read ARCHITECTURE.md and DESIGN.md to understand the system before making changes. If docs don't match implementation:

1. The next agent will write code that doesn't work
2. The agent will trust the docs and not verify against actual implementation
3. Wrong assumptions compound into architectural drift

**Example of documentation drift causing bugs:**
- ARCHITECTURE.md shows `Executor.__init__(target_a, target_b, timeout)`
- Implementation adds `link_fields` parameter
- Next agent reads ARCHITECTURE.md, writes code without `link_fields`
- Chain execution silently fails to extract variables

This is not a "nice to have"—it's as critical as writing correct code.

**WHY and INTENT matter more than WHAT:**

In the docs and in the code, WHY and INTENT are often more important than WHAT. Make sure the WHY and INTENT will be clear to LLM Agents with no prior context to changes you have made.

The next agent can read code to see what it does. What they cannot see is WHY it was designed that way. Without the reasoning:
- Agent sees "spec is parsed twice" and "fixes" it by removing the second parse
- Agent doesn't know Schemathesis doesn't expose raw spec, so the "fix" breaks link extraction

**Where to put WHY:**
- **Inline comments** — Local implementation choices affecting one file
- **DESIGN.md** — Significant decisions affecting multiple files or system architecture

---

## Writing Code for LLM Agents

LLM coding agents experience code more like grep output, not like a human using an IDE. LLMs see what is in their context-window, not the full project. LLMs cannot build mental models over months of working in a codebase. Write and modify code accordingly.

**1. Optimize for Grep, Not for IDE**

Names should be unique enough to locate via text search without false positives. Prefer `get_user_by_email()` over `get()`. Prefer `payment_stripe.py` over `utils.py`. If an agent searching the codebase for "stripe payment" wouldn't find the stripe payment code, rename the file and/or the methods in the file.

**2. One Concept Per File, Named for What It Contains**

Each file should contain one cohesive concept. If you struggle to name a file, it probably contains too many things. Apply the same judgment you would for "separation of concerns," but optimize for discoverability and context-efficiency rather than classical architectural purity.

**3. Inline Over Fragmented**

Prefer code that reads linearly from top to bottom over code that jumps between many small helper methods. The "Clean Code" style of extracting every few lines into named methods creates unnecessary indirection. Extract only when there's clear reuse or a genuine abstraction—not just for "human readability" that would harm an LLM's ability to follow the logic.

**4. Explicit Over Implicit**

Write code as if the reader cannot easily access files except the one they're viewing. Implicit behavior through decorators, metaprogramming, monkey-patching, or "magic" inheritance makes code harder to understand in isolation. When you must use such patterns, add clear comments explaining what they do.

**5. Types as Documentation**

Annotate and use types wherever possible as if they may be the only documentation available. In a codebase without an IDE, types are your primary signal for what functions accept and return.

**6. Errors That Explain Themselves**

Write error messages that state what went wrong, relevant variable context, and what was expected. Your error messages are often your only debugging context.

**7. Comments That State Intent**

Comments should explain why code exists and what it expects, not just what it does. The next agent reading this code will not have your current context window to reference. Write comments like a colleague explaining context you'd need before modifying something. Think of the clarity you'd find in well-documented open source projects like SQLite.

**8. Colocate What Changes Together**

Related code that changes together should live together, even if architectural patterns suggest separating by type (models/, services/, controllers/). Optimize for understanding a feature in one place over maintaining theoretical purity.

**9. Tests as Executable Specifications**

Write tests in the style of executable documentation. An LLM agent who reads only your test file should understand the complete contract of the code under test. Prioritize clarity over DRY in test code.

**Applying Judgment**

These principles exist because LLM context windows are finite and lack persistent memory across sessions. But they are principles, not laws. When they conflict, resolve them by asking: "What would make this code easiest to understand and modify for the next LLM agent seeing only this fragment?"

---

## Environment

**Git:** The `main` branch exists only on the remote, not locally.

```bash
git diff origin/main..HEAD   # Correct
git diff main..HEAD          # Wrong - fails with "unknown revision"
```

**Tools:**
- **Read files** with `Read` tool, not `cat` or shell commands
- **Search file contents** with `Grep`, not `grep` or `rg`
- **Find files by pattern** with `Glob`, not `find` or `ls`
- **Edit files** with `Edit` for surgical changes, `Write` for full rewrites
- **Explore codebase** with `Task` tool (subagent_type=Explore) for open-ended searches

**Scanning Document Structure:**

Before reading a long markdown file, scan its headings to understand structure:

```bash
grep -n "^##" docs/troubleshooting.md
```

This shows section names and line numbers without filling context with content. Use `Read` tool's `offset` and `limit` to read only the relevant section instead of loading 200+ lines.

---

## CRITICAL: No Blocking Code Without Timeouts

**This is a hard rule. No exceptions.**

Any code that can block indefinitely will eventually hang the entire process, making debugging impossible and requiring manual intervention. This includes:

- `subprocess.Popen.wait()` — use `wait(timeout=N)`
- `subprocess.Popen.communicate()` — use `communicate(timeout=N)`
- `file.read()` on pipes — use `select.select()` first
- `file.readline()` on pipes — use `select.select()` first
- `queue.get()` — use `get(timeout=N)`

**Pattern for safe pipe reads:**
```python
import select

# WRONG - can hang forever
line = proc.stdout.readline()

# RIGHT - timeout prevents hang
ready, _, _ = select.select([proc.stdout], [], [], timeout)
if ready:
    line = proc.stdout.readline()
```

**Safe subprocess cleanup:**
```python
proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.wait(timeout=5)
```

For tests: Use `PortReservation` not `find_free_port()` (race condition).

---

## Running Tests

**Build CEL evaluator first.** Many tests require it.

```bash
go build -o cel-evaluator ./cmd/cel-evaluator
python -m pytest tests/ -x -q --tb=short
```

### Why `-x -q --tb=short` is Mandatory

| Flag | Why |
|------|-----|
| `-x` | Stop on first failure. Prevents cascading failures from filling context. |
| `-q` | Quiet mode. Passing tests don't matter — only failures do. |
| `--tb=short` | Short tracebacks. Shows only what's needed to diagnose. |

**Without these flags:** A full verbose run produces 500+ lines of passing test names. A failing run with full tracebacks can be 200+ lines per failure. This fills context with noise.

**With these flags:** Passing run is ~5 lines. Failing run shows only the failure.

---

## Gotchas: Schemathesis

1. **Wrapped results** — `schema.get_all_operations()` returns `Ok/Err`. Call `.ok()`:
   ```python
   for result in schema.get_all_operations():
       operation = result.ok()  # Don't forget!
   ```

2. **Response constructor** — Headers are lists, content is bytes:
   ```python
   Response(
       status_code=200,
       headers={'content-type': ['application/json']},  # List!
       content=b'{"id": "abc"}',  # Bytes!
       request=prepared_request,
       elapsed=0.1,
       verify=False,
       http_version='1.1',
   )
   ```

3. **Override validate_response()** — Return pass to skip built-in validation.

4. **InferenceConfig** — Not exported. Access via:
   ```python
   from schemathesis.config import StatefulPhaseConfig
   _InferenceConfig = type(StatefulPhaseConfig().inference)
   ```

5. **Non-ASCII headers** — Executor sanitizes by replacing with `?`.

6. **Control chars in URLs** — Executor percent-encodes ASCII control characters (0x00-0x1F, 0x7F) in URL paths. httpx rejects them with `InvalidURL` otherwise.

## Gotchas: Comparison Rules

1. **Override, not merge** — Operation rules completely replace defaults for keys they define.
2. **unordered_array** — Doesn't handle duplicates. `[1,1,2]` matches `[1,2,2]`.
3. **Escape strings in CEL** — `foo"bar` becomes `"foo\"bar"`.
4. **Headers are lists** — First value only used for comparison.

## Gotchas: CEL Evaluator

1. **Flush writes** — `proc.stdin.flush()` required after every write.
2. **NDJSON** — One JSON per line. Newlines must be escaped.
3. **Handle EOF** — EOF on stdout = subprocess died. Restart (max 3 times).
4. **CEL errors** — Return `{"ok":false,"error":"..."}`, not exceptions.
5. **5-second timeout** — Go evaluator times out at 5s per expression.
6. **Python timeout** — Use `select.select()` with 10s before readline().

## Gotchas: Replay

1. **Classification by pattern** — Compares mismatch_type + paths, not values.
2. **DIFFERENT MISMATCH expected** — After rule changes, same bundle may fail differently.
3. **No spec in replay** — Chain replay parses link_source from stored data.
4. **--in is explore output** — Not a specific bundle path.

## Gotchas: Task Tool

1. **Save agentId** — Returned at end of each Task call.
2. **Resume limit** — ~2 resumes reliably. After 2-3, may hit API errors.

---

## Design Decision Format

When adding to DESIGN.md:

```markdown
# Descriptive Name

Keywords: searchable terms for grep
Date: YYYYMMDD

Paragraph explaining the problem, the decision, and WHY.
```

---

## Common Tasks Reference

### Adding a New Predefined Comparison

1. Add to `prototype/comparison-rules/comparison_library.json`
2. Add test in `tests/test_comparator.py`
3. Document in `docs/comparison-rules.md`

### Adding a New CLI Option

1. Add to Click command in `api_parity/cli.py`
2. Thread through to component
3. Add to CLI reference in `docs/configuration.md`
4. Add integration test if behavioral

### Debugging a CEL Expression

```python
from api_parity.cel_evaluator import CELEvaluator
cel = CELEvaluator()
result = cel.evaluate("a == b", {"a": 1, "b": 2})
print(result)  # False
cel.close()
```

### Understanding a Mismatch Bundle

```bash
# In a mismatch directory
cat metadata.json  # Run context, seed, targets
cat case.json      # Request that was sent
cat diff.json      # What differed and where
cat target_a.json  # Full response from A
cat target_b.json  # Full response from B
```

---

## Error Handling Patterns

**CEL Errors vs Infrastructure Errors:**
- CEL evaluation error (bad expression) → Records mismatch with `rule: "error: <message>"`, run continues
- CEL subprocess crash → Raises `CELSubprocessError`, auto-restarts up to 3 times

**HTTP Errors:**
- Connection error → `ResponseCase.error` set, `status_code = 0`, recorded as mismatch if other target succeeded
- 4xx/5xx responses → Normal `ResponseCase`, compared via status_code rules

**Config Validation:**
- Missing required field → `ValueError` at load time
- Unknown predefined → `ValueError` at load time
- Bad CEL syntax → Caught at runtime, recorded as mismatch
- Missing env var → `ValueError` with var name

---

## What NOT to Do

- Don't propose changes without reading relevant code
- Don't retread DESIGN.md decisions
- Don't add complexity or future-proofing
- Don't create files unless necessary
- Don't use Python CEL libraries (untrusted)
