# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-05-05

### Added
- `dataverse_list_environments` tool to list Power Platform environments available to the authenticated user via the admin API

### Changed
- Allow one MCP server instance to target different Dataverse environments per tool call via `dataverse_url`, while keeping `DATAVERSE_URL` as a temporary backward-compatible fallback
- Remove the `.env`-based setup flow from documentation and document MCP `env` configuration as the only supported configuration path
- Simplify `dataverse_list_environments` so it always returns the full normalized environment payload instead of supporting field selection
- Refresh README usage guidance to document local source-based VS Code MCP setup, no-build development workflow, and the current environments tool behavior

## [0.1.0] - 2026-04-09

### Added
- First stable release — all 7 tools verified against a live Dataverse environment
- `dataverse_list_solutions` — list installed solutions with filtering and pagination
- `dataverse_get_solution` — retrieve a single solution by unique name or ID
- `dataverse_list_solution_components` — list components in a solution with type codes and display names
- `dataverse_query_table` — query records from any table with OData filtering, column selection, sorting, and expand
- `dataverse_get_record` — retrieve a single record by ID from any table
- `dataverse_list_tables` — list available tables with metadata
- `dataverse_get_table_metadata` — retrieve detailed schema metadata for a table including columns and relationships

### Fixed
- Pin `httpx>=0.20.0,<1.0` to prevent `uvx --prerelease=allow` from resolving `httpx 1.0.dev` which removed `TransportError` from the top-level namespace

## [0.1.0b2] - 2026-04-09

### Fixed
- Pin `httpx>=0.20.0,<1.0` to prevent `uvx --prerelease=allow` from resolving `httpx 1.0.dev` which removed `TransportError` from the top-level namespace

## [0.1.0b1] - 2026-04-09

### Added
- Initial project setup with FastMCP server using stdio transport for VS Code Copilot integration
- `DataverseClient` wrapper with lifespan management — initializes on startup, cleans up on shutdown
- Authentication factory supporting `interactive`, `client_secret`, and `azure_cli` auth types via `DATAVERSE_AUTH_TYPE` environment variable
- `dataverse_list_solutions` tool — list installed solutions in the Dataverse environment with filtering and pagination
- `dataverse_get_solution` tool — retrieve a single solution by unique name
- `dataverse_list_solution_components` tool — list components belonging to a solution, with component type codes and display names
- `dataverse_query_table` tool — query records from any Dataverse table with OData filtering, column selection, sorting, and expand support
- `dataverse_get_record` tool — retrieve a single record by ID from any Dataverse table
- `dataverse_list_tables` tool — list available tables (entities) in the Dataverse environment with metadata
- `dataverse_get_table_metadata` tool — retrieve detailed schema metadata for a specific table including columns and relationships
- Pydantic v2 input models for all tools with field validation and constraints
- Structured JSON responses for all tools with consistent `error`, `count`, and `has_more` fields
- Logging to stderr via Python `logging` module — stdout reserved for stdio transport

[Unreleased]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0b2...v0.1.0
[0.1.0b2]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/ryanmichaeljames/dataverse-mcp/releases/tag/v0.1.0b1
