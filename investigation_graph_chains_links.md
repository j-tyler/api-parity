# Investigation: graph-chains --generated Not Producing All Links

Date: 2026-01-19

## Executive Summary

Investigation found two root causes for missing links in graph-chains output:

1. **Status Code Selection Bug** (PROVEN) - When an operation has links on multiple
   status codes (e.g., 200 and 201), only the lowest status code's links are visible
   to Schemathesis. Links on higher status codes are never traversed.

2. **Probabilistic Exploration** (CONFIRMED) - Schemathesis's state machine makes
   random choices about transitions. More chains = more link coverage, but some links
   may never be selected by chance alone.

## Evidence

### Test 1: Status Code Issue Reproduction

Created `tests/fixtures/multi_status_link_test.yaml` with:
- 2 links on status 200: `GetUpdatedResource`, `ListAll200`
- 2 links on status 201: `GetCreatedResource`, `NotifyCreated`

Result:
```
Total declared links: 4
Links actually used:  2 (both from 200)
Unused links: 2 (both from 201)
  createResource --(201)--> getResource [GetCreatedResource]
  createResource --(201)--> notifyCreation [NotifyCreated]
```

**Critical**: `notifyCreation` was NEVER tested because it's ONLY reachable via the
201-response link that was never seen by Schemathesis.

### Test 2: Probabilistic Exploration Evidence

Running archive_gateway_spec with different chain counts:
- 100 chains: 38/72 links used (52%)
- 500 chains: 51/72 links used (71%)

More chains = more link coverage, proving probabilistic selection is a factor.

### Test 3: Operation Reachability Analysis

`getTermedAsset` was reached in chains but its outgoing links were never followed:
```
Chain 197: ... -> getTermedAsset (ends here, no step 6)
```
Links like `getTermedAsset --(200)--> deleteTermedAsset` were available but not selected.

## Root Cause Analysis

### Root Cause 1: `_find_status_code_with_links()` Bug

Location: `api_parity/case_generator.py` line 726

The method returns `min(status_codes_with_links)` - always the LOWEST 2xx status code
with links. When an operation has links on multiple status codes, only the lowest one's
links are discovered.

```python
# Return lowest 2xx status code with links
if status_codes_with_links:
    return min(status_codes_with_links)  # BUG: ignores higher status codes
```

**Impact in archive_gateway_spec**: Only `createOrUpdateCollection` has links on
multiple status codes (200 and 202), so impact is limited. However, other specs
may have more operations with multi-status links.

### Root Cause 2: Schemathesis State Machine Behavior

Schemathesis's state machine makes probabilistic choices about:
1. Which operation to call next
2. Whether to follow an explicit link or make a "free" transition

This is expected behavior - disabling inference algorithms only stops Schemathesis
from CREATING new links, it doesn't restrict the state machine to only follow
existing links.

### Note: "Unknown Link" Transitions

Many chains show "via unknown link (not in spec)" - this is normal. Schemathesis
can transition between any operations; links just provide variable passing.

## Proposed Fixes

### Fix 1: Randomize Status Code Selection (Recommended)

Instead of always returning the lowest status code, randomly select from all status
codes that have links:

```python
def _find_status_code_with_links(self, operation_id: str, method: str) -> int:
    # ... find all status codes with links ...
    if status_codes_with_links:
        import random
        return random.choice(status_codes_with_links)
    # Fallback
```

**Pros**: Simple fix, distributes coverage across all links over multiple runs
**Cons**: Non-deterministic with same seed

### Fix 2: Return All Status Codes (More Complex)

Generate multiple synthetic responses per operation, one for each status code with
links. This requires changes to how Schemathesis processes responses.

**Pros**: Complete coverage in a single pass
**Cons**: Requires deeper integration with Schemathesis internals

### Fix 3: Run More Chains

Simply increasing `--max-chains` improves coverage:
- 100 chains: 52% link coverage
- 500 chains: 71% link coverage
- Projected 1000+ chains: ~80%+ coverage

**Pros**: No code changes needed
**Cons**: Longer runtime, still probabilistic

### Fix 4: Link Coverage Tracking

Track which links have been used and bias exploration toward unused links.

**Pros**: Ensures complete coverage
**Cons**: Complex implementation, may fight with Schemathesis's exploration strategy

## Recommended Action

1. **Short term**: Implement Fix 1 (randomize status code selection)
2. **Short term**: Document that more chains = better coverage (Fix 3)
3. **Long term**: Consider Fix 4 for guaranteed complete coverage

## Test Files Created

- `tests/fixtures/multi_status_link_test.yaml` - Proves status code bug
- `tests/fixtures/simple_link_test.yaml` - Baseline test for link behavior

## Files Affected

- `api_parity/case_generator.py` - `_find_status_code_with_links()` method
