# Dataverse MCP Server

An [MCP](https://modelcontextprotocol.io/) server for interacting with Microsoft Dataverse environments. Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk) and the official [PowerPlatform-Dataverse-Client](https://pypi.org/project/PowerPlatform-Dataverse-Client/) Python SDK.

## Features

- **Solution inspection** — list solutions, get solution details, browse solution components
- **Table querying** — flexible OData-style queries against any Dataverse table
- **Schema exploration** — list tables, inspect table metadata (primary key, name attribute)
- **Agent-friendly** — rich tool descriptions designed for AI agent discoverability
- **Secure** — Pydantic v2 input validation, GUID format enforcement, OData injection prevention

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — install from [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)
- Access to a Microsoft Dataverse environment
- Azure CLI (`az login`) or a registered app for authentication

## Installation

No install required — run directly from PyPI using `uvx`:

```bash
uvx dataverse-mcp
```

`uvx` downloads and runs the package in an isolated environment. No cloning, no virtual env setup.

## Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATAVERSE_URL` | Yes | — | Your Dataverse org URL (e.g., `https://yourorg.crm.dynamics.com`) |
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

Add the server to your VS Code MCP configuration (`.vscode/mcp.json`):

```json
{
  "servers": {
    "dataverse-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_URL": "https://yourorg.crm.dynamics.com",
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    }
  }
}
```

To connect to multiple environments, add one entry per environment with a unique key:

```json
{
  "servers": {
    "dataverse-mcp-dev": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_URL": "https://yourorg-dev.crm.dynamics.com",
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    },
    "dataverse-mcp-test": {
      "type": "stdio",
      "command": "uvx",
      "args": ["dataverse-mcp"],
      "env": {
        "DATAVERSE_URL": "https://yourorg-test.crm.dynamics.com",
        "DATAVERSE_AUTH_TYPE": "azure_cli"
      }
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
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
    ├── solutions.py     # Solution query tools
    ├── tables.py        # Table record query tools
    └── metadata.py      # Table/column metadata tools
```

## Development

```bash
# Clone the repo
git clone https://github.com/ryanmichaeljames/dataverse-mcp.git
cd dataverse-mcp

# Install dependencies
uv sync

# Run the MCP inspector for testing
uv run mcp dev src/dataverse_mcp/server.py

# Compile check all modules
uv run python -m py_compile src/dataverse_mcp/server.py
```

## License

MIT
