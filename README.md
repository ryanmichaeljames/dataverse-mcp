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
| `DATAVERSE_ALLOW_WRITE` | `false` | Set to `true` to register create, update, associate, merge, and schema mutation tools |
| `DATAVERSE_ALLOW_DELETE` | `false` | Set to `true` to register delete and disassociate tools |
| `DATAVERSE_WHITELIST` | — | Comma-separated list of allowed environment hostnames (e.g., `yourorg.crm.dynamics.com,yourorg-uat.crm.dynamics.com`). When set, tool calls to any environment not on the list are rejected. When empty, **all** environments are permitted — see the warning below |
| `DATAVERSE_AUTH_TIMEOUT_SECONDS` | `30` | Maximum seconds to wait for a credential acquisition (e.g., `az login` token fetch) before failing with an actionable auth error. Increase when operating in slow-network or MFA-heavy environments. Invalid or non-positive values fall back to `30` |
| `DATAVERSE_TOKEN_CACHE_PERSIST` | `true` | Controls whether `interactive` auth persists its MSAL token cache to disk so the server survives restarts without a new browser prompt (while a refresh token is valid). Set to `false` to disable and revert to in-memory-only behaviour. Invalid values fall back to `true` with a logged warning. Has no effect on `azure_cli` auth. |
| `DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED` | `false` | When `true`, permits writing the MSAL token cache to disk without OS-level encryption. Only needed on headless Linux hosts that lack a Secret Service (e.g., GNOME Keyring / libsecret). **Refresh tokens are long-lived credentials — only enable this on trusted, access-controlled hosts.** A startup warning is logged when this flag is active. Invalid values fall back to `false`. |
| `DATAVERSE_TOKEN_CACHE_PROFILE` | — | Optional name that isolates the `interactive` token cache and its `AuthenticationRecord` sidecar per profile. Set a distinct value in each session to run concurrent servers signed in to **different tenants/accounts** on the same host without them overwriting each other's cache. Must use only `[A-Za-z0-9_-]`; any other character fails fast at startup (silently sanitizing could collide two profiles and defeat isolation). Empty/unset uses the shared default filenames. Has no effect on `azure_cli` auth. |

> [!WARNING]
> **Leaving `DATAVERSE_WHITELIST` unset is risky.** Tools accept a `dataverse_url` per call, and the server mints a bearer token for whatever environment is supplied. Without a whitelist, a compromised or misbehaving agent can direct your credentials at *any* Dataverse environment. Set `DATAVERSE_WHITELIST` to the specific environment hostnames you intend to use so the server rejects everything else.

### Authentication

| Method | Description |
|--------|-------------|
| `azure_cli` (default) | Uses your active `az login` session. Best for local development. |
| `interactive` | Opens a browser window for interactive sign-in. The sign-in session persists across server restarts by default (see `DATAVERSE_TOKEN_CACHE_PERSIST`). The first launch always opens a browser; subsequent restarts reuse the cached refresh token silently while it remains valid. |

> [!NOTE]
> **Interactive auth persistence.** When `DATAVERSE_TOKEN_CACHE_PERSIST=true` (the default), the MSAL token cache is stored on disk using your OS secret store (Windows DPAPI, macOS Keychain, Linux libsecret). On headless Linux without libsecret, the first token acquisition will fail fast with an error. Set `DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED=true` to permit a plaintext cache on those hosts, and see the security warning for that variable above.

> [!NOTE]
> **Running multiple tenants/accounts at once.** The default cache and sidecar filenames are shared per host, so two `interactive` sessions signed in to different tenants/accounts would overwrite each other's pinned account. Give each session a distinct `DATAVERSE_TOKEN_CACHE_PROFILE` (e.g., `tenant-a`, `tenant-b`) to keep their caches and `AuthenticationRecord` sidecars separate.

#### Example: two tenants side by side

Register two server entries, each with its own `DATAVERSE_TOKEN_CACHE_PROFILE`. The profile is a *tenant-wide* cache key — each entry signs in once (its own browser prompt) and then restarts silently as its own account, while tools still receive the specific `dataverse_url` per call. The profiles never collide.

**Claude** (`claude_desktop_config.json` or `.claude/settings.json`):

```json
{
  "mcpServers": {
    "dataverse-tenant-a": {
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive",
        "DATAVERSE_TOKEN_CACHE_PROFILE": "tenant-a"
      }
    },
    "dataverse-tenant-b": {
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive",
        "DATAVERSE_TOKEN_CACHE_PROFILE": "tenant-b"
      }
    }
  }
}
```

**GitHub Copilot** (`.vscode/mcp.json`):

```json
{
  "servers": {
    "dataverse-tenant-a": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive",
        "DATAVERSE_TOKEN_CACHE_PROFILE": "tenant-a"
      }
    },
    "dataverse-tenant-b": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive",
        "DATAVERSE_TOKEN_CACHE_PROFILE": "tenant-b"
      }
    }
  }
}
```

Each profile maps to one tenant/account sign-in; agents pass the target `dataverse_url` on each tool call. Omit `DATAVERSE_TOKEN_CACHE_PROFILE` (or leave it empty) for a single-tenant setup — the original shared cache filenames are used.

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

### Reliability and Limits

These behaviors are built in and need no configuration:

- **Automatic retries** — requests throttled by Dataverse service-protection limits or hitting transient gateway errors are retried automatically; read-only requests are also retried on timeouts and connection failures.
- **Response size cap** — responses larger than 5 MB are replaced with an error asking the agent to narrow the query with `select`, `top`, or `filter`.
- **Consistent errors** — every tool returns JSON; failures have the shape `{"error": true, "message": "..."}` with the Dataverse error code included and the message capped in length.
- **Server-side paging** — list tools request right-sized pages from Dataverse instead of full 5,000-record pages.

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

`dataverse_url` is required on every tool call. Use `dataverse_list_environments` to discover available environments if you do not yet know the URL.

---

## Tools

**118 tools** grouped by domain below. Every tool returns JSON and requires `dataverse_url` on each call.

The **Gate** column shows when a tool is registered:

| Gate | Meaning |
|------|---------|
| `default` | Always registered (reads and safe queries). |
| `write` | Registered only when `DATAVERSE_ALLOW_WRITE=true`. |
| `delete` | Registered only when `DATAVERSE_ALLOW_DELETE=true`. |

> `dataverse_execute_batch` is `default` but rejects non-GET operations unless `DATAVERSE_ALLOW_WRITE=true`.

### Environment & identity

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_environments` | default | List Power Platform environments accessible to the caller |
| `dataverse_whoami` | default | Return the caller's `UserId`, `BusinessUnitId`, `OrganizationId` |
| `dataverse_get_entity_sets` | default | List OData EntitySet names from the service document |
| `dataverse_retrieve_user_privileges` | default | List security privileges assigned to a user |
| `dataverse_retrieve_principal_access` | default | Check a user's access rights to a specific record |

### Records & data

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_query_table` | default | Query records with filter, select, orderby, expand, top |
| `dataverse_get_record` | default | Get one record by entity set name and GUID |
| `dataverse_count_records` | default | Count rows in a table, optional filter |
| `dataverse_aggregate_table` | default | Aggregate (sum, avg, min, max, countdistinct) with optional grouping |
| `dataverse_execute_batch` | default | Run up to 1,000 OData operations in one `$batch` (GET-only unless write enabled) |
| `dataverse_associate_records` | write | Associate two records via a collection-valued navigation property |
| `dataverse_merge_records` | write | Merge a subordinate record into a target (account, contact, lead, incident) |
| `dataverse_disassociate_records` | delete | Remove an association between two records |

### Tables & columns

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_tables` | default | List tables, optional metadata filter |
| `dataverse_get_table_metadata` | default | Get full schema details for a table |
| `dataverse_list_columns` | default | List columns for a table, optional type filter |
| `dataverse_get_column` | default | Get full metadata for one column, including type-specific properties |
| `dataverse_create_table` | write | Create a custom table (ownership type, primary name attribute) |
| `dataverse_update_table` | write | Update a table's display name or description |
| `dataverse_create_column` | write | Add a typed column to a table |
| `dataverse_update_column` | write | Replace a column via full PUT (fetch with `dataverse_get_column` first) |
| `dataverse_publish_customizations` | write | Publish schema changes via `PublishXml` (targeted) or `PublishAllXml` |
| `dataverse_delete_table` | delete | Permanently delete a custom table and all its data |
| `dataverse_delete_column` | delete | Permanently delete a custom column and all its data |

### Relationships

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_relationships` | default | List relationships for a table or the whole environment |
| `dataverse_get_relationship` | default | Get full metadata for one relationship by schema name |
| `dataverse_check_relationship_eligibility` | default | Check whether a table can participate in a relationship |
| `dataverse_create_one_to_many_relationship` | write | Create a 1:N relationship and its lookup column |
| `dataverse_create_many_to_many_relationship` | write | Create an N:N relationship and its intersect table |
| `dataverse_create_multi_table_lookup` | write | Create a polymorphic lookup referencing multiple tables |
| `dataverse_update_relationship` | write | Replace a relationship via full PUT (fetch with `dataverse_get_relationship` first) |
| `dataverse_delete_relationship` | delete | Delete a custom relationship by MetadataId |

### Choices (option sets)

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_choices` | default | List global choices (option sets) |
| `dataverse_get_choice` | default | Get a global choice and its options by name or MetadataId |
| `dataverse_list_choice_column_options` | default | Get options for a Picklist or MultiSelectPicklist column |
| `dataverse_create_choice` | write | Create a global choice with initial options |
| `dataverse_update_choice` | write | Replace a global choice via full PUT (fetch with `dataverse_get_choice` first) |
| `dataverse_add_choice_option` | write | Add an option to a global or local choice |
| `dataverse_update_choice_option` | write | Update the display label of an option |
| `dataverse_reorder_choice_options` | write | Reorder all options in a choice |
| `dataverse_delete_choice` | delete | Delete a global choice by logical name |
| `dataverse_delete_choice_option` | delete | Remove one option from a global or local choice |

### Solutions & publishers

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_solutions` | default | List solutions, optional filter and pagination |
| `dataverse_get_solution` | default | Get a solution by unique name or GUID |
| `dataverse_list_solution_components` | default | List components in a solution, optional type filter |
| `dataverse_create_publisher` | write | Create a publisher with customization prefixes |
| `dataverse_update_publisher` | write | Update publisher fields by GUID |
| `dataverse_create_solution` | write | Create a solution (publisher binding, version) |
| `dataverse_update_solution` | write | Update solution fields by GUID or unique name |
| `dataverse_update_solution_version` | write | Update only a solution's version |
| `dataverse_add_component_to_solution` | write | Add a component via `AddSolutionComponent` |
| `dataverse_remove_component_from_solution` | delete | Remove a component via `RemoveSolutionComponent` |

### Cloud flows

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_get_cloud_flows` | default | Query cloud flows, optionally scoped to a solution |
| `dataverse_enable_cloud_flow` | write | Enable one flow by workflow ID |
| `dataverse_batch_enable_cloud_flows` | write | Enable many flows in one `$batch`, per-item results |
| `dataverse_disable_cloud_flow` | write | Disable one flow by workflow ID |
| `dataverse_batch_disable_cloud_flows` | write | Disable many flows in one `$batch`, per-item results |

### Forms

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_forms` | default | List forms for a table, optional form type filter |
| `dataverse_get_form` | default | Get a form's layout as a tabs → sections → controls tree |
| `dataverse_validate_formxml` | default | Validate FormXml against XSD; pass `formxml` for a dry-run |
| `dataverse_set_formxml` | write | Replace and publish a form's FormXml; returns `formxml_backup` for revert |
| `dataverse_add_form_control` | write | Add a column control to a form (auto-resolves classid) |
| `dataverse_remove_form_control` | write | Remove a column control by logical name |

### Views

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_views` | default | List saved views (savedquery) for a table, optional query type filter |
| `dataverse_get_view` | default | Get a view's FetchXml, LayoutXml, and column list |
| `dataverse_validate_view` | default | Validate a view's FetchXml and LayoutXml |
| `dataverse_create_view` | write | Create a saved view with FetchXml and LayoutXml |
| `dataverse_update_view` | write | Update a view's FetchXml, LayoutXml, name, or description |
| `dataverse_add_view_column` | write | Add a column to a view's LayoutXml |
| `dataverse_remove_view_column` | write | Remove a column from a view's LayoutXml |

### Model-driven apps

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_apps` | default | List apps; `include_unpublished=true` includes drafts |
| `dataverse_get_app` | default | Get an app's properties and components grouped by type |
| `dataverse_validate_app` | default | Validate an app via `ValidateApp` (surfaces missing sitemap, etc.) |
| `dataverse_create_app` | write | Create an app (sitemap, components, validation, publish) |
| `dataverse_update_app` | write | Update an app's name or description |
| `dataverse_add_app_components` | write | Add tables, forms, views, charts, or BPFs to an app |
| `dataverse_remove_app_components` | write | Remove components from an app |
| `dataverse_set_app_sitemap` | write | Create or replace an app's navigation sitemap |
| `dataverse_publish_app` | write | Publish an app to make it visible to users |
| `dataverse_assign_app_role` | write | Associate or disassociate a security role with an app |

### Connection references

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_connection_references` | default | List connection references, optional connector/status/OData filters |
| `dataverse_get_connection_reference` | default | Get one by GUID or logical name |
| `dataverse_create_connection_reference` | write | Create one, optional connection and solution association |
| `dataverse_update_connection_reference` | write | Assign/clear connection, update fields, or associate with a solution |
| `dataverse_delete_connection_reference` | delete | Delete an unmanaged connection reference |

### Plug-in registration

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_plugin_assemblies` | default | List registered plug-in assemblies |
| `dataverse_get_plugin_assembly` | default | Get one plug-in assembly |
| `dataverse_list_plugin_packages` | default | List NuGet plug-in packages |
| `dataverse_get_plugin_package` | default | Get one plug-in package |
| `dataverse_list_plugin_types` | default | List plug-in types (classes) in an assembly |
| `dataverse_get_plugin_type` | default | Get one plug-in type |
| `dataverse_list_plugin_steps` | default | List SDK message processing step registrations |
| `dataverse_get_plugin_step` | default | Get one processing step |
| `dataverse_list_plugin_step_images` | default | List pre/post entity images on a step |
| `dataverse_get_plugin_step_image` | default | Get one step image |
| `dataverse_list_sdk_messages` | default | List SDK messages (Create, Update, …) — reference |
| `dataverse_get_sdk_message` | default | Get one SDK message |
| `dataverse_list_sdk_message_filters` | default | List SDK message filters (message/entity combos) — reference |
| `dataverse_get_sdk_message_filter` | default | Get one SDK message filter |
| `dataverse_create_plugin_assembly` | write | Register a plug-in assembly |
| `dataverse_update_plugin_assembly` | write | Update a plug-in assembly |
| `dataverse_create_plugin_package` | write | Register a plug-in package |
| `dataverse_update_plugin_package` | write | Update a plug-in package |
| `dataverse_create_plugin_type` | write | Register a plug-in type |
| `dataverse_update_plugin_type` | write | Update a plug-in type |
| `dataverse_create_plugin_step` | write | Register an SDK message processing step |
| `dataverse_update_plugin_step` | write | Update a processing step |
| `dataverse_create_plugin_step_image` | write | Register a step image |
| `dataverse_update_plugin_step_image` | write | Update a step image |
| `dataverse_delete_plugin_assembly` | delete | Delete a plug-in assembly |
| `dataverse_delete_plugin_package` | delete | Delete a plug-in package |
| `dataverse_delete_plugin_type` | delete | Delete a plug-in type |
| `dataverse_delete_plugin_step` | delete | Delete a processing step |
| `dataverse_delete_plugin_step_image` | delete | Delete a step image |

### Plug-in tracing & statistics

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_plugin_type_statistics` | default | Runtime performance stats (execution count, failure rate, crashes) per plug-in type |
| `dataverse_get_plugin_trace_log_setting` | default | Get org-wide trace log verbosity (off / exception / all) |
| `dataverse_list_plugin_trace_logs` | default | List trace logs with filters (class, message, entity, operation, errors-only, time window) |
| `dataverse_set_plugin_trace_log_setting` | write | Set org-wide trace log verbosity (`off`, `exception`, `all`) |

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
