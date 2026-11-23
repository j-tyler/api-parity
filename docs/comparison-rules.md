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

## Response schema selection

Explore and replay use each target’s response status and `Content-Type` to select its
OpenAPI schema. For example, an endpoint can declare an integer `id` in
`application/vnd.example+json` and a string for `text/plain`. Each response is
checked against its own declaration. An exact type takes precedence over
a media range such as `text/*` or `*/*`. If no declaration matches, a bare
`application/json` declaration for that status still checks the captured body
and its extra fields. This keeps existing checks active when servers return
an undeclared type such as `application/problem+json`. Configured comparison
rules still run when there is no applicable schema.

Text stays a string in artifacts and field rules. A matched text schema can
also validate a JSON-form number or boolean, so a response of `42` can satisfy
an integer schema and its bounds. For a matched XML representation, use field
rules against the captured tree, such as `$.Item.count`. The executor retains
the XML root and string leaf values, so it skips automatic body schema checks
for that representation.

### JSON null, empty bodies, and malformed JSON

The four bytes `null` are a present JSON value. They satisfy a nullable schema
and participate in root (`$`) field rules. A response carrying `null` on one
target and an empty body on the other produces a mismatch. Two empty HTTP 204
responses can still match.

If an applicable schema or a JSON field rule needs to check a response that
is advertised as JSON but fails to parse, it reports an `invalid_json` difference. The target artifact retains its bytes in `body_base64` and records
`json_body_state: "invalid"`. A captured null records `json_body_state: "null"`.
With no applicable schema or JSON field rules, an explicit `binary_rule` can
compare the captured bytes directly.

## Fields outside the response schema

For each object, including objects inside arrays, the OpenAPI schema controls which
extra fields are allowed. An allowed, undeclared field must be present on both
targets with equal JSON values by default. A forbidden field produces a schema
violation before body comparison.

Use an ordinary body field rule to control an allowed extra field. For example,
this accepts different valid timestamps and ignores an internal ID:

```json
{
  "body": {
    "field_rules": {
      "$[*].updated_at": {"predefined": "iso_timestamp_format"},
      "$[*].internal_id": {"predefined": "ignore"}
    }
  }
}
```

Explicit rules retain their usual presence and value checks. Other extra fields
still use the default. Ignoring an entire object or array also ignores its
contents. A presence check on a declared container still allows its extra fields
to be compared.

For default comparisons, repeated array differences are grouped by field and by
presence or value failure. In `diff.json`, `path` and the two target values show
the first differing location. `path_pattern` replaces array indices with `[*]`,
and `occurrences` counts the differences in the group. Replay and merge use the
pattern so the same failing field is recognizable as page lengths change.

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

## Numeric precision

Use the standard `exact_match` or `both_null_or_equal` rule for integer IDs and counters.
These rules compare two JSON integers exactly, including inside arrays and objects.
When either number contains a decimal point or an exponent, they compare the pair
as doubles so equivalent serializer spellings such as `12345678901234567000` and
`1.2345678901234567e19` match. An integer outside the double range differs from
every finite double.

Custom CEL expressions and other predefined rules evaluate JSON numbers as doubles.
For example, `9007199254740992` and `9007199254740993` round to the same double.
A custom `a == b`, `numeric_tolerance` with zero tolerance, and `unordered_array`
on lists of these IDs can therefore report a match.

## Predefined Reference

| Name | Parameters | Description |
|------|------------|-------------|
| **Core** |
| `ignore` | — | Field completely skipped (presence defaults to optional) |
| `exact_match` | — | Equal JSON values, preserving integer precision in scalars, arrays, and objects |
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

Control how missing fields are handled. Presence is checked before value comparison.

```json
{"presence": "parity", "predefined": "exact_match"}
{"presence": "optional", "predefined": "iso_timestamp_format"}
{"presence": "forbidden"}
```

| Mode | Behavior |
|------|----------|
| `parity` (default) | Both must have the field, or both must lack it. If both lack it, pass. If only one has it, mismatch. |
| `required` | Field must exist in both responses. Missing from either = mismatch, even if both lack it. |
| `forbidden` | Field must be absent from both responses. Present in either = mismatch. No value comparison. |
| `optional` | Compare only if both have the field. If either or both lack it, pass. |

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

Multi-match rules pair values by their concrete JSON locations, including
array indices and object keys. For example, moving `id` from `items[0]` on A
to `items[1]` on B produces two one-sided fields under default `parity`.

A `required` wildcard query must find a match on both targets, and the matched
locations must agree. Both targets may omit the field at the same position in
a sparse array. An `optional` rule compares values only at shared locations.
For `[*]` over an array present on both targets, a length difference is reported
once even when its selected field is optional.

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
