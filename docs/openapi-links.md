# OpenAPI Links for Chain Testing

OpenAPI links enable **stateful chain testing**—multi-step sequences like create→get→update→delete. api-parity auto-discovers chains from links in your OpenAPI spec.

## What Links Enable

Without links: api-parity tests each operation in isolation with generated data.

With links: api-parity executes realistic workflows:
1. POST /widgets → creates widget, extracts `id` from response
2. GET /widgets/{id} → uses extracted `id`
3. PUT /widgets/{id} → updates the created widget
4. DELETE /widgets/{id} → cleans up

Both targets execute the same chain, but each uses its own response data. If Target A returns `id: "abc"` and Target B returns `id: "xyz"`, subsequent requests use the respective IDs.

## Link Syntax

Links are defined in the `responses` section of an operation:

```yaml
paths:
  /widgets:
    post:
      operationId: createWidget
      responses:
        "201":
          description: Widget created
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Widget"
          links:
            GetWidget:
              operationId: getWidget
              parameters:
                widget_id: "$response.body#/id"
```

### Key Fields

| Field | Description |
|-------|-------------|
| `operationId` | Target operation to invoke |
| `parameters` | Map of parameter name → runtime expression |

### Runtime Expressions

| Expression | Meaning |
|------------|---------|
| `$response.body#/id` | JSON Pointer into response body |
| `$response.body#/data/user_id` | Nested field |
| `$response.header.Location` | Response header value |
| `$request.path.id` | Request path parameter |
| `$request.query.filter` | Request query parameter |

## Complete Example

```yaml
paths:
  /widgets:
    post:
      operationId: createWidget
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Widget"
          links:
            GetCreatedWidget:
              operationId: getWidget
              parameters:
                widget_id: "$response.body#/id"
            UpdateCreatedWidget:
              operationId: updateWidget
              parameters:
                widget_id: "$response.body#/id"
            DeleteCreatedWidget:
              operationId: deleteWidget
              parameters:
                widget_id: "$response.body#/id"

  /widgets/{widget_id}:
    get:
      operationId: getWidget
      parameters:
        - name: widget_id
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Widget"
          links:
            UpdateWidget:
              operationId: updateWidget
              parameters:
                widget_id: "$response.body#/id"
            DeleteWidget:
              operationId: deleteWidget
              parameters:
                widget_id: "$response.body#/id"

    put:
      operationId: updateWidget
      parameters:
        - name: widget_id
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Widget"
          links:
            GetUpdatedWidget:
              operationId: getWidget
              parameters:
                widget_id: "$response.body#/id"

    delete:
      operationId: deleteWidget
      parameters:
        - name: widget_id
          in: path
          required: true
          schema:
            type: string
      responses:
        "204":
          description: Deleted
```

This enables chains like:
- `createWidget` → `getWidget`
- `createWidget` → `updateWidget` → `getWidget`
- `createWidget` → `deleteWidget`
- `createWidget` → `getWidget` → `updateWidget` → `getWidget` → `deleteWidget`

## Verify Links

Use `list-operations` to see discovered links:

```bash
api-parity list-operations --spec openapi.yaml
```

Output:
```
createWidget
  POST /widgets
  Links:
    201 → GetCreatedWidget → getWidget
    201 → UpdateCreatedWidget → updateWidget
    201 → DeleteCreatedWidget → deleteWidget

getWidget
  GET /widgets/{widget_id}
  Links:
    200 → UpdateWidget → updateWidget
    200 → DeleteWidget → deleteWidget

updateWidget
  PUT /widgets/{widget_id}
  Links:
    200 → GetUpdatedWidget → getWidget

deleteWidget
  DELETE /widgets/{widget_id}

Total: 4 operations
```

## Chain Depth

api-parity follows links to build chains up to 6+ steps. Deeper chains find more subtle bugs but take longer to execute.

Chain depth depends on:
1. How many links you define
2. How links connect operations (cycles enable longer chains)
3. Schemathesis's exploration strategy

## Field Name Flexibility

api-parity dynamically extracts field names from link expressions. Your API can use any field name:

```yaml
# Standard
parameters:
  user_id: "$response.body#/id"

# Custom field names work too
parameters:
  user_id: "$response.body#/user_uuid"

parameters:
  user_id: "$response.body#/data/identifier"
```

No configuration needed—field names are parsed from the spec.

## Best Practices

1. **Define links on success responses** — Links on 201, 200 enable happy-path chains.

2. **Connect CRUD operations** — At minimum: create→get, create→update, create→delete.

3. **Add bidirectional links** — get→update and update→get enables update verification chains.

4. **Use descriptive link names** — `GetCreatedWidget` is clearer than `link1`.

5. **Match parameter names exactly** — Link parameter names must match target operation's parameter names.

## Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| No chains generated | No links in spec | Add `links:` sections |
| Short chains only | Links don't form cycles | Add links from get/update back to other operations |
| Parameter not found | Link param name doesn't match operation param | Check spelling, use exact param name |
| Field not extracted | JSON Pointer path wrong | Verify path matches response schema |
