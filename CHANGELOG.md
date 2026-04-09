# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-04-09

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

[Unreleased]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ryanmichaeljames/dataverse-mcp/releases/tag/v0.1.0
