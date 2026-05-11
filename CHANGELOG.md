# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.3.2] - 2026-05-11

### Fixed
- `dataverse_execute_batch` POST/PATCH/PUT operations now include `Content-Length` on the inner HTTP request, resolving Dataverse error **0x80048d19** ("stream not readable") for all write operations (#34)
- `dataverse_execute_batch` change set operations now include the required `Content-ID` header on each part, resolving Dataverse error **0x80060888** ("Content-ID header not present") (#34)
- `dataverse_execute_batch` GET operations no longer incorrectly include `Content-Type: application/json` when there is no body (#34)
- `dataverse_execute_batch` change set response parser no longer raises `RecursionError`; the parser now correctly splits part headers from body before checking for nested multipart content (#34)

### Added
- Debug logging in `dataverse_execute_batch` for batch boundary, per-operation method/URL, response status, and per-result status codes (#34)

## [1.3.1] - 2026-05-11

### Fixed
- `azure_cli` auth now automatically adds known Azure CLI install directories (`C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin`) to `PATH` when `az` is not found, resolving `CredentialUnavailableError` when the MCP server is launched from VS Code without a login shell

## [1.3.0] - 2026-05-11

### Security
- Removed `client_secret` (service principal) authentication support — `DATAVERSE_AUTH_TYPE` now only accepts `interactive` and `azure_cli`; long-lived client secrets are unnecessary for local VS Code Copilot use cases and carry elevated risk

## [1.2.0] - 2026-05-11

### Added
- `DATAVERSE_ALLOW_WRITE` and `DATAVERSE_ALLOW_DELETE` environment variables to gate write and delete tool registration at server startup — when not set, those tools are not exposed to the agent at all

### Changed
- Write tools (`dataverse_associate_records`, `dataverse_merge_records`, all create/update schema tools) now require `DATAVERSE_ALLOW_WRITE=true` in the MCP server `env` to be registered
- `dataverse_execute_batch` is always registered for GET-only batch usage; non-GET operations in a batch now require `DATAVERSE_ALLOW_WRITE=true`
- Delete tools (`dataverse_disassociate_records`, `dataverse_delete_table`, `dataverse_delete_column`, `dataverse_delete_relationship`, `dataverse_delete_choice`, `dataverse_delete_choice_option`) now require `DATAVERSE_ALLOW_DELETE=true` in the MCP server `env` to be registered

### Removed
- **BREAKING:** Per-call `allow_write` and `allow_delete` parameters removed from all write and delete tool inputs — access is now controlled entirely by the MCP server `env` flags
- **BREAKING:** Preview mode removed from all write and delete tools

## [1.1.0] - 2026-05-11

### Added
- `dataverse_retrieve_user_privileges` tool to list all security privileges assigned to a system user via their role memberships; returns `PrivilegeName`, `Depth`, and `BusinessUnitId` for each privilege (#9)
- `dataverse_retrieve_principal_access` tool to check the access rights a system user has to a specific record; returns `AccessRights` bitmask and named rights list (#9)
- `dataverse_associate_records` tool to create an association between two records via a collection-valued navigation property (`$ref`); supports `allow_write` safety guard with preview mode (#16)
- `dataverse_disassociate_records` tool to remove an association between two records; supports `allow_delete` safety guard with preview mode (#16)
- `dataverse_merge_records` tool to merge a subordinate record into a target record using the Dataverse `Merge` action; supports `account`, `contact`, `lead`, and `incident` entity types; `allow_write` safety guard with preview mode (#15)
- `dataverse_execute_batch` tool to execute up to 1,000 OData operations in a single `$batch` request; supports atomic change sets, `continue_on_error`, and preview mode; parses per-operation responses (#15)

### Changed
- Server description updated from "read-only" to reflect full CRUD capability; `pyproject.toml`, `__init__.py`, `copilot-instructions.md`, and `README.md` updated accordingly
- Tool annotation guidelines updated: `readOnlyHint`, `destructiveHint`, and `idempotentHint` are now set per-tool based on actual behavior rather than always read-only
- `dataverse_create_column` tool to add a new column to a table with typed attribute metadata, display name, required level, and type-specific properties; supports `allow_write` safety guard with preview mode (#11)
- `dataverse_update_column` tool to update an existing column via full PUT replacement; agent must fetch the current definition via `dataverse_get_column` first; supports `allow_write` safety guard with preview mode (#11)
- `dataverse_delete_column` tool to permanently delete a custom column; fetches current definition for preview; supports `allow_delete` safety guard (#11)
- `dataverse_create_one_to_many_relationship` tool to create a 1:N relationship and its lookup column; supports `allow_write` safety guard with preview mode (#12)
- `dataverse_create_many_to_many_relationship` tool to create an N:N relationship and its intersect table; supports `allow_write` safety guard with preview mode (#12)
- `dataverse_create_multi_table_lookup` tool to create a polymorphic (multi-table) lookup column via `CreatePolymorphicLookupAttribute`; supports `allow_write` safety guard with preview mode (#12)
- `dataverse_update_relationship` tool to update an existing relationship via full PUT; agent must fetch current definition via `dataverse_get_relationship` first; supports `allow_write` safety guard with preview mode (#12)
- `dataverse_delete_relationship` tool to permanently delete a relationship by MetadataId; supports `allow_delete` safety guard (#12)
- `dataverse_create_choice` tool to create a new global choice with initial options; supports `allow_write` safety guard with preview mode (#13)
- `dataverse_update_choice` tool to update an existing global choice via full PUT replacement; agent must fetch the current definition via `dataverse_get_choice` first; supports `allow_write` safety guard with preview mode (#13)
- `dataverse_delete_choice` tool to permanently delete a global choice by logical name; supports `allow_delete` safety guard (#13)
- `dataverse_add_choice_option` tool to add a new option to a global or local choice via `InsertOptionValue`; supports `allow_write` safety guard with preview mode (#13)
- `dataverse_update_choice_option` tool to update the display label of an existing option via `UpdateOptionValue`; supports `allow_write` safety guard with preview mode (#13)
- `dataverse_delete_choice_option` tool to remove a specific option value from a global or local choice via `DeleteOptionValue`; supports `allow_delete` safety guard (#13)
- `dataverse_reorder_choice_options` tool to reorder all options of a global or local choice via `OrderOption`; supports `allow_write` safety guard with preview mode (#13)
- `dataverse_publish_customizations` tool to publish schema changes to make them visible in the UI; supports targeted publish (tables, choices, relationships) via `PublishXml` or full environment publish via `PublishAllXml`; supports `allow_write` safety guard with preview mode (#14)
- `dataverse_create_table` tool to create a new custom table with display names, schema name, ownership type, and primary name attribute; supports `allow_write` safety guard with preview mode (#8)
- `dataverse_update_table` tool to update an existing table's display name or description by fetching the current definition and PUTting the merged result; supports `allow_write` safety guard with preview mode (#8)
- `dataverse_delete_table` tool to permanently delete a custom table; supports `allow_delete` safety guard with preview mode (#8)
- `dataverse_whoami` tool to return the authenticated user's `UserId`, `BusinessUnitId`, and `OrganizationId` from the WhoAmI endpoint (#10)
- `dataverse_get_entity_sets` tool to list OData EntitySet names from the service document with `top` (default 50, max 1000) and `contains` substring filter, returning `has_more` when results are truncated (#10)
- `dataverse_list_choices` tool to list all global choice (option set) definitions in the environment with optional field selection and pagination (#7)
- `dataverse_get_choice` tool to retrieve a specific global choice by name or MetadataId, including all option values and labels (#7)
- `dataverse_list_relationships` tool to list relationship definitions for a table (OneToMany, ManyToOne, ManyToMany) or all relationships in the environment (#6)
- `dataverse_get_relationship` tool to retrieve full metadata for a single relationship by schema name, including cascade config and navigation property names (#6)
- `dataverse_check_relationship_eligibility` tool to check whether a table can participate in a relationship before attempting to create one (#6)
- `dataverse_list_columns` tool to list all column (attribute) definitions for a table with optional type and field filtering (#5)
- `dataverse_get_column` tool to retrieve full metadata for a single column including type-specific properties (#5)
- `dataverse_list_choice_column_options` tool to get all option values (integer code + label) for Picklist and MultiSelectPicklist columns (#5)
- Improved clarity and conciseness of tool docstrings across `solutions.py`, `tables.py`, `environments.py`, and `metadata.py` — removed duplicate sentences, simplified HTTP implementation details, and tightened wording (#24)

### Fixed
- `dataverse_list_relationships` now returns `has_more` and consistently applies the `top` limit (#6)
- `dataverse_check_relationship_eligibility` now uses Dataverse relationship eligibility endpoints (`CanBeReferenced`, `CanBeReferencing`, `CanManyToMany`) (#6)
- `dataverse_create_table` now marks timeout responses as errors (`error: true`) with `is_transient: true` so clients do not misinterpret timed-out creates as success (#8)
- `dataverse_delete_table` now marks timeout responses as errors (`error: true`) with `is_transient: true` so clients do not misinterpret timed-out deletes as success (#8)
- `dataverse_update_table` now validates `MetadataId` is present before constructing the PUT URL, returning a clear error if missing (#8)
- `dataverse_delete_table` now enforces local safety checks, blocking deletion unless `IsCustomEntity=true` and `IsManaged=false` to prevent accidental system/managed table deletion attempts (#8)
- `dataverse_create_column` now blocks reserved keys in `type_specific_properties` to prevent overriding tool-managed metadata fields
- `dataverse_delete_column` and `dataverse_delete_relationship` now fetch current metadata for preview and enforce custom/unmanaged deletion safety checks before DELETE
- Create operation annotations now mark `idempotentHint: false` for non-idempotent POST create tools (`dataverse_create_column`, `dataverse_create_one_to_many_relationship`, `dataverse_create_many_to_many_relationship`, `dataverse_create_multi_table_lookup`)
- `PublishCustomizationsInput` now requires at least one targeted publish item when `publish_all=false`
- Targeted `PublishXml` payload generation now escapes input safely via XML element construction
- `dataverse_associate_records` and `dataverse_disassociate_records` now return a clear JSON error when no Dataverse URL is available and move blocking HTTP calls off the async event loop (#16)

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

[Unreleased]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.0.0...v1.1.0
[1.0.0]:https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0b2...v0.1.0
[0.1.0b2]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/ryanmichaeljames/dataverse-mcp/releases/tag/v0.1.0b1
