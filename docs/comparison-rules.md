# Comparison Rules

Comparison rules define how responses are compared. Every field comparison ultimately evaluates a CEL expression with `a` (Target A value) and `b` (Target B value).

## File Structure

```json
{
  "version": "1",
  "default_rules": { ... },
  "operation_rules": {
    "<operationId>": { ... }
  }
}
```

- **default_rules** — Applied to all operations unless overridden
- **operation_rules** — Per-operation overrides, keyed by operationId

## Rule Structure

Each rule set can specify:

```json
{
  "status_code": { <comparison> },
  "headers": {
    "<header-name>": { <comparison> }
  },
  "body": {
    "field_rules": {
      "<jsonpath>": { <comparison> }
    }
  }
}
```

## Comparison Types

### Predefined Comparisons

Use built-in comparison logic:

```json
{"predefined": "exact_match"}
{"predefined": "uuid_format"}
{"predefined": "numeric_tolerance", "tolerance": 0.01}
```

### Custom CEL Expressions

Write arbitrary CEL:

```json
{"expr": "a == b"}
{"expr": "size(a) == size(b)"}
{"expr": "a > 0 && b > 0 && (a - b) <= 10"}
```

## Predefined Reference

| Name | Parameters | Description |
|------|------------|-------------|
| **Core** |
| `ignore` | — | Field completely skipped (presence defaults to optional) |
| `exact_match` | — | Values equal (`a == b`) |
| **Format Validation** |
| `uuid_format` | — | Both match UUID regex |
| `uuid_v4_format` | — | Both match UUID v4 regex |
| `url_format` | — | Both are valid URLs (http/https) |
| `iso_timestamp_format` | — | Both match ISO 8601 datetime |
| `iso_date_format` | — | Both match YYYY-MM-DD |
| `jwt_format` | — | Both are valid JWT format |
| `base64_format` | — | Both are valid base64 |
| `hex_string` | — | Both are hex strings of same length |
| **Numeric** |
| `numeric_tolerance` | `tolerance` | `\|a - b\| <= tolerance` |
| `both_positive` | — | Both > 0 |
| `both_non_negative` | — | Both >= 0 |
| `both_integer` | — | Both are integers (no decimal) |
| `same_sign` | — | Same sign (positive/negative/zero) |
| `both_in_range` | `min`, `max` | Both in [min, max] |
| **Timestamps** |
| `epoch_seconds_tolerance` | `seconds` | Epoch timestamps within N seconds |
| `epoch_millis_tolerance` | `millis` | Epoch timestamps within N milliseconds |
| **Strings** |
| `string_prefix` | `length` | First N characters match |
| `string_suffix` | `length` | Last N characters match |
| `string_contains` | `substring` | Both contain the substring |
| `string_length_match` | — | Same length |
| `string_nonempty` | — | Both non-empty |
| `both_match_regex` | `pattern` | Both match regex |
| **Arrays** |
| `unordered_array` | — | Same elements, any order (no duplicates) |
| `array_length` | — | `size(a) == size(b)` |
| `array_length_tolerance` | `tolerance` | Lengths differ by at most N |
| `array_nonempty` | — | Both have at least one element |
| **Objects** |
| `same_keys` | — | Both have identical key sets |
| `object_nonempty` | — | Both have at least one key |
| **Null/Type** |
| `both_null` | — | Both are null |
| `both_null_or_equal` | — | Both null, or both equal |
| `same_nullity` | — | Both null or both non-null |
| `both_boolean` | — | Both are boolean type |
| `type_match` | — | Same type |

## Presence Modes

Control how missing fields are handled:

```json
{"presence": "required", "predefined": "exact_match"}
{"presence": "optional", "predefined": "iso_timestamp_format"}
```

| Mode | Behavior |
|------|----------|
| `required` (default) | Field must exist in both responses. Missing = mismatch. |
| `optional` | Field may be missing from either/both. Only compared if present in both. |

## JSONPath Syntax

Field rules use JSONPath to select values:

| Pattern | Matches |
|---------|---------|
| `$.field` | Top-level field |
| `$.nested.field` | Nested field |
| `$.array[0]` | First array element |
| `$.array[*]` | All array elements |
| `$.array[*].field` | Field in each array element |
| `$..field` | Field at any depth (recursive) |

## Override Semantics

Operation rules **completely override** default rules for any key they define—no mental merging required to understand what applies. There is no deep merging.

```json
{
  "default_rules": {
    "headers": {
      "content-type": {"predefined": "exact_match"},
      "x-request-id": {"predefined": "uuid_format"}
    }
  },
  "operation_rules": {
    "createWidget": {
      "headers": {
        "location": {"predefined": "string_nonempty"}
      }
    }
  }
}
```

For `createWidget`: only `location` header is compared. `content-type` and `x-request-id` rules from defaults are **not inherited** because `createWidget` defines its own `headers` block.

To inherit and extend, repeat the defaults:

```json
"createWidget": {
  "headers": {
    "content-type": {"predefined": "exact_match"},
    "x-request-id": {"predefined": "uuid_format"},
    "location": {"predefined": "string_nonempty"}
  }
}
```

## Common Patterns

### Ignore volatile fields

```json
"$.created_at": {"predefined": "ignore"},
"$.request_id": {"predefined": "ignore"}
```

### Validate format without comparing values

```json
"$.id": {"predefined": "uuid_format"},
"$.timestamp": {"predefined": "iso_timestamp_format"}
```

### Allow small numeric differences

```json
"$.price": {"predefined": "numeric_tolerance", "tolerance": 0.01},
"$.score": {"predefined": "numeric_tolerance", "tolerance": 0.1}
```

### Compare arrays ignoring order

```json
"$.tags": {"predefined": "unordered_array"},
"$.roles": {"predefined": "unordered_array"}
```

**Warning:** `unordered_array` doesn't handle duplicates correctly. `[1,1,2]` matches `[1,2,2]`. Only use for arrays with unique elements.

### Optional fields

```json
"$.description": {"presence": "optional", "predefined": "exact_match"},
"$.updated_at": {"presence": "optional", "predefined": "iso_timestamp_format"}
```

### Custom validation logic

```json
"$.version": {"expr": "a.startsWith('v') && b.startsWith('v')"},
"$.count": {"expr": "a >= 0 && b >= 0 && a == b"}
```

## Complete Example

```json
{
  "version": "1",
  "default_rules": {
    "status_code": {"predefined": "exact_match"},
    "headers": {
      "content-type": {"predefined": "exact_match"},
      "x-request-id": {"predefined": "uuid_format"}
    },
    "body": {
      "field_rules": {}
    }
  },
  "operation_rules": {
    "createWidget": {
      "body": {
        "field_rules": {
          "$.id": {"presence": "required", "predefined": "uuid_format"},
          "$.name": {"predefined": "exact_match"},
          "$.price": {"predefined": "numeric_tolerance", "tolerance": 0.01},
          "$.tags": {"predefined": "unordered_array"},
          "$.created_at": {"predefined": "iso_timestamp_format"},
          "$.updated_at": {"presence": "optional", "predefined": "iso_timestamp_format"}
        }
      }
    },
    "healthCheck": {
      "body": {
        "field_rules": {
          "$.status": {"predefined": "exact_match"},
          "$.timestamp": {"predefined": "iso_timestamp_format"},
          "$.uptime_seconds": {"predefined": "both_positive"}
        }
      }
    }
  }
}
```
