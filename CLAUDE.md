# dataverse-mcp

## Purpose

This repository is a Python FastMCP server for Microsoft Dataverse (Web API v9.2), with tools implemented under `src/dataverse_mcp/tools/`.

## Keep These Invariants

- Python 3.10+, Pydantic v2, `mcp[cli]`, `httpx>=0.20.0,<1.0`
- Follow existing project patterns; do not introduce new architectural styles without clear need
- Tool modules stay domain-scoped (`solutions.py`, `tables.py`, `metadata.py`, `environments.py`)
- Use async `httpx` patterns already established in this codebase

## Tool Design Rules

- Tool names: `dataverse_{verb}_{noun}`
- Input models: `{Action}{Resource}Input` in `src/dataverse_mcp/models.py`
- Input models must use `ConfigDict(str_strip_whitespace=True, extra='forbid')`
- Use `Field(...)` constraints/descriptions on public tool inputs
- Set tool annotations truthfully:
  - `readOnlyHint=True` only for read operations
  - `destructiveHint=True` for delete operations
  - `idempotentHint=True` for GET/PUT/DELETE style behavior
  - `openWorldHint=True`

## Response and Error Contract

- Every tool returns `str` containing JSON (no Markdown)
- Do not raise uncaught exceptions from tools
- HTTP/API failures should return:

```json
{"error": true, "message": "Actionable message"}
```

- Catch `httpx.HTTPStatusError` before broad exceptions
- Include `count` on list-style responses
- Include `has_more` where pagination applies

## Dataverse-Specific Conventions

- Use `entity_set_name` where table collection names are required
- Prefer explicit `select` in queries to keep payloads small
- Default paging should remain conservative (`top=50` unless tool-specific needs differ)
- Navigation properties in `expand` are case-sensitive
- Use lowercase logical names where Dataverse expects logical names
- Resolve Dataverse base URL from per-call input first, then configured fallback

## Security and Config Hygiene

- Never hardcode or commit credentials, tenant IDs, client IDs, or real org URLs
- Use generic placeholders in docs/examples (`yourorg`, `your-tenant-id`, `your-client-id`)
- Keep authentication env-driven (`DATAVERSE_AUTH_TYPE` supports `interactive` and `azure_cli`)
- Write/delete behavior is controlled by server env flags (`DATAVERSE_ALLOW_WRITE`, `DATAVERSE_ALLOW_DELETE`)

## Logging and Imports

- Never `print()`; use `logging` (stderr)
- Module logger pattern: `logger = logging.getLogger(__name__)`
- Import order: standard library, third-party, local

## Changelog Policy

- Update `CHANGELOG.md` for notable behavior/tool changes
- Add entries only under `[Unreleased]`; do not modify released sections
