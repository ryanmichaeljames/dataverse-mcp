---
name: dataverse-mcp
description: >
  Use dataverse-mcp tools to interact with Microsoft Dataverse environments — query
  records, explore and modify schema (tables, columns, relationships, choices),
  manage solutions, and inspect security/access. Use this skill whenever the user
  mentions Dataverse, Power Platform, Dynamics 365, CRM data, tables, entities,
  columns, attributes, relationships, choices, option sets, solutions, or any task
  that involves reading or changing data or schema in a Dataverse environment. Also
  trigger when the user asks about records, lookups, OData queries, or publishing
  customizations.
---

# Dataverse MCP

**Load [📋 Tool Reference](./reference/tools.md)** for parameter details and return
shapes. **Load [🔍 OData Patterns](./reference/odata-patterns.md)** for `filter`,
`expand`, and gotchas.

---

## Non-Obvious Conventions

### Logical names vs entity set names
Schema tools take `table_logical_name` — singular (e.g. `account`, `contact`).
`dataverse_query_table` and `dataverse_get_record` take `entity_set_name` — plural
(e.g. `accounts`, `contacts`). Use `dataverse_get_entity_sets` if unsure.

### Write tools default to preview
Every write/delete tool returns a preview of the request without executing it.
Pass `allow_write=True` or `allow_delete=True` to execute. Show the preview to the
user before executing unless they've already confirmed.

### Publish after schema changes
Schema changes (tables, columns, relationships, choices) don't appear in the UI
until you call `dataverse_publish_customizations`. Always publish at the end of a
schema modification session.

### `has_more` means there are more pages
All tools return `has_more: true` when results are truncated. Increase `top` or
re-query with a filter to get the rest. Max `top` for records is 5000.
For metadata lists, `top=None` fetches all pages.

---

## Tool Areas

| Area | Key tools |
|------|-----------|
| Identity & access | `whoami`, `list_environments`, `get_entity_sets`, `retrieve_user_privileges`, `retrieve_principal_access` |
| Solutions | `list_solutions`, `get_solution`, `list_solution_components` |
| Schema — read | `list_tables`, `get_table_metadata`, `list_columns`, `get_column`, `list_relationships`, `get_relationship`, `list_choices`, `get_choice`, `list_choice_column_options`, `check_relationship_eligibility` |
| Schema — write | `create/update/delete` for table, column, relationship, choice + `publish_customizations` |
| Records | `query_table`, `get_record`, `associate_records`, `merge_records`, `execute_batch` |

---

All tools return `{"error": true, "message": "..."}` on failure — never exceptions.
A `"preview"` key in the response means `allow_write`/`allow_delete` was not set.
