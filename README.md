# Dataverse MCP Server

[![PyPI](https://img.shields.io/pypi/v/dataverse-mcp)](https://pypi.org/project/dataverse-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/dataverse-mcp)](https://pypi.org/project/dataverse-mcp/)
[![License: MIT](https://img.shields.io/github/license/ryanmichaeljames/dataverse-mcp)](LICENSE)

An [MCP](https://modelcontextprotocol.io/) server that gives AI agents structured access to Microsoft Dataverse — query records, inspect metadata, manage schema, and explore Power Platform environments.

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk), `httpx`, and the Dataverse OData v4.0 Web API. Communicates over **stdio** for seamless VS Code Copilot integration.

---

## Quick Start

**1. Install**

```bash
# Run directly from PyPI — no install needed
uvx dataverse-mcp
```

**2. Configure** — add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "dataverse-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    }
  }
}
```

**3. Sign in**

```bash
az login
```

That's it. Copilot can now query your Dataverse environments.

---

## Installation

### Run from PyPI (recommended)

```bash
uvx dataverse-mcp
```

[`uvx`](https://docs.astral.sh/uv/) downloads and runs the package in an isolated environment — no virtual environment management required.

### Run from a local checkout

```bash
git clone https://github.com/ryanmichaeljames/dataverse-mcp.git
cd dataverse-mcp
uv sync
```

This creates `.venv`. Use the local source MCP config shown in [VS Code Setup](#vs-code-setup) to point VS Code at it. No build step required — code changes are picked up on the next server start.

---

## Configuration

Set these in the `env` block of your MCP server entry. This project does not use a `.env` file.

| Variable | Default | Description |
|----------|---------|-------------|
| `DATAVERSE_AUTH_TYPE` | `azure_cli` | Authentication method: `azure_cli` or `interactive` |
| `DATAVERSE_URL` | — | Fallback org URL used when a tool call omits `dataverse_url` |
| `DATAVERSE_ALLOW_WRITE` | `false` | Set to `true` to register create, update, associate, merge, and schema mutation tools |
| `DATAVERSE_ALLOW_DELETE` | `false` | Set to `true` to register delete and disassociate tools |

### Authentication

| Method | Description |
|--------|-------------|
| `azure_cli` (default) | Uses your active `az login` session. Best for local development. |
| `interactive` | Opens a browser window for interactive sign-in. |

### Safety Guards

Most write and delete tools are **not registered by default**, so they do not appear to the agent until explicitly enabled. One exception is `dataverse_execute_batch`, which is always visible but only allows GET requests unless `DATAVERSE_ALLOW_WRITE=true`. This prevents accidental mutations when you only need to read or inspect data while still allowing safe batch reads by default.

```json
{
  "servers": {
    "dataverse-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "azure_cli",
        "DATAVERSE_ALLOW_WRITE": "true",
        "DATAVERSE_ALLOW_DELETE": "true"
      }
    }
  }
}
```

Each flag is independent — set only `DATAVERSE_ALLOW_WRITE=true` to allow creates and updates while keeping deletes disabled.

---

## VS Code Setup

### Run from PyPI

```json
{
  "servers": {
    "dataverse-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    }
  }
}
```

### Run from a local checkout

```json
{
  "servers": {
    "dataverse-mcp-local": {
      "type": "stdio",
      "command": "C:\\path\\to\\dataverse-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "dataverse_mcp.server"],
      "env": {
        "PYTHONPATH": "C:\\path\\to\\dataverse-mcp\\src",
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    }
  }
}
```

### Multi-environment targeting

A single server instance can target any Dataverse org — pass `dataverse_url` on each tool call:

```json
{
  "dataverse_url": "https://yourorg.crm.dynamics.com",
  "entity_set_name": "accounts",
  "select": ["name", "accountid"],
  "top": 10
}
```

When `dataverse_url` is omitted, the server falls back to `DATAVERSE_URL` if configured. Use `dataverse_list_environments` to discover available environments first.

---

## Tools

### Always available

These 21 tools are registered regardless of safety guard settings.

| Tool | Description |
|------|-------------|
| `dataverse_list_environments` | List Power Platform environments accessible to the authenticated user |
| `dataverse_whoami` | Return the authenticated user's `UserId`, `BusinessUnitId`, and `OrganizationId` |
| `dataverse_get_entity_sets` | List OData EntitySet names from the service document |
| `dataverse_retrieve_user_privileges` | List all security privileges assigned to a system user |
| `dataverse_retrieve_principal_access` | Check access rights a user has to a specific record |
| `dataverse_list_solutions` | List solutions with optional OData filter and pagination |
| `dataverse_get_solution` | Get a single solution by unique name or GUID |
| `dataverse_list_solution_components` | List components in a solution with optional type filter |
| `dataverse_query_table` | Query records from any table with filter, select, orderby, expand, and top |
| `dataverse_get_record` | Get a single record by entity set name and GUID |
| `dataverse_execute_batch` | Execute up to 1,000 OData operations in a single `$batch` request; GET-only unless `DATAVERSE_ALLOW_WRITE=true` |
| `dataverse_list_tables` | List available tables with optional metadata filter |
| `dataverse_get_table_metadata` | Get full schema details for a specific table |
| `dataverse_list_columns` | List column definitions for a table with optional type filter |
| `dataverse_get_column` | Get full metadata for a single column including type-specific properties |
| `dataverse_list_choice_column_options` | Get all option values for a Picklist or MultiSelectPicklist column |
| `dataverse_list_relationships` | List relationship definitions for a table or the entire environment |
| `dataverse_get_relationship` | Get full metadata for a single relationship by schema name |
| `dataverse_check_relationship_eligibility` | Check whether a table can participate in a relationship before creating one |
| `dataverse_list_choices` | List all global choice (option set) definitions in the environment |
| `dataverse_get_choice` | Get a specific global choice by name or MetadataId, including all option values |

### Requires `DATAVERSE_ALLOW_WRITE=true`

These 16 tools are only registered when `DATAVERSE_ALLOW_WRITE=true` is set.

| Tool | Description |
|------|-------------|
| `dataverse_associate_records` | Associate two records via a collection-valued navigation property |
| `dataverse_merge_records` | Merge a subordinate record into a target record (account, contact, lead, incident) |
| `dataverse_create_table` | Create a new custom table with display names, ownership type, and primary name attribute |
| `dataverse_update_table` | Update an existing table's display name or description |
| `dataverse_create_column` | Add a new typed column to a table |
| `dataverse_update_column` | Update an existing column via full PUT — fetch current definition with `dataverse_get_column` first |
| `dataverse_create_one_to_many_relationship` | Create a 1:N relationship and its lookup column |
| `dataverse_create_many_to_many_relationship` | Create an N:N relationship and its intersect table |
| `dataverse_create_multi_table_lookup` | Create a polymorphic lookup column referencing multiple tables |
| `dataverse_update_relationship` | Update an existing relationship via full PUT — fetch current definition with `dataverse_get_relationship` first |
| `dataverse_create_choice` | Create a new global choice with initial options |
| `dataverse_update_choice` | Update an existing global choice via full PUT — fetch current definition with `dataverse_get_choice` first |
| `dataverse_add_choice_option` | Add a new option to a global or local choice |
| `dataverse_update_choice_option` | Update the display label of an existing choice option |
| `dataverse_reorder_choice_options` | Reorder all options in a global or local choice |
| `dataverse_publish_customizations` | Publish schema changes via `PublishXml` (targeted) or `PublishAllXml` (environment-wide) |

### Requires `DATAVERSE_ALLOW_DELETE=true`

These 6 tools are only registered when `DATAVERSE_ALLOW_DELETE=true` is set.

| Tool | Description |
|------|-------------|
| `dataverse_disassociate_records` | Remove an association between two records |
| `dataverse_delete_table` | Permanently delete a custom table and all its data |
| `dataverse_delete_column` | Permanently delete a custom column and all its data |
| `dataverse_delete_relationship` | Delete a custom relationship by MetadataId |
| `dataverse_delete_choice` | Delete a global choice by logical name |
| `dataverse_delete_choice_option` | Remove a specific option value from a global or local choice |

---

## Development

```bash
# Install dependencies
uv sync

# Run the MCP inspector (interactive testing)
uv run mcp dev src/dataverse_mcp/server.py

# Run the server directly
uv run python -m dataverse_mcp.server

# Compile check
uv run python -m py_compile src/dataverse_mcp/server.py
```

Restart the MCP server in VS Code after code changes to pick up the new source.

---

## License

MIT
