# Schemathesis Validation Results

Date: 2026-01-08
Schemathesis Version: 4.8.0

## Executive Summary

**Schemathesis is validated as suitable for api-parity's request generation needs.** All critical requirements have been verified.

## Critical Requirements Validated

### 1. Case Generation Without HTTP Calls ✅

**Requirement:** Generate HTTP request data from OpenAPI spec without making actual calls.

**Result:** VALIDATED

- `operation.as_strategy()` returns a Hypothesis strategy
- Cases can be collected without any HTTP calls
- Generated 400 cases (50 per operation for 8 operations) in our test

**Key API:**
```python
from schemathesis.openapi import from_path

schema = from_path("spec.yaml")
for result in schema.get_all_operations():
    operation = result.ok()
    strategy = operation.as_strategy()
    # Use Hypothesis to draw cases from strategy
```

### 2. Case Object Structure ✅

**Requirement:** Cases must contain all data needed for HTTP requests.

**Result:** VALIDATED

Case objects provide:
- `method` - HTTP method
- `path` - Path template (e.g., `/items/{item_id}`)
- `path_parameters` - Dict of path parameter values
- `formatted_path` - Rendered path with parameters substituted
- `query` - Query parameters
- `headers` - HTTP headers
- `cookies` - Cookies
- `body` - Request body (for POST/PUT)
- `media_type` - Content-Type
- `as_transport_kwargs()` - Ready-to-use request parameters
- `as_curl_command()` - Curl representation for debugging

### 3. Chain Generation (Stateful Testing) ✅

**Requirement:** Generate multi-step request sequences following OpenAPI links.

**Result:** VALIDATED

- `schema.as_state_machine()` creates a Hypothesis state machine
- State machine automatically discovers transitions from OpenAPI links
- Generated 179 chains with max depth of 6 steps
- Average chain length: 3.7 steps

### 4. Variable Extraction Between Chain Steps ✅

**Requirement:** Extract data from step N's response to use in step N+1's request.

**Result:** VALIDATED

When POST /items returns `{"id": "abc123"}`, subsequent GET /items/{item_id}
correctly uses `item_id=abc123`. Variable extraction follows OpenAPI link
definitions (e.g., `$response.body#/id`).

### 5. OpenAPI Link Discovery ✅

**Requirement:** Automatically discover stateful chains from OpenAPI links.

**Result:** VALIDATED

Our test spec defined links like:
```yaml
responses:
  '201':
    links:
      GetItem:
        operationId: getItem
        parameters:
          item_id: '$response.body#/id'
```

Schemathesis generated transitions like:
- `POST_items___201_GetItem__GET_items_item_id_` (create → get)
- `POST_items___201_DeleteItem__DELETE_items_item_id_` (create → delete)
- 23 total transitions discovered

## Generated Artifacts

### Individual Cases (generated_cases.json)
- 400 cases across 8 operations
- Good variety in generated values
- Path parameters correctly substituted
- Query parameter combinations varied
- Request bodies match schema constraints

### Chains (generated_chains.json)
- 179 chains generated
- 70 unique operation sequences
- Chain lengths: 1-6 steps
- CRUD patterns: 39 chains with create→get/update/delete patterns

## Edge Cases Observed

1. **URL-encoded garbage in some path parameters**
   - One chain had `item_id: '%C3%8F%C2%93%16%F2%9B%80%95'`
   - This is fuzz testing behavior - intentionally generates invalid data
   - For api-parity, we may want to filter to "positive" test cases only

2. **Response mocking required for chains**
   - State machine's `call()` method must be overridden
   - Mock responses must include fields that links reference (e.g., `id`)
   - See `generate_chains.py` for example implementation

## Integration Approach for api-parity

### For Stateless Tests
```python
schema = from_path("spec.yaml")
for result in schema.get_all_operations():
    operation = result.ok()
    for case in generate_cases(operation, count=50):
        # case contains all request data
        # Execute against both targets and compare
```

### For Stateful Chains
1. Subclass `OpenAPIStateMachine`
2. Override `call()` to execute against both targets
3. Capture cases and responses at each step
4. Compare responses between targets
5. Store chain execution trace for mismatch bundles

## Additional Validation (Follow-up Testing)

### 6. GenerationMode.POSITIVE for Valid-Only Data ✅

**Concern:** Default generation includes invalid/garbage data (negative testing).

**Result:** VALIDATED - `GenerationMode.POSITIVE` filters to schema-valid data only.

```python
strategy = operation.as_strategy(generation_mode=GenerationMode.POSITIVE)
```

Test results for `getItem` with UUID path parameter:
- POSITIVE mode: 30/30 valid UUIDs
- NEGATIVE mode: 0/30 valid UUIDs (all garbage like `%C3%8F%C2%93...`)

**Recommendation:** Use `GenerationMode.POSITIVE` for api-parity since we want to compare behavior on valid inputs, not error handling for garbage.

### 7. Chain Behavior After Errors (Schemathesis Default)

**Concern:** What happens when a chain step returns 4xx/5xx?

**Result:** Schemathesis chains continue after errors by default.

When step N returns 404:
- Step N+1 still executes
- Variable extraction may use stale/missing data
- Chain does not abort

**⚠️ SUPERSEDED:** api-parity stops chains on mismatch, not error. If both targets return 404, that's parity—chain continues. If A returns 404 and B returns 200, that's a mismatch—chain stops. See DESIGN.md "Chain Stops at First Mismatch".

## Recommendations

1. **Use Schemathesis as the generator** - It meets all requirements
2. **Use GenerationMode.POSITIVE** - Generates schema-valid data only
3. **Stop chain on mismatch** - Override Schemathesis default; stop when targets diverge, not on error

## Files in This Prototype

- `sample_api.yaml` - Test OpenAPI spec with links
- `generate_cases.py` - Individual case generation
- `generate_chains.py` - Chain generation with mock responses
- `generated_cases.json` - 400 generated cases
- `generated_chains.json` - 179 generated chains
