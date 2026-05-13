# Dataverse MCP — Tool Reference

Full tool catalog grouped by domain. Each entry shows: purpose, key input
parameters, and return shape.

---

## Environment & Identity

### `dataverse_list_environments`
List all Power Platform environments the authenticated user can access.

**Returns:** `{ environments: [...], count }`

---

### `dataverse_whoami`
Return the current user's ID, business unit, and organization from the connected
Dataverse environment. Use this first to confirm identity and org URL.

**Returns:** `{ UserId, BusinessUnitId, OrganizationId }`

---

### `dataverse_get_entity_sets`
Return the OData entity set name for one or more tables. Use when you have a
`table_logical_name` but need the `entity_set_name` for `query_table`/`get_record`.

| Param | Type | Description |
|-------|------|-------------|
| `table_logical_names` | `list[str]` | One or more logical names |

**Returns:** `{ entity_sets: [{logical_name, entity_set_name}], count }`

---

### `dataverse_retrieve_user_privileges`
List all security privileges held by the current (or specified) user.

| Param | Type | Description |
|-------|------|-------------|
| `user_id` | `str \| None` | GUID of user; defaults to current user |

**Returns:** `{ privileges: [{PrivilegeId, PrivilegeName, Depth}], count }`

---

### `dataverse_retrieve_principal_access`
Return the access rights a principal (user or team) has on a specific record.

| Param | Type | Description |
|-------|------|-------------|
| `target_record_id` | `str` | GUID of the target record |
| `target_table_logical_name` | `str` | Logical name of the target table |
| `principal_id` | `str \| None` | GUID of user/team; defaults to current user |
| `principal_type` | `str` | `"systemuser"` or `"team"` |

**Returns:** `{ access_rights: [...] }`

---

## Solutions

### `dataverse_list_solutions`
List solutions installed in the environment.

| Param | Type | Description |
|-------|------|-------------|
| `top` | `int` | Max results (default 50) |
| `filter` | `str \| None` | OData filter string |

**Returns:** `{ solutions: [{uniquename, friendlyname, version, ismanaged}], count, has_more }`

---

### `dataverse_get_solution`
Get details for a single solution by unique name.

| Param | Type | Description |
|-------|------|-------------|
| `solution_unique_name` | `str` | e.g. `"Default"`, `"mySolution"` |

**Returns:** `{ solution: {...} }`

---

### `dataverse_list_solution_components`
List all components in a solution with their type codes and display names.

| Param | Type | Description |
|-------|------|-------------|
| `solution_unique_name` | `str` | Solution unique name |
| `component_type` | `int \| None` | Filter by type code (e.g. `1` = Table/Entity) |
| `top` | `int` | Max results (default 50) |

**Common component type codes:** `1` = Entity, `2` = Attribute, `3` = Relationship,
`9` = OptionSet/Choice, `61` = SystemForm, `26` = SavedQuery

**Returns:** `{ components: [{objectid, componenttype, componenttype_display}], count, has_more }`

---

## Schema — Read

### `dataverse_list_tables`
List all tables (entities) in the environment.

| Param | Type | Description |
|-------|------|-------------|
| `search` | `str \| None` | Filter by display name or logical name substring |
| `top` | `int \| None` | Max results; `None` = all (can be 800+) |

**Returns:** `{ tables: [{LogicalName, DisplayName, TableType, ...}], count }`

---

### `dataverse_get_table_metadata`
Get full metadata for a single table including display names, ownership type,
primary key/name columns, and capabilities.

| Param | Type | Description |
|-------|------|-------------|
| `table_logical_name` | `str` | e.g. `"account"` |

**Returns:** `{ table: { LogicalName, DisplayName, PrimaryIdAttribute, ... } }`

---

### `dataverse_list_columns`
List columns (attributes) on a table.

| Param | Type | Description |
|-------|------|-------------|
| `table_logical_name` | `str` | |
| `search` | `str \| None` | Substring filter on logical/display name |
| `attribute_type` | `str \| None` | Filter by type e.g. `"String"`, `"Lookup"`, `"Picklist"` |
| `top` | `int` | Max results (default 50) |

**Returns:** `{ columns: [{LogicalName, DisplayName, AttributeType, ...}], count, has_more }`

---

### `dataverse_get_column`
Get detailed metadata for a single column.

| Param | Type | Description |
|-------|------|-------------|
| `table_logical_name` | `str` | |
| `column_logical_name` | `str` | |

**Returns:** Full attribute metadata object (type-specific properties included)

---

### `dataverse_list_choice_column_options`
List option values for a Picklist or MultiSelectPicklist column.

| Param | Type | Description |
|-------|------|-------------|
| `table_logical_name` | `str` | |
| `column_logical_name` | `str` | Must be Picklist or MultiSelectPicklist type |

**Returns:** `{ options: [{Value, Label}], count }`

---

### `dataverse_list_relationships`
List relationships for a table (OneToMany, ManyToOne, ManyToMany — all fetched in
parallel).

| Param | Type | Description |
|-------|------|-------------|
| `table_logical_name` | `str` | |
| `relationship_type` | `str \| None` | `"one_to_many"`, `"many_to_one"`, `"many_to_many"` |
| `top` | `int` | Max per type (default 50) |

**Returns:** `{ one_to_many: [...], many_to_one: [...], many_to_many: [...], total_count }`

---

### `dataverse_get_relationship`
Get metadata for a single relationship by schema name.

| Param | Type | Description |
|-------|------|-------------|
| `relationship_schema_name` | `str` | e.g. `"account_contacts"` |
| `relationship_type` | `str` | `"one_to_many"`, `"many_to_one"`, `"many_to_many"` |

---

### `dataverse_list_choices`
List global choice sets (option sets) in the environment.

| Param | Type | Description |
|-------|------|-------------|
| `search` | `str \| None` | Substring filter |
| `top` | `int` | Default 50 |

**Returns:** `{ choices: [{Name, DisplayName, Options:[{Value,Label}]}], count, has_more }`

---

### `dataverse_get_choice`
Get a single global choice set by name.

| Param | Type | Description |
|-------|------|-------------|
| `choice_name` | `str` | e.g. `"incident_prioritycode"` |

---

### `dataverse_check_relationship_eligibility`
Check if a table can participate in a relationship type.

| Param | Type | Description |
|-------|------|-------------|
| `table_logical_name` | `str` | |
| `check_type` | `str` | `"referenced"`, `"referencing"`, or `"many_to_many"` |

**Returns:** `{ eligible: true/false }`

---

## Schema — Write

All write tools accept `allow_write=True` (or `allow_delete=True`) to execute.
Default is **preview mode** — returns the request that *would* be sent.

Always call `dataverse_publish_customizations` after a schema change session.

### Table operations
- `dataverse_create_table(allow_write)` — create a new custom table
- `dataverse_update_table(allow_write)` — update display name, ownership, capabilities
- `dataverse_delete_table(allow_delete)` — ⚠️ destructive; deletes all data

### Column operations
- `dataverse_create_column(allow_write)` — add a column (type-specific params)
- `dataverse_update_column(allow_write)` — update display name, requirements, etc.
- `dataverse_delete_column(allow_delete)` — ⚠️ destructive

### Relationship operations
- `dataverse_create_one_to_many_relationship(allow_write)` — creates a lookup column
- `dataverse_create_many_to_many_relationship(allow_write)` — junction table
- `dataverse_create_multi_table_lookup(allow_write)` — polymorphic lookup
- `dataverse_update_relationship(allow_write)` — update display name/behaviour
- `dataverse_delete_relationship(allow_delete)` — ⚠️ destructive

### Choice operations
- `dataverse_create_choice(allow_write)` — global option set
- `dataverse_update_choice(allow_write)` — rename a global choice
- `dataverse_delete_choice(allow_delete)` — ⚠️ destructive
- `dataverse_add_choice_option(allow_write)` — add an option to a choice
- `dataverse_update_choice_option(allow_write)` — rename/recolor an option
- `dataverse_delete_choice_option(allow_delete)` — remove an option
- `dataverse_reorder_choice_options(allow_write)` — reorder display sequence

### Publish
- `dataverse_publish_customizations(allow_write)` — publish specific components
  or all unpublished customizations. **Required after any schema change.**

---

## Records

### `dataverse_query_table`
Query records using OData. Uses **entity set name** (e.g. `accounts`), not logical
name.

| Param | Type | Description |
|-------|------|-------------|
| `entity_set_name` | `str` | OData collection name — use `get_entity_sets` if unsure |
| `select` | `list[str]` | Column names to return (always specify) |
| `filter` | `str \| None` | OData `$filter` expression |
| `orderby` | `list[str] \| None` | e.g. `["createdon desc"]` |
| `top` | `int` | Max records (default 50, max 5000) |
| `expand` | `list[str] \| None` | Navigation property expansion (max 15) |
| `count` | `bool` | Include `total_count` (capped at 5000) — default `False` |
| `include_formatted_values` | `bool` | Include human-readable labels for choice/lookup fields — default `False` |

**Returns:** `{ records: [...], count, has_more }` — plus `total_count` if `count=True`

---

### `dataverse_get_record`
Fetch a single record by GUID.

| Param | Type | Description |
|-------|------|-------------|
| `entity_set_name` | `str` | OData collection name |
| `record_id` | `str` | GUID |
| `select` | `list[str] \| None` | Column names |
| `include_formatted_values` | `bool` | Include human-readable labels — default `False` |

**Returns:** `{ record: {...} }`

---

### `dataverse_count_records`
Count records in a table, optionally filtered. Capped at 5,000 by Dataverse.

| Param | Type | Description |
|-------|------|-------------|
| `entity_set_name` | `str` | OData collection name |
| `filter` | `str \| None` | OData `$filter` to count only matching records |

**Returns:** `{ total_count: int, capped: bool }` — `capped=true` means actual count may exceed 5000

---

### `dataverse_aggregate_table`
Aggregate data using OData `$apply` expressions (groupby, sum, avg, min, max, count,
distinct). Works on up to 50,000 records.

| Param | Type | Description |
|-------|------|-------------|
| `entity_set_name` | `str` | OData collection name |
| `apply` | `str` | `$apply` expression (see examples below) |
| `filter` | `str \| None` | Pre-aggregation filter |

**Examples:**
- Count by status: `groupby((statecode),aggregate(accountid with count as total))`
- Sum a column: `aggregate(revenue with sum as total_revenue)`
- Avg/min/max: `aggregate(numberofemployees with avg as avg_emp)`
- Distinct values: `groupby((ownerid))`

**Returns:** `{ records: [...], count }`

### `dataverse_associate_records`
Create a relationship between two existing records.

| Param | Type | Description |
|-------|------|-------------|
| `source_entity_set` | `str` | Source collection |
| `source_id` | `str` | Source record GUID |
| `relationship_name` | `str` | Schema name of relationship |
| `target_entity_set` | `str` | Target collection |
| `target_id` | `str` | Target record GUID |
| `allow_write` | `bool` | Default `False` (preview) |

---

### `dataverse_disassociate_records`
Remove a relationship between two records.

| Param | Type | Description |
|-------|------|-------------|
| Same as `associate_records` | | |
| `allow_delete` | `bool` | Default `False` (preview) |

---

### `dataverse_merge_records`
Merge two records of the same table type (master wins).

| Param | Type | Description |
|-------|------|-------------|
| `target_id` | `str` | Master record GUID |
| `subordinate_id` | `str` | Record to merge into master |
| `entity_set_name` | `str` | Collection name |
| `allow_write` | `bool` | Default `False` (preview) |

---

### `dataverse_execute_batch`
Execute multiple OData operations in a single HTTP batch request. Use for bulk
creates, updates, or deletes to reduce round-trips.

| Param | Type | Description |
|-------|------|-------------|
| `operations` | `list[dict]` | Array of `{method, url, body?}` operations |
| `allow_write` | `bool` | Default `False` (preview) |

**Returns:** `{ responses: [{status, body}], count }`
