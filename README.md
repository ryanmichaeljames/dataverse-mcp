# Dataverse MCP Server

[![PyPI](https://img.shields.io/pypi/v/dataverse-mcp)](https://pypi.org/project/dataverse-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/dataverse-mcp)](https://pypi.org/project/dataverse-mcp/)
[![License: MIT](https://img.shields.io/github/license/ryanmichaeljames/dataverse-mcp)](LICENSE)

An [MCP](https://modelcontextprotocol.io/) server that gives AI agents structured access to Microsoft Dataverse — query records, inspect metadata, manage schema, manage model-driven app forms, views, and apps, manage plug-in trace logging, and explore Power Platform environments.

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk), `httpx`, and the Dataverse OData v4.0 Web API. Communicates over **stdio** and works with Claude, GitHub Copilot, and any MCP-compatible client.

---

## Quick Start

**1. Install uv**

```bash
pip install uv
```

**2. Configure** — add to your MCP client config:

**Claude** (`claude_desktop_config.json` or `.claude/settings.json`):

```json
{
  "mcpServers": {
    "dataverse-mcp": {
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    }
  }
}
```

**GitHub Copilot** (`.vscode/mcp.json`):

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

That's it. Your AI agent can now query your Dataverse environments.

---

## Installation

### Install uv

`uvx` is provided by [uv](https://docs.astral.sh/uv/). Install it first if you don't have it:

```bash
pip install uv
```

### Run from PyPI (recommended)

```bash
uvx dataverse-mcp
```

`uvx` downloads and runs the package in an isolated environment — no virtual environment management required.

### Run from a local checkout

```bash
git clone https://github.com/ryanmichaeljames/dataverse-mcp.git
cd dataverse-mcp
uv sync
```

This creates `.venv`. Use the local source MCP config shown in [Client Setup](#client-setup) to point your client at it. No build step required — code changes are picked up on the next server start.

---

## Configuration

Set these in the `env` block of your MCP server entry. This project does not use a `.env` file.

| Variable | Default | Description |
|----------|---------|-------------|
| `DATAVERSE_AUTH_TYPE` | `azure_cli` | Authentication method: `azure_cli` or `interactive` |
| `DATAVERSE_URL` | — | Fallback org URL used when a tool call omits `dataverse_url` |
| `DATAVERSE_ALLOW_WRITE` | `false` | Set to `true` to register create, update, associate, merge, and schema mutation tools |
| `DATAVERSE_ALLOW_DELETE` | `false` | Set to `true` to register delete and disassociate tools |
| `DATAVERSE_ALLOWED_HOST_SUFFIXES` | — | Comma-separated extra hostname suffixes to accept for `dataverse_url` (e.g., `.contoso.internal`). Standard Dataverse domains (`.dynamics.com`, `.dynamics-int.com`, and sovereign cloud equivalents) are always allowed; all other hosts are rejected to prevent requests and tokens being directed at unknown servers |

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

## Client Setup

### Claude

#### Claude Desktop

Add to `claude_desktop_config.json`:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "dataverse-mcp": {
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    }
  }
}
```

#### Claude Code

Add via the CLI:

```bash
claude mcp add dataverse-mcp --env DATAVERSE_AUTH_TYPE=azure_cli uvx dataverse-mcp
```

Or add directly to `.claude/settings.json` (project) or `~/.claude/settings.json` (user):

```json
{
  "mcpServers": {
    "dataverse-mcp": {
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    }
  }
}
```

#### Run from a local checkout

```json
{
  "mcpServers": {
    "dataverse-mcp-local": {
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

---

### GitHub Copilot

Add to `.vscode/mcp.json` in your project root.

#### Run from PyPI

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

#### Run from a local checkout

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

---

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

These 38 tools are registered regardless of safety guard settings.

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
| `dataverse_get_cloud_flows` | Get cloud flows by query, and optionally scope to a solution by ID or unique name |
| `dataverse_query_table` | Query records from any table with filter, select, orderby, expand, and top |
| `dataverse_get_record` | Get a single record by entity set name and GUID |
| `dataverse_count_records` | Count table rows with optional filter support |
| `dataverse_aggregate_table` | Execute aggregate queries (sum, avg, min, max, countdistinct) with optional grouping |
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
| `dataverse_list_plugin_type_statistics` | List runtime performance statistics (execution count, failure rate, crash metrics) for plug-in types |
| `dataverse_get_plugin_trace_log_setting` | Get the current organization-wide plug-in trace log verbosity (off / exception / all) |
| `dataverse_list_plugin_trace_logs` | List plug-in trace log records with filters for class name, message, entity, operation type, exceptions-only, and time window |
| `dataverse_list_connection_references` | List connection references with optional filters for connector ID, status, and OData expression |
| `dataverse_get_connection_reference` | Get a single connection reference by GUID or logical name |
| `dataverse_list_forms` | List model-driven app forms for a table with optional form type filter |
| `dataverse_get_form` | Get a form's layout as a structured tabs → sections → controls tree |
| `dataverse_validate_formxml` | Validate FormXml against structural XSD rules; pass `formxml` for a dry-run on a proposed string without fetching from Dataverse |
| `dataverse_list_views` | List saved views (savedquery records) for a table with optional query type filter |
| `dataverse_get_view` | Get a view's FetchXml, LayoutXml, and column list |
| `dataverse_validate_view` | Validate a view's FetchXml and LayoutXml against structural rules |
| `dataverse_list_apps` | List model-driven apps; set `include_unpublished=true` to include drafts |
| `dataverse_get_app` | Get a model-driven app's properties and component list grouped by type |
| `dataverse_validate_app` | Validate a model-driven app using ValidateApp — surfaces missing sitemap and other errors |

### Requires `DATAVERSE_ALLOW_WRITE=true`

These 43 tools are only registered when `DATAVERSE_ALLOW_WRITE=true` is set.

| Tool | Description |
|------|-------------|
| `dataverse_associate_records` | Associate two records via a collection-valued navigation property |
| `dataverse_merge_records` | Merge a subordinate record into a target record (account, contact, lead, incident) |
| `dataverse_create_publisher` | Create a Dataverse publisher with unique name and customization prefixes |
| `dataverse_update_publisher` | Update mutable publisher fields by publisher GUID |
| `dataverse_create_solution` | Create a solution with display name, version, and publisher binding |
| `dataverse_update_solution` | Update mutable solution fields by solution GUID or unique name |
| `dataverse_update_solution_version` | Update only the version of an existing solution |
| `dataverse_add_component_to_solution` | Add a component to a solution via the `AddSolutionComponent` action |
| `dataverse_enable_cloud_flow` | Enable a single cloud flow by workflow ID |
| `dataverse_batch_enable_cloud_flows` | Enable multiple cloud flows in one `$batch` request with per-item results |
| `dataverse_disable_cloud_flow` | Disable a single cloud flow by workflow ID |
| `dataverse_batch_disable_cloud_flows` | Disable multiple cloud flows in one `$batch` request with per-item results |
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
| `dataverse_set_plugin_trace_log_setting` | Set the organization-wide plug-in trace log verbosity: `off`, `exception`, or `all` |
| `dataverse_create_connection_reference` | Create a connection reference with optional immediate connection assignment and optional solution association |
| `dataverse_update_connection_reference` | Assign or clear a connection on a reference, update display name/description, or associate with a solution |
| `dataverse_set_formxml` | Replace a form's FormXml directly and publish; validates before writing, returns `formxml_backup` for revert |
| `dataverse_add_form_control` | Add a column control to a form — resolves classid from column metadata automatically |
| `dataverse_remove_form_control` | Remove a column control from a form by logical name |
| `dataverse_create_view` | Create a new saved view with FetchXml and LayoutXml |
| `dataverse_update_view` | Update an existing view's FetchXml, LayoutXml, name, or description |
| `dataverse_add_view_column` | Add a column to a view's LayoutXml |
| `dataverse_remove_view_column` | Remove a column from a view's LayoutXml |
| `dataverse_create_app` | Create a model-driven app with auto-generated sitemap, entity components, validation, and publish |
| `dataverse_update_app` | Update a model-driven app's name or description |
| `dataverse_add_app_components` | Add tables, forms, views, charts, or BPFs to a model-driven app |
| `dataverse_remove_app_components` | Remove components from a model-driven app |
| `dataverse_set_app_sitemap` | Create or replace a model-driven app's navigation sitemap from a table list or structured areas |
| `dataverse_publish_app` | Publish a model-driven app to make it visible to users |
| `dataverse_assign_app_role` | Associate or disassociate a security role with a model-driven app |

### Requires `DATAVERSE_ALLOW_DELETE=true`

These 8 tools are only registered when `DATAVERSE_ALLOW_DELETE=true` is set.

| Tool | Description |
|------|-------------|
| `dataverse_delete_connection_reference` | Delete an unmanaged connection reference (managed ones must be removed via their solution) |
| `dataverse_disassociate_records` | Remove an association between two records |
| `dataverse_remove_component_from_solution` | Remove a component from a solution via the `RemoveSolutionComponent` action |
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
