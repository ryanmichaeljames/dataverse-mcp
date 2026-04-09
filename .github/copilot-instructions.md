# GitHub Copilot Instructions

## Priority Guidelines

When generating code for this repository:

1. **Version Compatibility**: Python 3.10+, Pydantic v2, MCP SDK (mcp[cli]), PowerPlatform-Dataverse-Client 0.1.0b7
2. **Codebase Patterns**: Follow the patterns established in existing files — never introduce conventions not used here
3. **Architectural Consistency**: This is a FastMCP server with a modular tool layout under `src/dataverse_mcp/tools/`
4. **Code Quality**: Prioritize maintainability, security, and testability in all generated code

## Project Overview

This is a **read-only** MCP (Model Context Protocol) server that enables VS Code Copilot agents to query a Microsoft Dataverse environment during development. It uses the official `PowerPlatform-Dataverse-Client` Python SDK and exposes tools for retrieving solutions, querying tables, and inspecting metadata.

**Transport**: stdio (local VS Code Copilot integration)

## Technology Stack

| Technology | Version | Purpose |
|-----------|---------|---------|
| Python | >=3.10 | Runtime |
| mcp[cli] | latest | MCP server framework (FastMCP) |
| PowerPlatform-Dataverse-Client | 0.1.0b7 | Dataverse SDK |
| azure-identity | latest | Authentication (TokenCredential) |
| Pydantic | v2 | Input validation and schemas |

## Project Structure

```
src/dataverse_mcp/
├── __init__.py          # Package init
├── _app.py              # FastMCP instance (avoids circular imports)
├── server.py            # Entry point, logging setup, tool registration
├── client.py            # DataverseClient wrapper (auth, lifecycle)
├── models.py            # Pydantic input models for all tools
└── tools/
    ├── __init__.py      # Tools package init
    ├── solutions.py     # Solution query tools
    ├── tables.py        # Table record query tools
    └── metadata.py      # Table/column metadata tools
```

## Naming Conventions

### Tool Names
- **Pattern**: `dataverse_{verb}_{noun}` (snake_case with `dataverse_` prefix)
- **Examples**: `dataverse_list_solutions`, `dataverse_get_record`, `dataverse_query_table`
- **Verbs**: `list` (multiple items), `get` (single item by ID/name), `query` (flexible search)

### Pydantic Models
- **Pattern**: `{Action}{Resource}Input` (PascalCase)
- **Examples**: `ListSolutionsInput`, `GetRecordInput`, `QueryTableInput`
- All models use `ConfigDict(str_strip_whitespace=True, extra='forbid')`
- All fields use `Field(...)` with description, examples, and constraints

### Python Files
- snake_case for all module names
- One tool domain per file in `tools/`

## Tool Implementation Pattern

Every tool MUST follow this exact pattern:

```python
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

class ExampleToolInput(BaseModel):
    """Input for the example tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

    param: str = Field(
        ...,
        description="Clear description with example (e.g., 'account')",
        min_length=1,
    )
    optional_param: int | None = Field(
        default=50,
        description="Optional param with default and constraints",
        ge=1,
        le=5000,
    )

@mcp.tool(
    name="dataverse_example_tool",
    annotations={
        "title": "Example Tool",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def dataverse_example_tool(params: ExampleToolInput) -> str:
    """Concise description of what this tool does.

    Detailed explanation for the agent including:
    - When to use this tool vs alternatives
    - What the return format looks like
    - Any important constraints or limitations
    """
    # Implementation with error handling
    pass
```

### Tool Annotations — ALL tools in this project are read-only:
- `readOnlyHint`: Always `True`
- `destructiveHint`: Always `False`
- `idempotentHint`: Always `True`
- `openWorldHint`: Always `True`

## Error Handling

### Pattern
```python
import json
import logging
from PowerPlatform.Dataverse.core.errors import DataverseError, HttpError

logger = logging.getLogger(__name__)

try:
    result = client.records.get(...)
except HttpError as e:
    logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
    return json.dumps({
        "error": True,
        "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
        "is_transient": e.is_transient,
    })
except DataverseError as e:
    logger.error("Dataverse error: %s", e.message)
    return json.dumps({
        "error": True,
        "message": str(e),
    })
except Exception as e:
    logger.exception("Unexpected error")
    return json.dumps({
        "error": True,
        "message": f"Unexpected error: {type(e).__name__}: {e}",
    })
```

### Rules
- **Never** raise exceptions from tools — always return a JSON error response so the agent can act on it
- **Always** log errors with `logger.error()` or `logger.exception()`
- **Always** include `"error": True` in error responses
- **Always** include actionable `"message"` text the agent can use to self-correct
- Catch `HttpError` before `DataverseError` (more specific first)

## Logging

- Use `logging` module, never `print()` — stdout is reserved for stdio transport
- Logger per module: `logger = logging.getLogger(__name__)`
- Log to stderr (configured at server startup)
- Log levels: `DEBUG` for detailed traces, `INFO` for operations, `WARNING` for recoverable issues, `ERROR` for failures

## Import Ordering

Always follow this order with blank lines between groups:

```python
# 1. Standard library
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

# 2. Third-party
from azure.identity import AzureCliCredential, ClientSecretCredential
from PowerPlatform.Dataverse.client import DataverseClient
from pydantic import BaseModel, ConfigDict, Field

# 3. Local
from dataverse_mcp.client import get_dataverse_client
from dataverse_mcp.models import QueryTableInput
```

## Pydantic v2 Conventions

- Use `model_config = ConfigDict(...)` — never nested `class Config`
- Use `field_validator` — never deprecated `validator`
- Use `model_dump()` — never deprecated `dict()`
- Validators require `@classmethod` decorator
- Type hints required on all validator methods
- Use `str | None` — never `Optional[str]` (Python 3.10+ union syntax)

## Dataverse SDK Patterns

### Query Patterns
- Always include `select` to limit columns — never fetch all columns
- Always include `include_annotations="OData.Community.Display.V1.FormattedValue"` for formatted lookup/option set values
- Use lowercase logical names in `filter` expressions (case-sensitive!)
- Navigation properties in `expand` are case-sensitive
- Default `top=50` to prevent overwhelming agent context

### Client Lifecycle
- Use FastMCP `lifespan` to manage DataverseClient — init on startup, cleanup on shutdown
- Never create a new client per tool call
- Access client via `ctx.request_context.lifespan_context`

### Authentication
- Auth type selected via `DATAVERSE_AUTH_TYPE` environment variable
- Supported: `interactive`, `client_secret`, `azure_cli`
- Credentials come from environment variables, never hardcoded

## Response Format

All tools return `str` (JSON-serialized). Response structure:

### Success
```json
{
  "records": [...],
  "count": 10,
  "has_more": false
}
```

### Error
```json
{
  "error": true,
  "message": "Actionable error description"
}
```

- JSON only — no Markdown responses
- Always include `count` for list operations
- Always include `has_more` for paginated operations
- Solution component types include both integer code AND display name

## Changelog

This project maintains a `CHANGELOG.md` at the root following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

### Rules
- **Always** update `CHANGELOG.md` when making any notable change — new tools, bug fixes, breaking changes, dependency updates
- **Never** edit released version entries — only add to `[Unreleased]`
- All entries go under `## [Unreleased]` until a version is released

### Sections (use only what applies)
- `### Added` — new tools, features, or capabilities
- `### Changed` — changes to existing tools or behavior
- `### Deprecated` — features that will be removed in a future release
- `### Removed` — removed features or tools
- `### Fixed` — bug fixes
- `### Security` — security patches or dependency updates for CVEs

### Commit Convention
Use [Conventional Commits](https://www.conventionalcommits.org/) for all commits:

| Type | Description | Changelog Section |
|------|-------------|-------------------|
| `feat` | New tool or feature | Added |
| `fix` | Bug fix | Fixed |
| `refactor` | Code restructure (no behavior change) | Changed |
| `perf` | Performance improvement | Changed |
| `docs` | Documentation only | (omit) |
| `chore` | Maintenance, deps | (omit unless notable) |
| `ci` | CI/CD changes | (omit) |
| `revert` | Revert a commit | Removed |

Breaking changes: append `!` to type (e.g., `feat!`) and add `BREAKING CHANGE:` footer → bump MAJOR version.

### Version Bumping
- `feat` → MINOR bump
- `fix` / `perf` / `refactor` → PATCH bump
- `feat!` / `BREAKING CHANGE` → MAJOR bump

### Entry Format
```markdown
## [Unreleased]

### Added
- `dataverse_list_solutions` tool for querying installed solutions (#12)

### Fixed
- Pagination off-by-one in `dataverse_query_table` (#8)
```

## Security

- Never expose credentials in logs or responses
- Never hardcode secrets — use environment variables
- All tools are read-only — no write, update, or delete operations
- Validate all inputs via Pydantic before passing to SDK
- Sanitize OData filter strings to prevent injection

### Source Control Safety

- **Never** commit real Dataverse URLs, tenant IDs, client IDs, or any environment-specific identifiers to source control
- `.vscode/mcp.json` is gitignored — it contains user-specific environment URLs and must not be committed
- `.env` is gitignored — credentials and environment config live here, never in tracked files
- Only `.env.example` (with placeholder values like `https://yourorg.crm.dynamics.com`) is committed
- In documentation, README, and code examples, always use generic placeholders (`yourorg`, `your-tenant-id`, `your-client-id`) — never real values
- Test scripts and smoke tests must read URLs/credentials from environment variables, never hardcode them
- When generating code, configs, or documentation, verify no real org names, URLs, GUIDs, or identifiers from test environments appear in the output
