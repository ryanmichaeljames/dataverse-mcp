![dataverse-mcp](https://raw.githubusercontent.com/ryanmichaeljames/dataverse-mcp/main/assets/dataverse-mcp-banner.svg)

[![CI](https://github.com/ryanmichaeljames/dataverse-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ryanmichaeljames/dataverse-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dataverse-mcp)](https://pypi.org/project/dataverse-mcp/)
[![Downloads](https://img.shields.io/pypi/dm/dataverse-mcp)](https://pypi.org/project/dataverse-mcp/)
[![License: MIT](https://img.shields.io/github/license/ryanmichaeljames/dataverse-mcp)](LICENSE)

An [MCP](https://modelcontextprotocol.io/) server that gives AI agents structured access to Microsoft Dataverse — query records, bulk upsert data, inspect metadata, manage schema, analyze component dependencies, manage model-driven app forms, views, and apps, administer security roles, teams, and users, audit user access, manage plug-in trace logging, manage custom APIs, and explore Power Platform environments.

Built with [MCPServer](https://github.com/modelcontextprotocol/python-sdk) (`mcp.server.mcpserver`), `httpx`, and the Dataverse OData v4.0 Web API. Communicates over **stdio** and works with Claude, GitHub Copilot, and any MCP-compatible client.

📖 **[Full documentation is on the wiki](https://github.com/ryanmichaeljames/dataverse-mcp/wiki)**

---

## Quick Start

**1. Install uv** — `uvx` is provided by [uv](https://docs.astral.sh/uv/):

```bash
pip install uv
```

**2. Configure** — add to your MCP client config.

**Claude Code** — add it with the CLI:

```bash
claude mcp add dataverse-mcp --scope user --env DATAVERSE_AUTH_TYPE=interactive -- uvx dataverse-mcp
```

Use `--scope project` instead to write a `.mcp.json` at the repo root and share the server with your team.

**Claude Desktop** (`claude_desktop_config.json`) — or, for Claude Code, a hand-written `.mcp.json`:

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

**GitHub Copilot / VS Code** (`.vscode/mcp.json`):

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

**3. Sign in** — on first use the server opens a browser for interactive sign-in. The session is cached and reused across restarts, so you are not prompted again while the token is valid. Prefer your existing Azure CLI session? Set `DATAVERSE_AUTH_TYPE` to `azure_cli` and run `az login`.

That's it — your AI agent can now query your Dataverse environments. Tools are read-only until you opt in to writes; pass `dataverse_url` (e.g. `https://yourorg.crm.dynamics.com`) on every tool call, or call `dataverse_list_environments` to discover it.

More detail: [Installation](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Installation) · [Client Setup](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Client-Setup) · [Authentication](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Authentication) · [First Steps](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/First-Steps)

---

## What it can do

**200 tools** across 14 categories. Every tool returns JSON. Use `DATAVERSE_TOOLS` to register only the categories your agent needs.

| Category | Tools | Covers |
|----------|-------|--------|
| [`core`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Core) | 24 | Record CRUD, OData queries, FetchXML, aggregation, `$batch`, bulk upsert, environment introspection (always registered) |
| [`schema`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Schema) | 35 | Table, column, relationship, choice (option set) and alternate-key metadata, plus publishing |
| [`solutions`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Solutions) | 21 | Solution and publisher ALM — import, export, patch, stage-and-upgrade, import diagnostics, dependency analysis |
| [`plugins`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Plugins) | 33 | Plug-in assemblies, packages, types, SDK message processing steps, step images, trace logs and statistics |
| [`security`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Security) | 22 | Security roles, teams, users, business units, privileges, record sharing, access audit and change history |
| [`customapis`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Custom-APIs) | 13 | Custom API definitions with their request parameters and response properties |
| [`apps`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Apps) | 10 | Canvas and model-driven app management, sitemaps, app components, publishing |
| [`variables`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Variables) | 8 | Environment variable definitions and their current values |
| [`flows`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Flows) | 8 | Cloud flows and classic processes (workflows, business rules, BPFs) — list, activate, deactivate |
| [`views`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Views) | 7 | Saved queries and views — FetchXml, LayoutXml, columns |
| [`forms`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Forms) | 6 | Model-driven form layout, FormXml validation, controls |
| [`connections`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Connections) | 5 | Connection references for cloud flows |
| [`webresources`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Web-Resources) | 5 | Web resource (JS / HTML / CSS / image) CRUD |
| [`jobs`](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tools-Jobs) | 3 | Async operation (system job) monitoring and cancellation |

Every tool, with arguments and gates: **[Tool Index](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tool-Index)** · category detail: **[Tool Categories](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tool-Categories)**

---

## Configuration

Set these in the `env` block of your MCP server entry — this project does not use a `.env` file.

| Variable | Default | Description |
|----------|---------|-------------|
| `DATAVERSE_AUTH_TYPE` | `interactive` | Authentication method: `interactive` (recommended) or `azure_cli` |
| `DATAVERSE_ALLOW_WRITE` | unset (off) | Set to `true` to register create, update, associate, merge and schema mutation tools |
| `DATAVERSE_ALLOW_DELETE` | unset (off) | Set to `true` to register delete and disassociate tools |
| `DATAVERSE_TOOLS` | — | Comma-separated tool categories to register (e.g. `core,schema,security`). Unset registers all; `core` is always registered |
| `DATAVERSE_WHITELIST` | — | Comma-separated allowed environment hostnames (e.g. `yourorg.crm.dynamics.com`). Tool calls to any other environment are rejected |

> [!WARNING]
> **Leaving `DATAVERSE_WHITELIST` unset is risky.** Tools accept a `dataverse_url` per call and the server mints a bearer token for whatever environment is supplied, so a compromised or misbehaving agent can point your credentials at *any* Dataverse environment. Set the whitelist for any non-local or shared deployment.

Full reference — every variable, token cache and multi-tenant options: **[Configuration](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Configuration)** · write/delete gating and hardening: **[Safety and Permissions](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Safety-and-Permissions)**

---

## Documentation

| Page | What's there |
|------|--------------|
| [Installation](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Installation) | uv/uvx install, PyPI and local checkout, SDK version requirements |
| [Client Setup](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Client-Setup) | Claude Desktop, Claude Code, GitHub Copilot / VS Code, config file paths per OS |
| [Authentication](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Authentication) | Interactive vs Azure CLI, token cache persistence, multi-tenant profiles |
| [First Steps](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/First-Steps) | Discovering environments and running your first queries |
| [Configuration](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Configuration) | Complete environment variable reference |
| [Safety and Permissions](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Safety-and-Permissions) | Write/delete gates, environment whitelisting, filesystem confinement |
| [Tool Categories](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tool-Categories) | What each category contains and how `DATAVERSE_TOOLS` gating composes |
| [Tool Index](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Tool-Index) | All 200 tools with gates and descriptions |
| [How It Works](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/How-It-Works) | Retries, response size cap, paging, error contract, multi-environment targeting |
| [Troubleshooting](https://github.com/ryanmichaeljames/dataverse-mcp/wiki/Troubleshooting) | Common startup, auth and tool-call failures |

---

## Contributing

Issues and pull requests are welcome — see the [issue templates](https://github.com/ryanmichaeljames/dataverse-mcp/issues/new/choose). To work on the server locally:

```bash
uv sync                                       # install dependencies
uv run mcp dev src/dataverse_mcp/server.py    # MCP inspector (interactive testing)
uv run python -m dataverse_mcp.server         # run the server directly
```

Restart the MCP server in your client after code changes to pick up the new source.

## Security, changelog, license

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md). Release notes are in [CHANGELOG.md](CHANGELOG.md). Licensed MIT — see [LICENSE](LICENSE).

---

## Disclaimer

Independent community project. Not affiliated with, endorsed by, or supported by Microsoft. For the first-party runtime server, see Microsoft's [Dataverse MCP Server](https://learn.microsoft.com/en-us/power-platform/release-plan/2025wave1/data-platform/dataverse-mcp-server).

"Dataverse" is a trademark of the President and Fellows of Harvard College. "Microsoft Dataverse" and "Power Platform" are trademarks of the Microsoft group of companies. Used here only to describe the systems this tool works with.
