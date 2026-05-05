# Dataverse MCP Server

An [MCP](https://modelcontextprotocol.io/) server for interacting with Microsoft Dataverse environments. Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk) and the official [PowerPlatform-Dataverse-Client](https://pypi.org/project/PowerPlatform-Dataverse-Client/) Python SDK.

## Features

- **Solution inspection** — list solutions, get solution details, browse solution components
- **Table querying** — flexible OData-style queries against any Dataverse table
- **Schema exploration** — list tables, inspect table metadata (primary key, name attribute)
- **Environment discovery** — list Power Platform environments available to the authenticated user
- **Multi-environment targeting** — one MCP server config can query any Dataverse org the caller specifies
- **Agent-friendly** — rich tool descriptions designed for AI agent discoverability
- **Secure** — Pydantic v2 input validation, GUID format enforcement, OData injection prevention

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — install from [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)
- Access to a Microsoft Dataverse environment
- Azure CLI (`az login`) or a registered app for authentication

## Installation

You can run this server either from PyPI with `uvx` or directly from a local checkout.

### Option 1: Run from PyPI

```bash
uvx dataverse-mcp
```

`uvx` downloads and runs the package in an isolated environment.

### Option 2: Run from a local checkout

```bash
git clone https://github.com/ryanmichaeljames/dataverse-mcp.git
cd dataverse-mcp
uv sync
```

This creates `.venv`, which is the local Python environment used by the source-based MCP configuration shown below.

## Configuration

Configure the server through your MCP client. In VS Code, that means the `env`
block on the server entry in `.vscode/mcp.json` or your user `mcp.json`.
This project does not use a `.env` file for normal setup.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATAVERSE_URL` | No | — | Optional fallback org URL, set only if you want requests without `dataverse_url` to still work |
| `DATAVERSE_AUTH_TYPE` | No | `azure_cli` | Auth method: `interactive`, `client_secret`, or `azure_cli` |
| `AZURE_TENANT_ID` | For `client_secret` | — | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | For `client_secret` | — | App registration client ID |
| `AZURE_CLIENT_SECRET` | For `client_secret` | — | App registration client secret |

### Authentication Methods

- **`azure_cli`** (default) — Uses your existing `az login` session. Best for local development.
- **`interactive`** — Opens a browser window for interactive sign-in.
- **`client_secret`** — Uses a service principal. Requires `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET`.

## Usage

This server communicates over stdio and works with any MCP-compatible client.

### VS Code

Add the server to your VS Code MCP configuration. Choose either the packaged `uvx` form or the local source form.

Run from PyPI:

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

Run from a local checkout on the same machine:

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

The local source form does not require a build step. Code changes are picked up on the next server start.

If you want a fallback environment for requests that omit `dataverse_url`, add
`DATAVERSE_URL` to that same `env` block. Keep it in MCP config, not a `.env`
file.

### Environment Targeting

Use a single server entry and provide `dataverse_url` on each tool call to target the Dataverse environment explicitly. Example tool input:

```json
{
  "dataverse_url": "https://yourorg.crm.dynamics.com",
  "table_name": "account",
  "select": ["name", "accountid"],
  "top": 10
}
```

If you omit `dataverse_url`, the server falls back to `DATAVERSE_URL` when that value is configured in your MCP server `env`. That fallback is kept for backward compatibility only; the preferred setup is explicit environment targeting on every request.

Use `dataverse_list_environments` first if you need to discover which Power Platform environments are available before choosing a `dataverse_url`.

`dataverse_list_environments` does not require `dataverse_url` and always returns the full normalized environment payload. Optional flags let you include capacity and add-on details.

## Tools

| Tool | Description |
|------|-------------|
| `dataverse_list_environments` | List Power Platform environments available to the authenticated user via the admin API, returning the full normalized payload |
| `dataverse_list_solutions` | List solutions with optional OData filter, select, and top |
| `dataverse_get_solution` | Get a single solution by unique name or GUID |
| `dataverse_list_solution_components` | List components in a solution with optional type filter |
| `dataverse_query_table` | Query records from any table with filter, select, orderby, expand, top |
| `dataverse_get_record` | Get a single record by table name and GUID |
| `dataverse_list_tables` | List available tables/entities with optional filter |
| `dataverse_get_table_metadata` | Get schema details for a specific table |

## Project Structure

```
src/dataverse_mcp/
├── __init__.py          # Package init
├── _app.py              # FastMCP instance (avoids circular imports)
├── server.py            # Entry point, logging setup, tool registration
├── client.py            # DataverseClient wrapper (auth, lifecycle)
├── models.py            # Pydantic v2 input models for all tools
└── tools/
    ├── __init__.py      # Tools package init
    ├── environments.py   # Power Platform environment discovery tool
    ├── solutions.py     # Solution query tools
    ├── tables.py        # Table record query tools
    └── metadata.py      # Table/column metadata tools
```

## Development

```bash
# Install dependencies into .venv
uv sync

# Run the MCP inspector for testing
uv run mcp dev src/dataverse_mcp/server.py

# Run the server directly from source
uv run python -m dataverse_mcp.server

# Compile check touched modules
uv run python -m py_compile src/dataverse_mcp/server.py
```

If you run the server from a local checkout in VS Code, restart the MCP server after code changes so the new Python source is loaded.

## License

MIT
