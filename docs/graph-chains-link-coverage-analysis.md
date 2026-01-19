# Graph-Chains Link Coverage Analysis

Date: 2026-01-19

## Executive Summary

Investigation into why `graph-chains --generated` doesn't produce all expected links revealed that **the issue is probabilistic exploration, not broken link handling**. All spec links are correctly converted to state machine rules, but Schemathesis's random exploration doesn't guarantee visiting all paths.

## Key Findings

### 1. All Spec Links Are Converted to Rules

Schemathesis correctly converts all OpenAPI link definitions into state machine rules:
- archive_gateway_spec.yaml: 72 declared links → 72 state machine rules
- No links are lost in conversion

### 2. Two Types of State Machine Rules

Schemathesis creates two types of rules:

**LINK Rules** - Follow explicit OpenAPI links
- Format: `{METHOD}_{path}___{status}_{LinkName}__{METHOD}_{target_path}`
- Example: `GET_items___200_GetItemFromList__GET_items_itemId_`
- These require a previous step to provide parameter values

**RANDOM Rules** - Entry points without link requirements
- Format: `RANDOM__{METHOD}_{path}`
- Example: `RANDOM__GET_items`
- Created for operations without required path parameters
- These are the source of "unknown link (not in spec)" transitions

### 3. Coverage Is Limited by Exploration Strategy

| Test | max_chains | Actual Chains | Links Used | Coverage |
|------|------------|---------------|------------|----------|
| archive_gateway_spec | 100 | 146 | 21/72 | 29.2% |
| archive_gateway_spec | 500 | 146 | 21/72 | 29.2% |
| archive_gateway_spec | 1000 | 152 | 26/72 | 36.1% |
| archive_gateway_spec | 5000 | 150 | 18/72 | 25.0% |

Key observations:
- **Actual chain count caps at ~150** regardless of max_chains
- This is because Schemathesis exhausts its state space early
- Coverage doesn't improve linearly with more requested chains

### 4. Seed Variation Provides Marginal Improvement

Testing 10 different seeds (0-9) with max_chains=500:
- Individual seeds: 14-25 links each
- Cumulative across all seeds: 32/72 (44.4%)
- Still leaves 40 links unused

### 5. "Unknown Link" Transitions Are Expected Behavior

The "via unknown link (not in spec)" message appears when:
1. A RANDOM rule is used (operation called without following a link)
2. Transition between operations where no link exists in that direction

This is NOT a bug - it's how Schemathesis state machines work. RANDOM rules allow operations to be called as entry points.

## Root Cause Analysis

The core issue is a mismatch between expectations and Schemathesis behavior:

**Expectation**: Generating chains should exercise all OpenAPI links
**Reality**: Schemathesis probabilistically explores state space, favoring some paths over others

The state machine uses hypothesis.stateful which:
1. Generates random sequences of operations
2. Uses links to provide parameter values when available
3. Can use RANDOM rules to call operations without links
4. Has built-in limits on state space exploration

## Reachability Analysis

All 24 operations in archive_gateway_spec are reachable through the link graph:
- 9 entry points (operations with RANDOM rules)
- 15 operations reachable only via links
- All 72 links are from reachable operations

The issue is **not reachability** but **probability of visiting specific paths**.

## Potential Solutions

### 1. Multiple Seeds (Partial Fix)
Generate chains with multiple seeds and combine results.
- Pro: Simple to implement
- Con: Only improves coverage marginally (tested: 44.4% with 10 seeds)

### 2. Increase max_steps (Limited Effect)
Allow longer chains to reach more operations.
- Pro: Some links are only reachable through long paths
- Con: Chain count still caps due to state space exhaustion

### 3. Enumerate Link Paths Systematically (New Feature)
Instead of random exploration, systematically enumerate all possible link paths.
- Pro: Guarantees coverage of all links
- Con: Requires significant implementation effort

### 4. Bias Toward Unused Links (Enhancement)
Track which links have been used and bias generation toward unused ones.
- Pro: Improves coverage without changing fundamental approach
- Con: Requires modifying state machine behavior

### 5. Hybrid Approach (Recommended)
Combine random exploration with systematic enumeration:
1. Use `--generated` for random fuzzing (current behavior)
2. Add `--enumerate` mode that walks all link paths
3. Report which links are covered by each mode

## Recommendations

1. **Document the behavior**: Users should understand that `--generated` uses probabilistic exploration and won't guarantee 100% link coverage.

2. **Add coverage reporting**: Show a warning when link coverage is below a threshold (e.g., < 50%).

3. **Consider enumeration mode**: For thorough link testing, implement a systematic path enumeration that guarantees visiting all links.

4. **Use multiple seeds**: When coverage matters, run with multiple seeds and combine results.

## Test Cases Created

The following test fixtures were created to verify behavior:
- `tests/fixtures/debug_link_test.yaml` - Simple spec to test array index body paths
- `tests/fixtures/request_path_link_test.yaml` - Tests `$request.path.*` with `$response.body#/*`

Both test specs work correctly, confirming the link handling implementation is correct.
