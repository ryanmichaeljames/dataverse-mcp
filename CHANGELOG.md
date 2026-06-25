# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Thirteen Custom API management tools in new module `src/dataverse_mcp/tools/custom_apis.py`
  (`customapis` category), covering three Dataverse entity sets (standard OData, not metadata API):
  `dataverse_list_custom_apis` (read, `@tool`) — GET `customapis`; optional OData `filter` and
  `top`; returns customapiid, uniquename, name, displayname, bindingtype, isfunction, isprivate,
  allowedcustomprocessingsteptype, count, has_more.
  `dataverse_get_custom_api` (read, `@tool`) — GET `customapis(<id>)` with `$expand` for
  `CustomAPIRequestParameters` and `CustomAPIResponseProperties`; returns `{"custom_api": {...}}`.
  `dataverse_create_custom_api` (write, `@write_tool`) — POST `customapis`; required `uniquename`
  (with publisher prefix, immutable) and `name`; optional `displayname`, `description`,
  `binding_type` (0=Global, 1=Entity, 2=EntityCollection, default 0), `is_function` (default false),
  `is_private` (default false), `allowed_custom_processing_step_type` (default 0),
  `bound_entity_logical_name` (required when binding_type=1/2); model_validator enforces this.
  `dataverse_update_custom_api` (write, `@write_tool`) — PATCH mutable fields only (name,
  displayname, description, is_private, execute_privilege_name); model_validator requires ≥1 field.
  `dataverse_delete_custom_api` (delete, `@delete_tool`) — DELETE `customapis(<id>)`; cascades
  to associated parameters and properties.
  `dataverse_list_custom_api_request_parameters` (read, `@tool`) — GET `customapirequestparameters`
  filtered by `_customapiid_value`; optional additional OData filter and top; returns
  customapirequestparameterid, uniquename, name, displayname, type, isoptional, count, has_more.
  `dataverse_create_custom_api_request_parameter` (write, `@write_tool`) — POST
  `customapirequestparameters`; required `custom_api_id`, `uniquename`, `type` (0-12, immutable),
  `name`; optional `displayname`, `description`, `is_optional` (default false); uses
  `customapiid@odata.bind` lookup binding.
  `dataverse_update_custom_api_request_parameter` (write, `@write_tool`) — PATCH mutable fields
  (name, displayname, description, is_optional); model_validator requires ≥1 field.
  `dataverse_delete_custom_api_request_parameter` (delete, `@delete_tool`) — DELETE by
  `request_parameter_id` GUID.
  `dataverse_list_custom_api_response_properties` (read, `@tool`) — GET
  `customapiresponseproperties` filtered by `_customapiid_value`; optional additional OData filter
  and top; returns customapiresponsepropertyid, uniquename, name, displayname, type, count, has_more.
  `dataverse_create_custom_api_response_property` (write, `@write_tool`) — POST
  `customapiresponseproperties`; required `custom_api_id`, `uniquename`, `type` (0-12, immutable),
  `name`; optional `displayname`, `description`; uses `customapiid@odata.bind` lookup binding.
  `dataverse_update_custom_api_response_property` (write, `@write_tool`) — PATCH mutable fields
  (name, displayname, description); model_validator requires ≥1 field.
  `dataverse_delete_custom_api_response_property` (delete, `@delete_tool`) — DELETE by
  `response_property_id` GUID.
  Thirteen input models added to `src/dataverse_mcp/models.py`:
  `ListCustomApisInput`, `GetCustomApiInput`, `CreateCustomApiInput` (model_validator enforces
  bound_entity_logical_name when binding_type=1/2), `UpdateCustomApiInput`, `DeleteCustomApiInput`,
  `ListCustomApiRequestParametersInput`, `CreateCustomApiRequestParameterInput`,
  `UpdateCustomApiRequestParameterInput`, `DeleteCustomApiRequestParameterInput`,
  `ListCustomApiResponsePropertiesInput`, `CreateCustomApiResponsePropertyInput`,
  `UpdateCustomApiResponsePropertyInput`, `DeleteCustomApiResponsePropertyInput`.
  New `customapis` category added to `_KNOWN_CATEGORIES` in `src/dataverse_mcp/_app.py`.
  Module registered in `src/dataverse_mcp/server.py`. Total tool count: 157 → 170.
  Type enum for parameters and properties: 0=Boolean, 1=DateTime, 2=Decimal, 3=Entity,
  4=EntityCollection, 5=EntityReference, 6=Float, 7=Integer, 8=Money, 9=Picklist,
  10=String, 11=StringArray, 12=Guid.
- `dataverse_create_column` now supports `Memo` (multi-line text / `MemoAttributeMetadata`) as a column type; `MaxLength` defaults to `2000` and `IsValidForAdvancedFind` is automatically omitted (Dataverse rejects it for Memo columns).
- `dataverse_create_column` now supports `boolean_true_label` and `boolean_false_label` fields for Boolean columns; the required `OptionSet` with `TrueOption`/`FalseOption` is built automatically (defaults: "Yes" / "No").
- `dataverse_create_column` now supports `global_choice_name` for `Picklist` and `MultiSelectPicklist` columns to bind the column to an existing global choice; the tool resolves the choice name to its MetadataId GUID automatically before creating the column.

## [3.2.0] - 2026-06-24

### Added
- Five web resource CRUD tools in new module `src/dataverse_mcp/tools/web_resources.py`
  (`webresources` category):
  `dataverse_list_web_resources` (read, `@tool`) — GET `webresources`; optional filter by
  `web_resource_type` (Picklist int) and/or `name_contains` (OData `contains(name,'...')`);
  ordered by `name`; returns records enriched with `webresourcetype_label`, `count`, `has_more`.
  Content field excluded from list responses (can be very large).
  `dataverse_get_web_resource` (read, `@tool`) — GET `webresources(<id>)`; returns
  `{"record": {...}}` stripped of `@odata.context`, enriched with `webresourcetype_label`;
  `include_content=true` adds the base64 `content` field.
  `dataverse_create_web_resource` (write, `@write_tool`) — POST `webresources` with required
  `name`, `webresourcetype`, `content` (base64); optional `displayname`, `description`;
  extracts new GUID from `OData-EntityId` response header; returns `{"created": true, "id": ...}`.
  Requires `DATAVERSE_ALLOW_WRITE=true`. Caller must publish afterward via
  `dataverse_publish_customizations`.
  `dataverse_update_web_resource` (write, `@write_tool`) — PATCH `webresources(<id>)` with
  at least one of `content`, `display_name`, `description`; returns `{"updated": true, "id": ...}`.
  Requires `DATAVERSE_ALLOW_WRITE=true`. Caller must publish afterward.
  `dataverse_delete_web_resource` (delete, `@delete_tool`) — DELETE `webresources(<id>)`;
  returns `{"deleted": true, "id": ...}`. Requires `DATAVERSE_ALLOW_DELETE=true`.
  Five input models added to `src/dataverse_mcp/models.py`:
  `ListWebResourcesInput`, `GetWebResourceInput`, `CreateWebResourceInput`,
  `UpdateWebResourceInput` (model_validator requires at least one updatable field),
  `DeleteWebResourceInput`.
  New `webresources` category added to `_KNOWN_CATEGORIES` in `src/dataverse_mcp/_app.py`.
  Module registered in `src/dataverse_mcp/server.py`. Total tool count: 152 → 157.
  webresourcetype enum confirmed: 1=HTML, 2=CSS, 3=JScript, 4=XML, 5=PNG, 6=JPG, 7=GIF,
  8=Silverlight(XAP), 9=StyleSheet(XSL), 10=ICO, 11=Vector(SVG), 12=String(RESX).

- Three async operation (system job) monitoring tools in new module
  `src/dataverse_mcp/tools/jobs.py` (`jobs` category):
  `dataverse_list_async_operations` (read, `@tool`) — GET `asyncoperations`; optional
  filter by `state_code` (0=Ready,1=Suspended,2=Locked,3=Completed), `status_code`
  (0=Waiting For Resources,10=Waiting,20=In Progress,21=Pausing,22=Canceling,30=Succeeded,
  31=Failed,32=Canceled), or `operation_type` (raw int); ordered by `createdon desc`;
  returns records enriched with `statecode_label`/`statuscode_label`, `count`, `has_more`.
  `dataverse_get_async_operation` (read, `@tool`) — GET `asyncoperations(<id>)`;
  returns `{"record": {...}}` stripped of `@odata.context`, enriched with labels.
  `dataverse_cancel_async_operation` (write, `@write_tool`) — PATCH
  `asyncoperations(<id>)` with `{"statecode": 3, "statuscode": 32}`; returns
  `{"cancelled": true, "async_operation_id": "<id>"}`. The `asyncoperation` entity
  supports direct `statecode`/`statuscode` PATCH (confirmed against the Dataverse
  Web API v9.2 asyncoperation entity reference). Requires `DATAVERSE_ALLOW_WRITE=true`.
  Three input models added to `src/dataverse_mcp/models.py`:
  `ListAsyncOperationsInput`, `GetAsyncOperationInput`, `CancelAsyncOperationInput`.
  New `jobs` category added to `_KNOWN_CATEGORIES` in `src/dataverse_mcp/_app.py`.
  Module registered in `src/dataverse_mcp/server.py`. Total tool count: 149 → 152.

- `dataverse_execute_fetchxml` read-only tool in `src/dataverse_mcp/tools/tables.py` (`core` category).
  Executes a FetchXML query via `GET {entity_set}?fetchXml=<encoded>` and returns `records`, `count`,
  `has_more` (from `@Microsoft.Dynamics.CRM.morerecords`), `paging_cookie` (from
  `@Microsoft.Dynamics.CRM.fetchxmlpagingcookie`, when present), and `total_record_count` (from
  `@Microsoft.Dynamics.CRM.totalrecordcount`, when present and non-negative). Supports
  `include_formatted_values` via `Prefer: odata.include-annotations` header. FetchXML uses paging
  cookies rather than `@odata.nextLink`; this tool returns one page plus the paging metadata so
  callers can iterate. `ExecuteFetchXmlInput` added to `src/dataverse_mcp/models.py` with a
  `field_validator` that rejects input not starting with `<fetch`. Total tool count: 148 → 149.

- Five solution ALM tools in `src/dataverse_mcp/tools/solutions.py` (all `solutions` category):
  `dataverse_export_solution` (read, `@tool`) — POST `ExportSolution`; optional `output_path` writes the
  decoded .zip to a local path and returns metadata only; inline base64 returned when under ~3 MB, structured
  error otherwise; `_SOLUTION_JOB_TIMEOUT = 600.0` and `_INLINE_FILE_MAX_BYTES = 3_000_000` constants added.
  `dataverse_import_solution` (write, `@write_tool`) — POST `ImportSolutionAsync`; accepts inline base64
  (`customization_file`) XOR local zip path (`input_path`); generates `ImportJobId` GUID when not supplied;
  returns `import_job_id`, `async_operation_id`, `import_job_key`.
  `dataverse_get_import_job` (read, `@tool`) — GET `importjobs(<id>)`; excludes the large `data` XML column
  by default; `include_data=true` fetches it; returns `progress` and `completed` alongside the record.
  `dataverse_list_import_jobs` (read, `@tool`) — GET `importjobs`; optional filter by `solution_name`;
  ordered by `createdon desc`; count/has_more.
  `dataverse_clone_solution_as_patch` (write, `@write_tool`) — POST bound action
  `solutions(<id>)/Microsoft.Dynamics.CRM.CloneAsPatch`; resolves parent solution via `_resolve_solution_record`;
  returns `patch_solution_id`.
  Five matching input models added to `src/dataverse_mcp/models.py`: `ExportSolutionInput`,
  `ImportSolutionInput` (exactly-one-of validator for `customization_file`/`input_path`),
  `GetImportJobInput`, `ListImportJobsInput`, `CloneSolutionAsPatchInput`.
  This introduces the first local filesystem I/O in tool code: export writes the decoded zip when `output_path`
  is given; import reads a zip when `input_path` is given. All OS errors (PermissionError, FileNotFoundError,
  IsADirectoryError) are mapped to the standard `{"error": true, ...}` contract. Total tool count: 143 → 148.

- Tool category gating via new `DATAVERSE_TOOLS` environment variable. When set to a
  comma-separated list of category names, only those categories (plus `core`, which is
  always registered) expose tools to the agent. When unset or empty, all categories
  register (preserving current default behaviour). The 11 categories are: `core`,
  `schema`, `solutions`, `flows`, `forms`, `views`, `apps`, `connections`, `variables`,
  `plugins`, `security`. Gating composes with the existing `DATAVERSE_ALLOW_WRITE` and
  `DATAVERSE_ALLOW_DELETE` flags: a tool registers only when its category is enabled AND
  its write/delete flag (if applicable) is set. Unknown category names are logged as
  warnings and ignored. Implemented via a `category_tools(category)` factory in
  `src/dataverse_mcp/_app.py` that returns `(tool, write_tool, delete_tool)` decorators
  scoped to the given category; all 13 tool modules now bind their decorators from this
  factory. `solutions.py` binds two sets — `category_tools("solutions")` for solution and
  publisher tools, `category_tools("flows")` for cloud-flow tools.
- Twelve security administration tools in a new module
  `src/dataverse_mcp/tools/security.py`. Read-only tools (registered via `@mcp.tool`):
  `dataverse_list_security_roles` (list `roles` entity set, filter/select/top, count/has_more),
  `dataverse_get_security_role` (single role by GUID → `{"record": {...}}`),
  `dataverse_list_teams` (list `teams` entity set, filter/select/top),
  `dataverse_get_team` (single team by GUID),
  `dataverse_list_users` (list `systemusers` entity set, filter/select/top),
  `dataverse_get_user` (single systemuser by GUID),
  `dataverse_list_business_units` (list `businessunits` entity set, filter/select/top).
  Write tools (registered via `@write_tool`, gated on `DATAVERSE_ALLOW_WRITE=true`):
  `dataverse_assign_security_role` (POST `$ref` to `systemuserroles_association` or
  `teamroles_association`), `dataverse_remove_security_role` (DELETE `$ref` from same),
  `dataverse_add_team_members` (POST `$ref` to `teammembership_association`, per-user results),
  `dataverse_remove_team_members` (DELETE `$ref` from `teammembership_association`),
  `dataverse_set_user_state` (enable/disable a systemuser by PATCHing the writable
  `isdisabled` boolean on `systemusers(<id>)`). Twelve matching input models
  (`ListSecurityRolesInput`, `GetSecurityRoleInput`, `ListTeamsInput`, `GetTeamInput`,
  `ListUsersInput`, `GetUserInput`, `ListBusinessUnitsInput`, `AssignSecurityRoleInput`,
  `RemoveSecurityRoleInput`, `AddTeamMembersInput`, `RemoveTeamMembersInput`,
  `SetUserStateInput`) added to `src/dataverse_mcp/models.py`. Module import added to
  `src/dataverse_mcp/server.py`.
- Two read-only solution history tools in `src/dataverse_mcp/tools/solutions.py`:
  `dataverse_get_solution_history` (retrieves a single `msdyn_solutionhistory` record by GUID,
  returns `{"record": {...}}`), `dataverse_list_solution_histories` (lists solution history records
  with optional filtering by `solution_id` or `solution_unique_name`; when `solution_id` is given
  it is resolved to `uniquename` via the `solutions` entity set first, then filtered via the
  `msdyn_name` text column — both identifiers mutually exclusive, both omitted = list all;
  returns `count`/`has_more`). A `_DEFAULT_SOLUTION_HISTORY_SELECT` constant is defined alongside
  the other default-select tuples. Two matching input models (`GetSolutionHistoryInput`,
  `ListSolutionHistoriesInput`) added to `src/dataverse_mcp/models.py`.

## [3.1.0] - 2026-06-22

### Added
- Four environment variable definition tools in `src/dataverse_mcp/tools/environment_variables.py`:
  `dataverse_get_environment_variables` (read, joins definitions with their current value via
  `$expand`, optional solution scoping via componenttype 380 solutioncomponents query; now also
  accepts a `name` arg to look up a single definition by schema name or display name),
  `dataverse_create_environment_variable` (`@write_tool`, creates definition and optional bound
  value record in one call), `dataverse_update_environment_variable` (`@write_tool`, patches
  definition fields and upserts the value record — PATCH if found, POST if not),
  `dataverse_delete_environment_variable` (`@delete_tool`, target=`definition`|`value`|`both`).
  A shared async helper `_resolve_definition_by_name_or_id` is exported from the definitions
  module and resolves a definition GUID from a name (schema name first, display name fallback)
  with structured error returns for zero or multiple matches.
  Four matching input models (`GetEnvironmentVariablesInput`, `CreateEnvironmentVariableInput`,
  `UpdateEnvironmentVariableInput`, `DeleteEnvironmentVariableInput`) updated in
  `src/dataverse_mcp/models.py` (`GetEnvironmentVariablesInput` gains a `name` field).
- Four new environment variable value tools in a new module
  `src/dataverse_mcp/tools/environment_variable_values.py`:
  `dataverse_get_environment_variable_values` (read, accepts value GUID, definition GUID, or
  definition name; returns list shape with `count`/`has_more`),
  `dataverse_create_environment_variable_value` (`@write_tool`, binds via
  `EnvironmentVariableDefinitionId@odata.bind`, accepts definition GUID or name),
  `dataverse_update_environment_variable_value` (`@write_tool`, PATCHes existing value record
  by value GUID, definition GUID, or definition name; returns error if no value record exists),
  `dataverse_delete_environment_variable_value` (`@delete_tool`, deletes the value record only
  by value GUID, definition GUID, or definition name; leaves the definition intact).
  Four matching input models (`GetEnvironmentVariableValuesInput`,
  `CreateEnvironmentVariableValueInput`, `UpdateEnvironmentVariableValueInput`,
  `DeleteEnvironmentVariableValueInput`) added to `src/dataverse_mcp/models.py`.

### Fixed
- Corrected `COMPONENT_TYPE_NAMES` in `src/dataverse_mcp/tools/solutions.py`: moved
  "Environment Variable Definition" and "Environment Variable Value" from their previous
  incorrect entries (372/373) to the authoritative component type codes 380/381, as verified
  against the Microsoft Learn `solutioncomponent` EntityType reference. Entries for 372/373
  are set to "Connector" matching the actual values in the map.

## [3.0.0] - 2026-06-19

### Added
- Three dedicated single-record CRUD tools in `src/dataverse_mcp/tools/tables.py`:
  `dataverse_create_record` (POST, `@write_tool`, returns the new record `id`),
  `dataverse_update_record` (PATCH partial update, `@write_tool`),
  `dataverse_delete_record` (DELETE, `@delete_tool`). Create and update require
  `DATAVERSE_ALLOW_WRITE=true`; delete requires `DATAVERSE_ALLOW_DELETE=true`. All three
  reuse existing `build_headers` / `request_with_retry` / `finalize_response` helpers and
  follow the same error-JSON contract as the rest of the server. Three matching input
  models (`CreateRecordInput`, `UpdateRecordInput`, `DeleteRecordInput`) added to
  `src/dataverse_mcp/models.py`.
- `dataverse_list_views`, `dataverse_list_forms`, `dataverse_list_apps`, and `dataverse_list_tables` now accept a `top` parameter (default 50, range 1–5000) and return `has_more: true` when the result set is at or above the requested limit. All four tools route through the shared `paginate_records` helper, matching the conservative-paging invariant already in place for connection references and plug-in trace logs. Resolves #60.
- Interactive auth now persists the MSAL token cache to disk (OS-encrypted by default) so server restarts no longer force a browser re-prompt while a refresh token is valid. After the first interactive sign-in a secret-free `AuthenticationRecord` sidecar is saved alongside the cache to anchor silent account selection on subsequent startups. Two new env vars control the behaviour: `DATAVERSE_TOKEN_CACHE_PERSIST` (default `true`; set `false` to revert to in-memory-only) and `DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED` (default `false`; opt-in plaintext cache for headless Linux without libsecret). Has no effect on `azure_cli` auth. Resolves #63.
- New `DATAVERSE_TOKEN_CACHE_PROFILE` env var (default empty) that isolates the interactive token cache and its `AuthenticationRecord` sidecar per profile. Set a distinct value in each session to run concurrent servers signed in to different tenants/accounts on the same host without them overwriting each other's cache and pinned account. Must use only `[A-Za-z0-9_-]`; any other character fails fast at startup with an actionable error (silently sanitizing could collapse two distinct profiles onto one cache and defeat the isolation). Empty/unset preserves the previous shared default filenames.
- 29 new plug-in registration MCP tools (`src/dataverse_mcp/tools/plugin_registration.py`) covering the full Dataverse plug-in registration model end-to-end: plug-in assemblies (5 tools), plug-in packages (5 tools), plug-in types (5 tools), SDK messages (2 read-only tools), SDK message filters (2 read-only tools), SDK message processing steps (5 tools), and step images (5 tools). Split: 14 read (`@mcp.tool`), 10 write (`@write_tool`), 5 delete (`@delete_tool`). All 26 input models added to `src/dataverse_mcp/models.py`.
- New `DATAVERSE_AUTH_TIMEOUT_SECONDS` environment variable (default `30`) that caps how long a cold-cache credential acquisition is allowed to block before the call is abandoned with an actionable auth error. Invalid or non-positive values fall back to the default with a logged warning.
- CI quality gate: `uv run pytest tests/ -q` and `uv run ruff check .` now pass locally and in CI without any live credentials. `pyproject.toml` declares `ruff` as a dev dependency, registers an `integration` pytest marker, and sets `asyncio_mode = "auto"` for async tests.
- Targeted unit tests for `odata_quote` (`tests/test_odata_utils.py`) covering no-quote passthrough, single-quote doubling, multiple quotes, and empty string.
- Targeted unit tests for `_parse_retry_after_seconds` (`tests/test_odata_utils.py`) covering numeric header, missing header default, unparseable header default, and negative-value clamp to 0.0.
- Targeted unit tests for `build_inner_request`, `build_batch_body`, and `parse_batch_response` (`tests/test_batch.py`) asserting structural invariants: GET/DELETE carry no body or content headers; POST/PUT/PATCH include `Content-Type` and `Content-Length`; batch body contains correct boundary markers; change-set parts carry `Content-ID`; parse round-trip returns correct status codes and JSON bodies.
- Integration test scaffold (`tests/integration/`) with a secret-free gate: tests run only when `DATAVERSE_INTEGRATION_URL` and `DATAVERSE_INTEGRATION_TOKEN` are set; otherwise every integration test is skipped automatically. Includes a read-only WhoAmI test and write/delete scaffolds gated additionally on `DATAVERSE_ALLOW_WRITE`/`DATAVERSE_ALLOW_DELETE`.

### Changed
- **BREAKING** — The default `DATAVERSE_AUTH_TYPE` is now `interactive` (was `azure_cli`). With the persistent token cache the browser prompt only appears on first sign-in, making interactive the recommended default. Setups relying on the implicit `azure_cli` default (e.g. CI or headless hosts using an existing `az login` session) must now set `DATAVERSE_AUTH_TYPE=azure_cli` explicitly.

### Removed
- **BREAKING** — Removed the `DATAVERSE_URL` environment-variable fallback entirely. `dataverse_url` is now a **required** field on every tool input model; tool calls that omit it will be rejected by Pydantic validation before reaching any tool logic. The `AppContext.fallback_dataverse_url` field and the startup env-read in `dataverse_lifespan` have been removed. `resolve_base_url` no longer accepts an `AppContext` argument. Use `dataverse_list_environments` to discover environment URLs if needed.

### Security
- Interactive token cache is encrypted at rest by default using the OS secret store (Windows DPAPI / macOS Keychain / Linux libsecret). Plaintext storage is never chosen silently — it requires explicit `DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED=true` and logs a startup warning. A persisted refresh token is a long-lived credential; writing it in plaintext to a shared or backed-up host would be a real exposure.
- Hardened `DATAVERSE_WHITELIST` host matching: all URL hostnames and allowlist entries are now canonicalized through a shared `_canonicalize_host` helper before comparison, preventing representation-sensitive bypass via trailing dots (absolute DNS form), Unicode/IDN vs punycode drift, and inconsistent casing.
- Non-standard ports (anything other than the default HTTPS port 443) are now hard-rejected in `_normalize_org_url`; supplying a port such as `:8443` raises `ValueError` rather than silently including it in the normalized URL, closing the port-bypass vector against allowlist matching.
- The startup audit log now records the actual canonical allowed host forms (not just the count) when `DATAVERSE_WHITELIST` is active, making the active allowlist observable in server logs.
- Untrusted agent-supplied `FormXml` and `filter_fetchxml` inputs are now parsed with `defusedxml` instead of stdlib `xml.etree.ElementTree`, defending against entity-expansion ("billion laughs") DoS; malicious payloads containing DTD declarations or entity references are rejected with the project's error-JSON contract rather than hanging or raising uncaught exceptions.

### Fixed
- `azure.core.exceptions.ClientAuthenticationError` (e.g., expired `az login` session, misconfigured `DATAVERSE_AUTH_TYPE`) now surfaces as `{"error": true, "message": "Authentication failed. Run az login …"}` instead of the generic "Unexpected error: …" fallback. The actionable message mentions both `az login` and `DATAVERSE_AUTH_TYPE`; credential detail is only written to the server log (stderr).
- Credential acquisition under the per-scope lock in `build_headers` is now bounded by `DATAVERSE_AUTH_TIMEOUT_SECONDS` (default 30 s). A hung or slow credential acquisition no longer serializes every concurrent caller for that scope indefinitely; after the timeout the lock is released and subsequent callers can proceed. A timed-out acquisition raises `ClientAuthenticationError` and does not poison the token cache.
- `request_with_retry` no longer retries 502/503/504 responses for non-idempotent HTTP methods (POST, PATCH). A gateway error on a write request may arrive after Dataverse has already committed the operation; retrying would risk duplicate writes or associations. 429 throttle responses continue to retry for all methods because a 429 guarantees the request was rejected before processing.
- `dataverse_create_view` and `dataverse_set_app_sitemap` previously returned `"published": true` unconditionally, even when the publish HTTP call failed with an `HTTPStatusError`. The `published` flag now accurately reflects publish outcome: `true` only when the publish call succeeds, `false` when it raises an error (the created/updated record is unaffected).
- Bumped `build-system.requires` from `setuptools>=61.0` to `setuptools>=77.0` to match the PEP 639 SPDX `license = "MIT"` string form declared in `[project]`; setuptools 77+ is required to recognise an inline SPDX expression as the license field — earlier versions would fail to build the package metadata correctly.
- `dataverse_execute_batch` now generates a unique UUID-based multipart boundary per request (e.g. `batch_<32-hex-chars>`) instead of reusing the hardcoded literal `batch_dataverse_mcp`, eliminating any risk of boundary collision with response body content.
- `parse_batch_response` now tolerates both `\r\n` and bare `\n` line endings throughout the multipart response, including header/body separators and inner `multipart/mixed` boundary extraction.
- `parse_batch_response` no longer silently drops batch sub-responses that contain body content but no parseable `HTTP/1.1` status line; those parts now surface as `{"status_code": 0, "error": "<description>"}` entries in the results list so callers can detect and handle failures.

### Documentation
- Rewrote the README **Tools** section: tools are grouped by domain (environment & identity, records & data, tables & columns, relationships, choices, solutions & publishers, cloud flows, forms, views, model-driven apps, connection references, plug-in registration, plug-in tracing & statistics) with a per-row `Gate` column (`default` / `write` / `delete`), and the 29 plug-in registration tools that were missing from the previous list were added.
- Revised tool docstrings and `Field(description=...)` text across the tool corpus to follow a consistent style guide: verb-first first line, disambiguating when-to-use context, unified "Publishes automatically — no separate publish needed" wording, explicit `Requires DATAVERSE_ALLOW_WRITE/DELETE=true` gates, and prerequisite-chain guidance for plug-in registration tools. Stripped the `Required:/Optional:` schema-echo blocks from `plugin_registration.py` (~1,500 tokens) and moved enum meanings (stage 10/20/40, mode 0/1) into `Field(description=...)` in `models.py`.

## [2.2.0] - 2026-06-12

### Security
- Added the `DATAVERSE_WHITELIST` environment variable to restrict tool calls to an explicit list of allowed environment hostnames;
- User-supplied values such as column names are now escaped in all OData filters

### Changed
- Throttled and transiently failing requests are now retried automatically
- Responses larger than 5 MB are replaced with an error suggesting `select`/`top`/`filter` narrowing
- Error messages are consistent across all tools, capped in length, and include the Dataverse error code
- Large queries page more efficiently and tolerate slower responses; concurrent calls no longer repeat authentication

### Fixed
- `dataverse_list_apps` and `dataverse_get_app` failed with HTTP 400 on every call
- `dataverse_get_app` always returned an empty component list
- `dataverse_set_app_sitemap` could never create a sitemap
- `dataverse_create_multi_table_lookup` failed with HTTP 400 on every call

## [2.1.0] - 2026-06-02

### Added
- `dataverse_list_connection_references` tool to list connection references with optional filters for connector ID, status, and a raw OData expression
- `dataverse_get_connection_reference` tool to retrieve a single connection reference by GUID or logical name
- `dataverse_create_connection_reference` write tool (requires `DATAVERSE_ALLOW_WRITE=true`) to create a connection reference with optional immediate connection assignment and optional solution association via `MSCRM.SolutionUniqueName` header
- `dataverse_update_connection_reference` write tool (requires `DATAVERSE_ALLOW_WRITE=true`) to assign or clear a connection, or update display name and description; supports `solution_unique_name` to associate the reference with a solution via `MSCRM.SolutionUniqueName` header
- `dataverse_delete_connection_reference` delete tool (requires `DATAVERSE_ALLOW_DELETE=true`) to delete an unmanaged connection reference
- `dataverse_set_formxml` write tool (requires `DATAVERSE_ALLOW_WRITE=true`) to replace a form's FormXml directly and publish; returns `formxml_backup` for revert; validates XML before PATCHing
- `dataverse_validate_formxml` now accepts an optional `formxml` parameter for dry-run validation of a proposed XML string without fetching from Dataverse
- `dataverse_get_plugin_trace_log_setting` tool to read the current organization-wide plug-in trace log verbosity (off / exception / all)
- `dataverse_set_plugin_trace_log_setting` write tool (requires `DATAVERSE_ALLOW_WRITE=true`) to enable or disable plug-in trace logging; accepts `'off'`, `'exception'`, or `'all'`
- `dataverse_list_plugin_trace_logs` tool to query `plugintracelog` records with filters for plug-in class name (partial match), triggering message, primary entity, operation type, exceptions-only, and a rolling time window (`hours_ago`)
- `dataverse_list_plugin_type_statistics` tool to query runtime performance statistics for Dataverse plug-in types; returns execution count, failure rate, crash metrics, and worker-process termination contribution percentages with optional expansion to plug-in type name and assembly
- Form tools: `dataverse_list_forms`, `dataverse_get_form` (returns structured tabs → sections → controls tree), and `dataverse_validate_formxml` for read-only form inspection
- Form write tools (require `DATAVERSE_ALLOW_WRITE=true`): `dataverse_add_form_control` (auto-resolves classid from column metadata) and `dataverse_remove_form_control`
- View tools: `dataverse_list_views`, `dataverse_get_view` (returns FetchXml, LayoutXml, and column list), and `dataverse_validate_view` for read-only view inspection
- View write tools (require `DATAVERSE_ALLOW_WRITE=true`): `dataverse_create_view`, `dataverse_update_view`, `dataverse_add_view_column`, and `dataverse_remove_view_column`
- Model-driven app tools: `dataverse_list_apps`, `dataverse_get_app` (returns grouped component list via RetrieveAppComponents), and `dataverse_validate_app`
- App write tools (require `DATAVERSE_ALLOW_WRITE=true`): `dataverse_create_app` (auto-generates sitemap and adds entity components), `dataverse_update_app`, `dataverse_add_app_components`, `dataverse_remove_app_components`, `dataverse_set_app_sitemap`, `dataverse_publish_app`, and `dataverse_assign_app_role`

## [2.0.0] - 2026-05-18

### Added
- Solution and publisher lifecycle tools, including create/update operations and solution component add/remove actions
- Cloud flow management tools for querying, single-flow enable/disable, and batch state updates with per-item results
- New query capabilities: `dataverse_count_records`, `dataverse_aggregate_table`, and optional formatted values in record/query responses

### Changed
- Official stable release of the feature set previously published through the `2.0.0b*` prereleases
- Read/write/delete guardrail model consolidated around server environment flags (`DATAVERSE_ALLOW_WRITE`, `DATAVERSE_ALLOW_DELETE`)
- Dataverse HTTP layer and auth flow optimized for async execution and in-process token caching

### Fixed
- Batch request/response handling reliability, including multipart parsing and required inner-request headers
- OData compatibility issues across filtering, count behavior, and relationship eligibility checks
- Azure CLI token acquisition reliability when the CLI path is missing in non-login shell launches

### Removed
- **BREAKING:** `powerplatform-dataverse-client` dependency in favor of direct Dataverse Web API usage
- **BREAKING:** Per-call write/delete preview and allow flags from tool inputs

## [2.0.0b3] - 2026-05-18

### Added
- `dataverse_create_publisher` tool to create publishers with `uniquename`, `friendlyname`, `customizationprefix`, and `customizationoptionvalueprefix`
- `dataverse_update_publisher` tool to update mutable publisher properties by `publisher_id`
- `dataverse_create_solution` tool to create solutions with unique name, display name, version, and publisher binding
- `dataverse_update_solution` tool to update mutable solution properties by `solution_id` or `solution_unique_name`
- `dataverse_update_solution_version` tool to update only a solution's version by `solution_id` or `solution_unique_name`
- `dataverse_add_component_to_solution` tool to add components using the Dataverse `AddSolutionComponent` action
- `dataverse_remove_component_from_solution` tool to remove components using the Dataverse `RemoveSolutionComponent` action
- `dataverse_get_cloud_flows` tool to retrieve cloud flows by OData query and optionally scope by solution ID or solution unique name
- `dataverse_enable_cloud_flow` and `dataverse_disable_cloud_flow` tools to toggle a single flow state with PATCH and SetState fallback behavior
- `dataverse_batch_enable_cloud_flows` and `dataverse_batch_disable_cloud_flows` tools to toggle flow states at scale using Dataverse `$batch` with per-item result reporting

### Changed
- `dataverse_remove_component_from_solution` now requires `DATAVERSE_ALLOW_DELETE=true` (delete-gated) instead of write gating

## [2.0.0b2] - 2026-05-15

### Fixed
- `dataverse_query_table` now uses Dataverse-compatible filtered count behavior (`?$filter=...&$count=true&$top=1`) when `count=true` and `filter` is provided, instead of calling `/$count` with `$filter`
- `build_headers` now checks the in-process token cache on the event loop first and only uses `asyncio.to_thread` for token acquisition on cache miss
- `dataverse_check_relationship_eligibility` no longer returns HTTP 404 — removed erroneous trailing `()` from unbound action URL

### Changed
- `dataverse_query_table` and `dataverse_get_record` now apply a conservative default `$select` projection (`createdon,modifiedon`) when `select` is omitted, preventing full-row payload expansion by default
- Clarified docs to consistently describe `dataverse_get_record` and `dataverse_query_table` as taking `entity_set_name`
- `dataverse_check_relationship_eligibility` tool description now explicitly states it should only be called before relationship creation tools, preventing agents from invoking it unnecessarily on unrelated prompts
- `dataverse_create_one_to_many_relationship` and `dataverse_create_many_to_many_relationship` docstrings changed from mandatory pre-check instruction to optional suggestion

## [2.0.0b1] - 2026-05-12

### Added
- Added `consistency_strong` option to metadata read tools
- Added `solution_unique_name` option to metadata write tools
- Added `dataverse_count_records` tool
- Added `dataverse_aggregate_table` tool
- Added `count` option to `dataverse_query_table`
- Added `include_formatted_values` option to `dataverse_query_table` and `dataverse_get_record`

### Fixed
- Fixed `dataverse_execute_batch` `KeyError` from stale `If-None-Match` deletion
- Fixed `dataverse_check_relationship_eligibility` action URL format (`ActionName()`)
- Fixed `dataverse_check_relationship_eligibility` to use POST (not GET)
- Fixed `dataverse_check_relationship_eligibility` result parsing for `{"Value": true}` payloads
- Fixed `dataverse_get_column` filter escaping for `column_logical_name`
- Fixed OData query option encoding (`$select`, `$filter`, `$top`)
- Fixed `dataverse_list_tables` metadata pagination truncation
- Fixed default `If-None-Match: null` header usage

### Changed
- Changed `.github/copilot-instructions.md` to a concise repo-specific ruleset
- Changed HTTP layer to shared async `httpx.AsyncClient`
- Changed `build_headers` and `paginate_records` to native async
- Changed auth flow to use in-process bearer token cache
- Changed `normalize_dataverse_url` to use `@functools.lru_cache`
- Changed relationship and choice-option metadata fetches to run in parallel
- Changed and removed duplicate `_DATAVERSE_API_VERSION` in `environments.py`
- Changed **BREAKING** `dataverse_query_table` and `dataverse_get_record` to use `entity_set_name` instead of `table_name`
- Changed read tools to direct Dataverse Web API via `httpx` (removed SDK dependency)
- Changed base URL resolution behavior to avoid invalid fallback values

### Removed
- Removed **BREAKING** `powerplatform-dataverse-client` dependency

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

[Unreleased]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v3.2.0...HEAD
[3.2.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v2.2.0...v3.0.0
[2.2.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v2.0.0b3...v2.0.0
[2.0.0b3]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v2.0.0b2...v2.0.0b3
[2.0.0b2]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v2.0.0b1...v2.0.0b2
[2.0.0b1]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.3.2...v2.0.0b1
[1.3.2]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0b2...v0.1.0
[0.1.0b2]: https://github.com/ryanmichaeljames/dataverse-mcp/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/ryanmichaeljames/dataverse-mcp/releases/tag/v0.1.0b1
