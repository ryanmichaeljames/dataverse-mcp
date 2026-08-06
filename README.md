![dataverse-mcp](https://raw.githubusercontent.com/ryanmichaeljames/dataverse-mcp/main/assets/dataverse-mcp-banner.png)

[![CI](https://github.com/ryanmichaeljames/dataverse-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ryanmichaeljames/dataverse-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dataverse-mcp)](https://pypi.org/project/dataverse-mcp/)
[![Downloads](https://img.shields.io/pypi/dm/dataverse-mcp)](https://pypi.org/project/dataverse-mcp/)
[![License: MIT](https://img.shields.io/github/license/ryanmichaeljames/dataverse-mcp)](LICENSE)

An [MCP](https://modelcontextprotocol.io/) server that gives AI agents structured access to Microsoft Dataverse — query records, bulk upsert data, inspect metadata, manage schema, analyze component dependencies, manage model-driven app forms, views, and apps, administer security roles, teams, and users, audit user access, manage plug-in trace logging, manage custom APIs, and explore Power Platform environments.

Built with [MCPServer](https://github.com/modelcontextprotocol/python-sdk) (`mcp.server.mcpserver`), `httpx`, and the Dataverse OData v4.0 Web API. Communicates over **stdio** and works with Claude, GitHub Copilot, and any MCP-compatible client.

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
        "DATAVERSE_AUTH_TYPE": "interactive"
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
        "DATAVERSE_AUTH_TYPE": "interactive"
      }
    }
  }
}
```

**3. Sign in**

On first use the server opens a browser for interactive sign-in. The session is cached and reused across restarts (see `DATAVERSE_TOKEN_CACHE_PERSIST`), so you are not prompted again while the token is valid.

That's it. Your AI agent can now query your Dataverse environments.

> Prefer your existing Azure CLI session instead? Set `DATAVERSE_AUTH_TYPE` to `azure_cli` and run `az login`.

---

## Installation

### Install uv

`uvx` is provided by [uv](https://docs.astral.sh/uv/). Install it first if you don't have it:

```bash
pip install uv
```

> **Requires the MCP Python SDK `>=2.0.0`.** Versions up to and including 3.7.0 require SDK 1.x and will not start against 2.x — the SDK removed `mcp.server.fastmcp` in 2.0.0, so an older release installed today fails at import with `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. Because `uvx` ignores lockfiles and resolves the latest SDK, use 3.8.0 or later.

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
| `DATAVERSE_AUTH_TYPE` | `interactive` | Authentication method: `interactive` (recommended) or `azure_cli` |
| `DATAVERSE_ALLOW_WRITE` | `false` | Set to `true` to register create, update, associate, merge, and schema mutation tools |
| `DATAVERSE_ALLOW_DELETE` | `false` | Set to `true` to register delete and disassociate tools |
| `DATAVERSE_TOOLS` | — | Comma-separated list of tool categories to register (e.g., `core,schema,security`). When unset or empty, all categories register. `core` is always registered regardless. Unknown category names are logged as warnings and ignored. See [Tool categories](#tool-categories) below. |
| `DATAVERSE_WHITELIST` | — | Comma-separated list of allowed environment hostnames (e.g., `yourorg.crm.dynamics.com,yourorg-uat.crm.dynamics.com`). When set, tool calls to any environment not on the list are rejected. When empty, **all** environments are permitted — see the warning below. Treat as a **required hardening step** for any non-local or shared deployment |
| `DATAVERSE_REQUIRE_WHITELIST` | `false` | When `true`, fails closed: if `DATAVERSE_WHITELIST` is empty, **every** tool call is rejected so a bearer token is never minted for an unapproved host. Recommended for shared/multi-tenant deployments. Invalid values fall back to `false` with a logged warning |
| `DATAVERSE_FILE_BASE_DIR` | — | Optional directory that confines the solution export/import file paths (`output_path` / `input_path` on `dataverse_export_solution`, `dataverse_import_solution`, `dataverse_stage_and_upgrade_solution`). When set, any path that resolves outside this directory — including `..` traversal or an absolute path elsewhere — is rejected. Bounds the blast radius of an arbitrary-location file write/read (e.g. a prompt-injection payload steering the agent to overwrite a startup script or read an SSH key). When unset, paths are unrestricted (prior behaviour). Recommended for non-local or shared deployments |
| `DATAVERSE_AUTH_TIMEOUT_SECONDS` | `30` | Maximum seconds to wait for a credential acquisition (e.g., `az login` token fetch) before failing with an actionable auth error. Increase when operating in slow-network or MFA-heavy environments. Invalid or non-positive values fall back to `30` |
| `DATAVERSE_TOKEN_CACHE_PERSIST` | `true` | Controls whether `interactive` auth persists its MSAL token cache to disk so the server survives restarts without a new browser prompt (while a refresh token is valid). Set to `false` to disable and revert to in-memory-only behaviour. Invalid values fall back to `true` with a logged warning. Has no effect on `azure_cli` auth. |
| `DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED` | `false` | When `true`, permits writing the MSAL token cache to disk without OS-level encryption. Only needed on headless Linux hosts that lack a Secret Service (e.g., GNOME Keyring / libsecret). **Refresh tokens are long-lived credentials — only enable this on trusted, access-controlled hosts.** A startup warning is logged when this flag is active. Invalid values fall back to `false`. |
| `DATAVERSE_TOKEN_CACHE_PROFILE` | — | Optional name that isolates the `interactive` token cache and its `AuthenticationRecord` sidecar per profile. Set a distinct value in each session to run concurrent servers signed in to **different tenants/accounts** on the same host without them overwriting each other's cache. Must use only `[A-Za-z0-9_-]`; any other character fails fast at startup (silently sanitizing could collide two profiles and defeat isolation). Empty/unset uses the shared default filenames. Has no effect on `azure_cli` auth. |

> [!WARNING]
> **Leaving `DATAVERSE_WHITELIST` unset is risky.** Tools accept a `dataverse_url` per call, and the server mints a bearer token for whatever environment is supplied. Without a whitelist, a compromised or misbehaving agent can direct your credentials at *any* Dataverse environment. Set `DATAVERSE_WHITELIST` to the specific environment hostnames you intend to use so the server rejects everything else. On shared or multi-tenant hosts, also set `DATAVERSE_REQUIRE_WHITELIST=true` so the server fails closed rather than minting tokens when the whitelist is accidentally left empty.

### Authentication

| Method | Description |
|--------|-------------|
| `interactive` (default, recommended) | Opens a browser for interactive sign-in. Supports MFA and per-account isolation, and needs no separate CLI login. The session persists across server restarts (see `DATAVERSE_TOKEN_CACHE_PERSIST`): the first launch opens a browser; subsequent restarts reuse the cached refresh token silently while it remains valid. |
| `azure_cli` | Uses your active `az login` session. Useful in CI or where a browser is unavailable and an Azure CLI session already exists. Requires the Azure CLI installed and signed in. |

> [!NOTE]
> **Interactive auth persistence.** When `DATAVERSE_TOKEN_CACHE_PERSIST=true` (the default), the MSAL token cache is stored on disk using your OS secret store (Windows DPAPI, macOS Keychain, Linux libsecret). On headless Linux without libsecret, the first token acquisition will fail fast with an error. Set `DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED=true` to permit a plaintext cache on those hosts, and see the security warning for that variable above.

> [!NOTE]
> **Running multiple tenants/accounts at once.** The default cache and sidecar filenames are shared per host, so two `interactive` sessions signed in to different tenants/accounts would overwrite each other's pinned account. Give each session a distinct `DATAVERSE_TOKEN_CACHE_PROFILE` (e.g., `prod`, `dev`) to keep their caches and `AuthenticationRecord` sidecars separate.

#### Example: two tenants side by side

Register two server entries, each with its own `DATAVERSE_TOKEN_CACHE_PROFILE`. The profile is a *tenant-wide* cache key — each entry signs in once (its own browser prompt) and then restarts silently as its own account, while tools still receive the specific `dataverse_url` per call. The profiles never collide.

**Claude** (`claude_desktop_config.json` or `.claude/settings.json`):

```json
{
  "mcpServers": {
    "dataverse-prod": {
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive",
        "DATAVERSE_TOKEN_CACHE_PROFILE": "prod"
      }
    },
    "dataverse-dev": {
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive",
        "DATAVERSE_TOKEN_CACHE_PROFILE": "dev"
      }
    }
  }
}
```

**GitHub Copilot** (`.vscode/mcp.json`):

```json
{
  "servers": {
    "dataverse-prod": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive",
        "DATAVERSE_TOKEN_CACHE_PROFILE": "prod"
      }
    },
    "dataverse-dev": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive",
        "DATAVERSE_TOKEN_CACHE_PROFILE": "dev"
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
        "DATAVERSE_AUTH_TYPE": "interactive",
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
        "DATAVERSE_AUTH_TYPE": "interactive"
      }
    }
  }
}
```

#### Claude Code

Add via the CLI:

```bash
claude mcp add dataverse-mcp --env DATAVERSE_AUTH_TYPE=interactive uvx dataverse-mcp
```

Or add directly to `.claude/settings.json` (project) or `~/.claude/settings.json` (user):

```json
{
  "mcpServers": {
    "dataverse-mcp": {
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_AUTH_TYPE": "interactive"
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
        "DATAVERSE_AUTH_TYPE": "interactive"
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
        "DATAVERSE_AUTH_TYPE": "interactive"
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
        "DATAVERSE_AUTH_TYPE": "interactive"
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

**200 tools** grouped by domain below. Every tool returns JSON and requires `dataverse_url` on each call.

The **Gate** column shows when a tool is registered:

| Gate | Meaning |
|------|---------|
| `default` | Always registered (reads and safe queries). |
| `write` | Registered only when `DATAVERSE_ALLOW_WRITE=true`. |
| `delete` | Registered only when `DATAVERSE_ALLOW_DELETE=true`. |

> `dataverse_execute_batch` is `default` but rejects non-GET operations unless `DATAVERSE_ALLOW_WRITE=true`.

### Tool categories

Use `DATAVERSE_TOOLS` to register only the tool categories your agent needs. This shrinks the visible tool list and reduces token overhead.

| Category | Tools | Description |
|----------|-------|-------------|
| `core` | 24 | Environment introspection, effective org settings, all record CRUD, and unpublished-customization reads (always registered) |
| `schema` | 35 | Table/column/relationship/choice/alternate-key metadata, component customizability pre-flight, environment language codes |
| `solutions` | 21 | Solution and publisher management, solution components, history, import/export ALM, import diagnostics, dependency analysis |
| `flows` | 8 | Cloud flow + classic process listing and activate/deactivate |
| `forms` | 6 | Model-driven form management |
| `views` | 7 | Saved query / view management |
| `apps` | 10 | Canvas and model-driven app management |
| `connections` | 5 | Connection reference management |
| `variables` | 8 | Environment variable definitions and values |
| `plugins` | 33 | Plugin assemblies, types, steps, step images, packages, trace logs |
| `security` | 22 | Security roles and their privileges, teams and their privileges, the environment-wide privilege catalogue, users, business units, record access origin, record shares, composite access audit, record- and column-level audit history |
| `jobs` | 3 | Async operation (system job) monitoring and cancellation |
| `webresources` | 5 | Web resource (JS/HTML/CSS/image) CRUD — gated, not always-on |
| `customapis` | 13 | Custom API, request parameter, and response property management |

`core` is **always** registered even when not listed. When `DATAVERSE_TOOLS` is unset or empty, all categories register (current default behaviour). Category gating composes with `DATAVERSE_ALLOW_WRITE` and `DATAVERSE_ALLOW_DELETE`: a tool registers only when its category is enabled AND its write/delete flag (if any) is set.

### Environment & identity

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_environments` | default | List Power Platform environments accessible to the caller |
| `dataverse_whoami` | default | Return the caller's `UserId`, `BusinessUnitId`, `OrganizationId` |
| `dataverse_get_organization_info` | default | Fingerprint the environment — server version, organization identity, instance type, service endpoints, installed-solution count |
| `dataverse_get_setting` | default | Read one setting's **final computed value** via `RetrieveSetting` — the value in effect after the platform's precedence rules, which is what a configuration diff between environments needs. The value is lifted out of the `SettingDetail` container Dataverse returns (`Value` is a **string**; `DataType` is an integer code, passed through unmapped). Optional `app_unique_name` reads the model-driven app's view of it; omitted, the parameter is left out of the call entirely. An **unknown setting name is not an error** — Dataverse answers HTTP 200 with `SettingDetail: null`, reported as `setting_found: false`, which never collapses with a setting that genuinely holds `""`, `"false"` or `0` |
| `dataverse_get_entity_sets` | default | List OData EntitySet names from the service document |
| `dataverse_retrieve_user_privileges` | default | List security privileges assigned to a user |
| `dataverse_retrieve_principal_access` | default | Check a user's access rights to a specific record |

### Security administration

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_security_roles` | default | List security roles, optional filter and pagination |
| `dataverse_get_security_role` | default | Get one security role by GUID |
| `dataverse_get_role_privileges` | default | List the privileges assigned to a security role via `RetrieveRolePrivilegesRole` — the "what can this role actually **do**?" companion to `dataverse_get_security_role`, which returns only the role record. A System Administrator role carries thousands of privileges (~4,100, ~1 MB), so entries are trimmed to `top` (default 50, max 1000) while `total_count` and `depth_summary` report the true magnitude |
| `dataverse_retrieve_access_origin` | default | Answer **why** a principal has access to one record — role, ownership, share, business-unit hierarchy, team membership — via `RetrieveAccessOrigin`, where `dataverse_retrieve_principal_access` returns only the access *mask*. Takes the **singular lowercase** table `logical_name`, not the entity set name. **HTTP 200 does not mean "has access"** — no access and a nonexistent record are also successful calls, distinguishable only by the prose in `access_origin` |
| `dataverse_list_teams` | default | List teams, optional filter and pagination |
| `dataverse_get_team` | default | Get one team by GUID |
| `dataverse_get_team_privileges` | default | Answer "what can this **team** actually do?" via the entity-bound `RetrieveTeamPrivileges` — the missing third of the trio alongside `dataverse_get_role_privileges` (role) and `dataverse_retrieve_user_privileges` (user). Trimmed to `top` (default 50, max 1000) with `total_count`, `has_more` and `depth_summary` over the full set. The collection arrives under **`RolePrivileges`**, not `TeamPrivileges` — check `privileges_source`, and expect `normalized: false` with the raw payload if the shape is unrecognized. **An empty list is normal**, meaning no directly-assigned security roles; a nonexistent team id is an HTTP 404 instead |
| `dataverse_list_privileges` | default | List the privileges **defined** in the environment from the `privileges` catalogue — the definitions behind the `prvReadAccount` names the role/team/user privilege tools return, answering "what exists" rather than "who holds what". The integer `accessright` column is decoded into a readable name (`ReadAccess`, `AppendToAccess`, …) by a hand-rolled map, because **Dataverse exposes no option set for it** — the `PicklistAttributeMetadata` cast and `GlobalOptionSetDefinitions` both 404 — and an unrecognized value is reported **raw with no name** rather than mislabelled. Optional `table_logical_name` scopes to one table through the `privilegeobjecttypecodesset` join table, not by matching privilege names (`endswith(name,'Role')` spans four unrelated tables); an unknown name is an HTTP 400 naming it, while an **empty list means a real table with no privileges**. `total_count` comes from `$apply=aggregate($count as c)` because **`@odata.count` caps at 5,000** and under-reports this ~7,300-row collection |
| `dataverse_list_shared_principals` | default | List **everyone one record was shared with**, merging `RetrieveSharedPrincipalsAndAccess` (principals + their access) and `RetrieveSharedLinks`. Neither `dataverse_retrieve_principal_access` (the mask) nor `dataverse_retrieve_access_origin` (the why) can enumerate them. Takes the **plural `entity_set_name`** (`accounts`), unlike `dataverse_retrieve_access_origin`'s singular logical name — and a wrong entity set returns the **same HTTP 404 `Does Not Exist`** as a missing record, so check the plural first. One function failing lands in `partial_errors` while the other still returns; an empty result is **not** proof the record is private |
| `dataverse_list_users` | default | List system users, optional filter and pagination |
| `dataverse_get_user` | default | Get one system user by GUID |
| `dataverse_list_business_units` | default | List business units, optional filter and pagination |
| `dataverse_audit_user_access` | default | Composite report: user identity, direct roles, team memberships + team roles, effective privileges, optional record-level access check |
| `dataverse_list_audit` | default | Query the `audits` entity set with optional OData filter, select, orderby, and top; returns audit metadata rows |
| `dataverse_get_audit_details` | default | Get full before/after detail for a single audit record via the bound `RetrieveAuditDetails` function |
| `dataverse_retrieve_record_change_history` | default | Retrieve the full audit change history for a single record via `RetrieveRecordChangeHistory`; returns structured `AuditDetailCollection` |
| `dataverse_get_attribute_change_history` | default | Audit trail for **one column of one record** via `RetrieveAttributeChangeHistory` — the column-scoped sibling of `dataverse_retrieve_record_change_history`, answering "when did this field last change, and to what?" from the server instead of filtering a whole record's history client-side. Takes the table twice: the plural `entity_set_name` (the only one sent to the function) and the singular `table_logical_name` (used only by the probes below). Org-level audit-**configuration** rows accompany *every* response whatever the target, so they are identified by type and partitioned into `audit_configuration_events` and excluded from `audit_details`, `count` and `has_more`. **Zero changes is ambiguous, so it is diagnosed** — an `audit_configuration` block names the outermost level at which auditing is off (organization / table / column), or confirms auditing is on and nothing was recorded |
| `dataverse_assign_security_role` | write | Assign a security role to a user or team |
| `dataverse_add_team_members` | write | Add one or more users to a team |
| `dataverse_set_user_state` | write | Enable or disable a system user (`isdisabled`) |
| `dataverse_remove_security_role` | delete | Remove a security role from a user or team |
| `dataverse_remove_team_members` | delete | Remove one or more users from a team |

### Async jobs

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_async_operations` | default | List system jobs (asyncoperations), optional filter by statecode/statuscode/operationtype |
| `dataverse_get_async_operation` | default | Get one system job by GUID |
| `dataverse_cancel_async_operation` | write | Cancel a running or waiting system job (PATCH statecode=3/statuscode=32) |

### Web resources

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_web_resources` | default | List web resources; optional filter by type and/or name substring |
| `dataverse_get_web_resource` | default | Get one web resource by GUID; `include_content=true` adds the base64 content field |
| `dataverse_create_web_resource` | write | Create a web resource (name, type, base64 content); call `dataverse_publish_customizations` afterward |
| `dataverse_update_web_resource` | write | PATCH content, display name, or description; call `dataverse_publish_customizations` afterward |
| `dataverse_delete_web_resource` | delete | Permanently delete an unmanaged web resource by GUID |

### Records & data

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_query_table` | default | Query records with filter, select, orderby, expand, top |
| `dataverse_execute_fetchxml` | default | Execute a FetchXML query (joins, aggregation, paging cookie) |
| `dataverse_validate_fetchxml` | default | Pre-flight a FetchXML query via `ValidateFetchXmlExpression`: Dataverse parses and analyses the expression and reports errors plus performance suggestions **without executing it**. **HTTP 200 does not mean the query is valid** — an unknown table or attribute returns 200 with an error-severity message — so the findings are lifted to `has_errors`, `error_count`, `warning_count` and `errors`. Max 2000 characters (the query travels in the request URL) |
| `dataverse_get_record` | default | Get one record by entity set name and GUID |
| `dataverse_retrieve_unpublished` | default | Read the **unpublished (draft)** definition of one customization record via `RetrieveUnpublished` — a normal GET returns the *published* row, so a read-back after a form or view edit is stale until `dataverse_publish_customizations` runs. Limited to `savedqueries`, `systemforms`, `appmodules` and `webresourceset` (`sitemap` is refused by the platform). Returns one record, large XML/binary columns excluded unless requested via `select`; NULL columns are omitted |
| `dataverse_count_records` | default | Count rows in a table, optional filter |
| `dataverse_get_total_record_counts` | default | Approximate row counts for up to 50 tables in one call (`RetrieveTotalRecordCount`). Counts come from a snapshot up to 24 hours old and can be stale — or uniformly `0` where the snapshot job has not run — so use `dataverse_count_records` for an exact, live count. All-or-nothing: one unrecognized logical name fails the whole batch with HTTP 400 |
| `dataverse_aggregate_table` | default | Aggregate (sum, avg, min, max, countdistinct) with optional grouping |
| `dataverse_execute_batch` | default | Run up to 1,000 OData operations in one `$batch` (GET-only unless write enabled) |
| `dataverse_bulk_upsert` | write | Upsert many records via `$batch` PATCH; auto-detects primary GUID key or uses `key_columns` for alternate-key upserts; per-row outcomes |
| `dataverse_create_record` | write | Create a record and return its new GUID |
| `dataverse_update_record` | write | Partially update a record (PATCH) |
| `dataverse_swap_flow_connection_reference` | write | Swap a connection reference logical name inside a cloud flow's `clientdata` server-side (GET, literal string replace, PATCH) — avoids sending the multi-KB `clientdata` blob as a tool argument |
| `dataverse_associate_records` | write | Associate two records via a collection-valued navigation property |
| `dataverse_merge_records` | write | Merge a subordinate record into a target (account, contact, lead, incident) |
| `dataverse_delete_record` | delete | Permanently delete a record |
| `dataverse_disassociate_records` | delete | Remove an association between two records |

### Tables & columns

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_tables` | default | List tables, optional metadata filter |
| `dataverse_get_table_metadata` | default | Get full schema details for a table |
| `dataverse_list_columns` | default | List columns for a table, optional type filter |
| `dataverse_get_column` | default | Get full metadata for one column, including type-specific properties |
| `dataverse_list_languages` | default | Report the environment's language codes (LCIDs), reconciling `RetrieveProvisionedLanguages`, `RetrieveAvailableLanguages` and `RetrieveInstalledLanguagePacks` in one concurrent call. **`provisioned` is the load-bearing set** — the only LCIDs a `LocalizedLabels` entry may use, so check it before writing a localized label rather than assuming `1033`. The three sets can be **mutually disjoint** (measured live: `available` and `provisioned` both `[1033]`, `installed_packs` 44 other LCIDs), so never infer one from another; `available_not_provisioned` and `installed_not_provisioned` are reported only when both their inputs were read, and the three calls fail independently via `partial_errors` |
| `dataverse_is_component_customizable` | default | Pre-flight check via `IsComponentCustomizable`: can this solution component be edited, before an update is attempted? Takes the component's own GUID plus the same integer component-type codes as `dataverse_analyze_dependencies`. **Do not assume system components answer `false`** — core tables such as `systemuser` report `true` because customizations like adding columns are permitted |
| `dataverse_create_table` | write | Create a custom table (ownership type, primary name attribute) |
| `dataverse_update_table` | write | Update a table's display name or description |
| `dataverse_create_column` | write | Add a typed column to a table (supports Memo, Boolean with custom labels, and Picklist/MultiSelectPicklist bound to a global choice) |
| `dataverse_update_column` | write | Replace a column via full PUT (fetch with `dataverse_get_column` first) |
| `dataverse_publish_customizations` | write | Publish schema changes via `PublishXml` (targeted by entity/option set/relationship/web resource IDs) or `PublishAllXml` |
| `dataverse_delete_table` | delete | Permanently delete a custom table and all its data |
| `dataverse_delete_column` | delete | Permanently delete a custom column and all its data |

### Relationships

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_relationships` | default | List relationships for a table or the whole environment |
| `dataverse_get_relationship` | default | Get full metadata for one relationship by schema name |
| `dataverse_check_relationship_eligibility` | default | Answer "**is this table** eligible?" — a per-table boolean for one table you can already name; use `dataverse_get_valid_relationship_entities` to discover candidates |
| `dataverse_get_valid_relationship_entities` | default | Answer "**which tables** are eligible?" — enumerate the tables that may take a relationship role via `GetValidReferencedEntities` / `GetValidReferencingEntities` / `GetValidManyToMany` (`referenced` = valid lookup targets, `referencing` = tables that can hold a lookup, `many_to_many` = tables that can take an N:N), the enumeration counterpart to `dataverse_check_relationship_eligibility`'s per-table boolean. Every role answers the **environment-wide** question: the optional `table_logical_name` (1:N roles only, rejected for `many_to_many`) is validated server-side but **does not narrow the result**, which the response states via `table_logical_name_filtered: false`. The lists are large and none of the functions pages server-side, so names are trimmed to `top` (default 250) while `count`, `total_count` and `has_more` describe the full set |
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

### Alternate keys

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_alternate_keys` | default | List `EntityKeyMetadata` definitions on a table; returns `SchemaName`, `LogicalName`, `KeyAttributes`, `EntityKeyIndexStatus`, and `IsManaged` |
| `dataverse_create_alternate_key` | write | Create an alternate key by `SchemaName`, `DisplayName`, and attribute list; poll `EntityKeyIndexStatus` until `"Active"` before using for upserts |
| `dataverse_delete_alternate_key` | delete | Remove an alternate key by logical name; drops the underlying SQL index |

### Solutions & publishers

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_solutions` | default | List solutions, optional filter and pagination |
| `dataverse_get_solution` | default | Get a solution by unique name or GUID |
| `dataverse_list_solution_components` | default | List components in a solution, optional type filter |
| `dataverse_get_solution_history` | default | Get one solution history record (import/upgrade/export operation) by GUID |
| `dataverse_list_solution_histories` | default | List solution history records, optional filter by solution GUID or unique name; `msdyn_suboperation` distinguishes Update (`3`) from upgrade-with-deletion (`5`) |
| `dataverse_create_publisher` | write | Create a publisher with customization prefixes |
| `dataverse_update_publisher` | write | Update publisher fields by GUID |
| `dataverse_create_solution` | write | Create a solution (publisher binding, version) |
| `dataverse_update_solution` | write | Update solution fields by GUID or unique name |
| `dataverse_update_solution_version` | write | Update only a solution's version |
| `dataverse_add_component_to_solution` | write | Add a component via `AddSolutionComponent` |
| `dataverse_remove_component_from_solution` | delete | Remove a component via `RemoveSolutionComponent` |
| `dataverse_export_solution` | default | Export a solution as a base64 zip; write to disk via `output_path` for large solutions (no org mutation — no write flag required) |
| `dataverse_import_solution` | write | Import a solution asynchronously via `ImportSolutionAsync`; supply zip as inline base64 (`customization_file`) or a local path (`input_path`); returns `import_job_id` to poll. `hold_for_upgrade=false` does an **UPDATE** (overlay — does NOT delete components removed in the new version). For a true upgrade use `dataverse_stage_and_upgrade_solution`, or `hold_for_upgrade=true` then `dataverse_delete_and_promote_solution` |
| `dataverse_stage_and_upgrade_solution` | write | Single-step solution **upgrade** via `StageAndUpgradeAsync` — stages as holding, deletes obsolete components, and promotes in one async op; supply zip via `customization_file` or `input_path`; returns `import_job_id`, `async_operation_id`, `import_job_key` |
| `dataverse_delete_and_promote_solution` | write | Two-step apply-upgrade via `DeleteAndPromote` — promotes the holding `_Upgrade` solution and deletes obsolete components (pair with `dataverse_import_solution` + `hold_for_upgrade=true`); synchronous, returns `solution_id` |
| `dataverse_get_import_job` | default | Get one importjob by GUID — returns progress, completedon, solutionname; add `include_data=true` for the result XML (incl. deletion-phase component errors such as `8004F037`) on failure |
| `dataverse_get_import_job_results` | default | **Why did the import fail?** — the platform's own human-readable results document for one importjob via `RetrieveFormattedImportJobResults`, instead of the opaque `data` XML blob. The document is a **SpreadsheetML (Excel XML) workbook**, so the meaning is in the **cell values**, not the tag names. Large (~14k–71k characters); trimmed to `max_chars` (default 20,000) with `results_length` always reporting the true size |
| `dataverse_list_import_jobs` | default | List importjobs, optional filter by solution unique name, ordered by createdon desc |
| `dataverse_clone_solution_as_patch` | write | Clone a solution as a patch via bound `CloneAsPatch` action; resolves parent by GUID or unique name |
| `dataverse_analyze_dependencies` | default | Analyze component dependencies: `blocking_delete` (blocks deletion), `dependents` (what depends on it), or `required` (what it needs); resolves component type codes to names |

> **Filesystem I/O note.** `dataverse_export_solution` can write the decoded .zip to a local path when
> `output_path` is supplied. `dataverse_import_solution` can read a local .zip when `input_path` is
> supplied. Both paths are resolved on the machine running the MCP server. Use `output_path` / `input_path`
> for solutions larger than ~3 MB (the inline base64 threshold).

### Cloud flows & processes

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_get_cloud_flows` | default | Query cloud flows, optionally scoped to a solution |
| `dataverse_enable_cloud_flow` | write | Enable one flow by workflow ID |
| `dataverse_batch_enable_cloud_flows` | write | Enable many flows in one `$batch`, per-item results |
| `dataverse_disable_cloud_flow` | write | Disable one flow by workflow ID |
| `dataverse_batch_disable_cloud_flows` | write | Disable many flows in one `$batch`, per-item results |
| `dataverse_list_processes` | default | List classic processes (workflows, business rules, actions, BPFs) from the `workflow` entity; filterable by category and type |
| `dataverse_activate_process` | write | Activate a classic process (sets statecode=1/Activated); idempotent |
| `dataverse_deactivate_process` | write | Deactivate a classic process (sets statecode=0/Draft); idempotent |

### Forms

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_forms` | default | List forms for a table, optional form type filter |
| `dataverse_get_form` | default | Get a form's layout as a tabs → sections → controls tree |
| `dataverse_validate_formxml` | default | Validate FormXml against XSD; pass `formxml` for a dry-run |
| `dataverse_set_formxml` | write | Replace and publish a form's FormXml; returns `formxml_backup` for revert |
| `dataverse_add_form_control` | write | Add a column control to a form (auto-resolves classid) |
| `dataverse_remove_form_control` | write | Remove a column control by logical name |

> `dataverse_get_form` returns the **published** form. After any write above, read the draft back with `dataverse_retrieve_unpublished` (`entity_set_name='systemforms'`) or publish first with `dataverse_publish_customizations`.

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

> `dataverse_get_view` returns the **published** view. After any write above, read the draft back with `dataverse_retrieve_unpublished` (`entity_set_name='savedqueries'`) or publish first with `dataverse_publish_customizations`.

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

### Environment variables — definitions

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_get_environment_variables` | default | List definitions with current values; optional solution filter or single-record name lookup (schema name / display name) |
| `dataverse_create_environment_variable` | write | Create a definition and optional initial value |
| `dataverse_update_environment_variable` | write | Update definition fields and/or upsert the current value |
| `dataverse_delete_environment_variable` | delete | Delete definition, value record, or both |

### Environment variables — values

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_get_environment_variable_values` | default | Get value record(s) by value GUID, definition GUID, or definition name |
| `dataverse_create_environment_variable_value` | write | Create a value record bound to a definition (by GUID or name) |
| `dataverse_update_environment_variable_value` | write | PATCH an existing value record by value GUID, definition GUID, or definition name |
| `dataverse_delete_environment_variable_value` | delete | Delete a value record only (resets to default value) by value GUID, definition GUID, or definition name |

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

### Custom APIs

| Tool | Gate | Description |
|------|------|-------------|
| `dataverse_list_custom_apis` | default | List custom APIs, optional filter and pagination |
| `dataverse_get_custom_api` | default | Get one custom API by GUID, including its request parameters and response properties |
| `dataverse_create_custom_api` | write | Create a custom API (unbound, entity-bound, or entity collection-bound) |
| `dataverse_update_custom_api` | write | Update mutable fields of a custom API (display name, description, visibility, allowed step types) |
| `dataverse_delete_custom_api` | delete | Permanently delete a custom API and its child parameters and properties |
| `dataverse_list_custom_api_request_parameters` | default | List request parameters for a custom API |
| `dataverse_create_custom_api_request_parameter` | write | Add a typed request parameter to a custom API |
| `dataverse_update_custom_api_request_parameter` | write | Update mutable fields of a request parameter |
| `dataverse_delete_custom_api_request_parameter` | delete | Delete a request parameter |
| `dataverse_list_custom_api_response_properties` | default | List response properties for a custom API |
| `dataverse_create_custom_api_response_property` | write | Add a typed response property to a custom API |
| `dataverse_update_custom_api_response_property` | write | Update mutable fields of a response property |
| `dataverse_delete_custom_api_response_property` | delete | Delete a response property |

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

---

## Disclaimer

Independent community project. Not affiliated with, endorsed by, or supported by Microsoft. For the first-party runtime server, see Microsoft's [Dataverse MCP Server](https://learn.microsoft.com/en-us/power-platform/release-plan/2025wave1/data-platform/dataverse-mcp-server).

"Dataverse" is a trademark of the President and Fellows of Harvard College. "Microsoft Dataverse" and "Power Platform" are trademarks of the Microsoft group of companies. Used here only to describe the systems this tool works with.
