# CLI Analysis Findings

Analysis of `api_parity/cli.py` against tests and documentation to identify potential bugs and inconsistencies.

## Summary

After thorough analysis, I found **no functional bugs** in the CLI implementation. The CLI correctly:
- Parses all documented arguments
- Validates required parameters
- Returns correct exit codes (0 for success, non-zero for errors)
- Follows documented behavior (finding mismatches returns 0, not an error)

However, I found several **documentation inconsistencies** and **minor code inconsistencies** worth noting.

---

## Issue 1: Inconsistent Argument Validation for `graph-chains` vs `explore`

**Severity:** Minor (inconsistency, not a bug)

**Location:** `api_parity/cli.py` lines 325-340 vs lines 433-443

**Description:**
The `graph-chains` subcommand uses plain `type=int` for `--max-chains`, `--max-steps`, and `--seed`, while `explore` uses `type=positive_int` for `--max-chains` and `--max-steps`:

```python
# graph-chains (lines 325-340):
graph_chains_parser.add_argument("--max-chains", type=int, ...)
graph_chains_parser.add_argument("--max-steps", type=int, ...)
graph_chains_parser.add_argument("--seed", type=int, ...)

# explore (lines 433-443):
explore_parser.add_argument("--max-chains", type=positive_int, ...)
explore_parser.add_argument("--max-steps", type=positive_int, ...)
explore_parser.add_argument("--seed", type=int, ...)  # Note: also plain int
```

For `graph-chains`, invalid values (0, -1) are caught later in `_run_graph_chains_generated()` at lines 763-768:
```python
if args.max_chains < 1:
    print("Error: --max-chains must be >= 1", file=sys.stderr)
    return 1
if args.max_steps < 1:
    print("Error: --max-steps must be >= 1", file=sys.stderr)
    return 1
```

**Impact:** Different error messages for the same invalid input:
- `explore --max-chains 0` produces: `error: argument --max-cases: Value must be positive, got 0.`
- `graph-chains --generated --max-chains 0` produces: `Error: --max-chains must be >= 1`

**Test Coverage:** Tests cover both behaviors (test_cli_common.py tests positive_int, test_cli_graph_chains.py tests runtime validation).

---

## Issue 2: Documentation Claims Config Override Behavior Not Fully Implemented

**Severity:** Documentation inconsistency

**Location:** DESIGN.md "CLI Arguments Override Config File" section (lines 453-472)

**Description:**
DESIGN.md states:
> "Every configuration option can be specified in the configuration file. If the same option is also passed as a CLI argument, the CLI argument takes precedence for that run."

And shows:
> **Precedence order (highest to lowest):**
> 1. CLI arguments
> 2. Config file values
> 3. Built-in defaults

However, the `RuntimeConfig` model (models.py lines 461-469) only supports:
- `targets`
- `comparison_rules`
- `rate_limit`
- `secrets`

There is no support for specifying execution parameters like `max_cases`, `timeout`, `seed`, `max_chains`, `max_steps`, `exclude`, etc. in the config file. These are CLI-only options.

**Impact:** Documentation misleads users into thinking they can put execution parameters in the config file.

**Actual Behavior:** Only CLI arguments and built-in defaults exist for execution parameters. The config file is for target/rules/secrets configuration only.

---

## Issue 3: `--seed` Accepts Negative Values

**Severity:** Minor (potentially intended)

**Location:** `api_parity/cli.py` line 383

**Description:**
The `--seed` argument uses plain `type=int`, allowing negative values:
```python
explore_parser.add_argument("--seed", type=int, default=None, ...)
```

Test `test_cli_common.py::TestEdgeCases::test_negative_seed` explicitly tests that negative seeds are accepted:
```python
def test_negative_seed(self):
    """Test negative seed values are accepted."""
    args = parse_args([..., "--seed", "-1"])
    assert args.seed == -1
```

**Impact:** This is likely intentional (Python's `random` module accepts negative seeds). Not a bug, but worth noting the explicit design choice.

---

## Issue 4: No Tests for Exit Code on Mismatch Found

**Severity:** Test gap (behavior is correct)

**Location:** Tests in `test_cli_explore.py` and integration tests

**Description:**
The implementation correctly returns `return 0` from `run_explore()` (line 1280) and `run_replay()` (line 1716) even when mismatches are found. This matches the documented behavior in ARCHITECTURE.md:
> "Finding mismatches during explore is expected behavior, not an error. Mismatch count is reported in output, not exit code."

However, I did not find a test that explicitly verifies this behavior. Integration tests check `result.returncode == 0` but this passes whether mismatches are found or not.

**Recommendation:** Add an explicit test that:
1. Runs explore against servers known to produce mismatches
2. Verifies return code is 0
3. Verifies mismatches were found (count > 0)

---

## Issue 5: `replay` Does Not Support `--spec` (Expected, But Not Documented)

**Severity:** Documentation gap

**Location:** README.md CLI Reference

**Description:**
The `replay` command does not accept `--spec` because replay uses saved bundles, not the OpenAPI spec. This is correct behavior and noted in ARCHITECTURE.md:
> "Replay Mode: Schema validation is not performed during replay (no spec available)."

However, the README CLI Reference doesn't explain WHY replay doesn't need `--spec`, which could confuse users who expect symmetry with `explore`.

---

## Verified Correct Behaviors

The following documented behaviors were verified as correctly implemented:

1. **All documented arguments exist and parse correctly**
   - `list-operations --spec`
   - `graph-chains --spec [--exclude] [--generated] [--max-chains] [--max-steps] [--seed]`
   - `explore` with all documented options
   - `replay` with all documented options
   - `lint-spec --spec [--output]`

2. **Exit codes follow documentation**
   - `0` for successful completion (even with mismatches)
   - Non-zero for errors (missing args, invalid spec, config errors)

3. **Required arguments are enforced**
   - All tests verify `SystemExit` with code 2 for missing required args

4. **Positive validators work correctly**
   - `positive_int` rejects 0 and negative values
   - `positive_float` rejects 0 and negative values
   - `parse_operation_timeout` validates format and positive timeout

5. **Multiple values work correctly**
   - `--exclude` can be repeated
   - `--operation-timeout` can be repeated
   - Duplicate operation timeouts warn and use last value

6. **Default values match documentation**
   - `--timeout` defaults to 30.0
   - `--max-chains` effective default is 20 (via `args.max_chains or 20`)
   - `--max-steps` defaults to 6
   - `--output` for `lint-spec` defaults to "text"

---

## Files Analyzed

- `/home/user/api-parity/api_parity/cli.py` (1999 lines)
- `/home/user/api-parity/tests/test_cli_explore.py` (485 lines)
- `/home/user/api-parity/tests/test_cli_replay.py` (169 lines)
- `/home/user/api-parity/tests/test_cli_list_ops.py` (33 lines)
- `/home/user/api-parity/tests/test_cli_common.py` (353 lines)
- `/home/user/api-parity/tests/test_cli_graph_chains.py` (1109 lines)
- `/home/user/api-parity/tests/test_cli_lint_spec.py` (189 lines)
- `/home/user/api-parity/README.md`
- `/home/user/api-parity/ARCHITECTURE.md`
- `/home/user/api-parity/DESIGN.md`
- `/home/user/api-parity/api_parity/models.py` (RuntimeConfig)
- `/home/user/api-parity/tests/integration/test_explore_execution.py`
- `/home/user/api-parity/tests/integration/test_replay_execution.py`
