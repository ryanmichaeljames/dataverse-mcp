"""Table and column metadata tools for the Dataverse MCP server."""

import asyncio
import json
import logging
from urllib.parse import quote as _url_quote, urlencode
from xml.etree import ElementTree as ET

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import delete_tool, mcp, write_tool
from dataverse_mcp.client import (
    AppContext,
    _DATAVERSE_API_VERSION,
    build_headers,
    extract_error_message,
    odata_quote,
    paginate_records,
    resolve_base_url,
)
from dataverse_mcp.models import (
    AddChoiceOptionInput,
    CheckRelationshipEligibilityInput,
    CreateChoiceInput,
    CreateColumnInput,
    CreateManyToManyRelationshipInput,
    CreateMultiTableLookupInput,
    CreateOneToManyRelationshipInput,
    CreateTableInput,
    DeleteChoiceInput,
    DeleteChoiceOptionInput,
    DeleteColumnInput,
    DeleteRelationshipInput,
    DeleteTableInput,
    GetChoiceInput,
    GetColumnInput,
    GetRelationshipInput,
    GetTableMetadataInput,
    ListChoiceColumnOptionsInput,
    ListChoicesInput,
    ListColumnsInput,
    ListRelationshipsInput,
    ListTablesInput,
    PublishCustomizationsInput,
    ReorderChoiceOptionsInput,
    UpdateChoiceInput,
    UpdateChoiceOptionInput,
    UpdateColumnInput,
    UpdateRelationshipInput,
    UpdateTableInput,
)

logger = logging.getLogger(__name__)

_DEFAULT_TABLE_SELECT = [
    "LogicalName",
    "SchemaName",
    "DisplayName",
    "EntitySetName",
    "IsCustomEntity",
    "IsManaged",
]

_DEFAULT_COLUMN_SELECT = [
    "LogicalName",
    "SchemaName",
    "AttributeType",
    "DisplayName",
    "RequiredLevel",
    "IsValidForRead",
    "IsValidForCreate",
    "IsValidForUpdate",
]

_CREATE_COLUMN_RESERVED_KEYS = {"@odata.type", "SchemaName", "DisplayName", "RequiredLevel"}


def _build_extra_headers(
    *,
    solution_unique_name: str | None = None,
    consistency_strong: bool = False,
) -> dict[str, str] | None:
    """Build optional extra headers for metadata API requests."""
    extra: dict[str, str] = {}
    if solution_unique_name:
        extra["MSCRM.SolutionUniqueName"] = solution_unique_name
    if consistency_strong:
        extra["Consistency"] = "Strong"
    return extra or None


def _get_app_ctx(ctx: Context) -> AppContext:
    """Return the application context from the request context."""
    return ctx.request_context.lifespan_context


def _to_bool(value: object) -> bool:
    """Coerce bool-ish Dataverse values.

    - bool values are returned as-is
    - numeric values are true when non-zero
    - strings are true for 'true', '1', or 'yes' (case-insensitive)
    - all other values are false
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _extract_action_bool(result: dict, result_key: str) -> bool:
    """Extract and coerce Dataverse action results that may be wrapped."""
    raw = result.get(result_key, result.get("value", result))
    if isinstance(raw, dict):
        raw = raw.get("Value", raw.get("value", raw))
    return _to_bool(raw)


@mcp.tool(
    name="dataverse_list_tables",
    annotations={
        "title": "List Tables",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_tables(params: ListTablesInput, ctx: Context) -> str:
    """List available tables (entities) in the Dataverse environment.
    Returns table metadata including logical name, schema name, and display
    name. By default returns all non-private tables. Use filter to narrow
    results (e.g., "IsCustomEntity eq true" for custom tables only).

    Use this tool to discover which tables exist before querying them with
    dataverse_query_table or inspecting their schema with
    dataverse_get_table_metadata.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_TABLE_SELECT
    query_params: dict[str, str] = {"$select": ",".join(select)}
    if params.filter:
        query_params["$filter"] = params.filter

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/EntityDefinitions?{urlencode(query_params, safe='$,')}"

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        tables = await paginate_records(url, headers, None, app_ctx.http_client)
        return json.dumps({
            "tables": tables,
            "count": len(tables),
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_tables")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_table_metadata",
    annotations={
        "title": "Get Table Metadata",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_table_metadata(
    params: GetTableMetadataInput, ctx: Context
) -> str:
    """Get detailed metadata for a specific Dataverse table.
    Returns the table's schema name, logical name, entity set name,
    primary key attribute, and primary name attribute. Use this to
    understand a table's structure before querying it with
    dataverse_query_table.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = _url_quote(params.table_name, safe="")
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
        f"EntityDefinitions(LogicalName='{table_enc}')"
        f"?$select=LogicalName,SchemaName,EntitySetName,MetadataId,"
        f"PrimaryIdAttribute,PrimaryNameAttribute"
    )

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        resp = await app_ctx.http_client.get(url, headers=headers)
        resp.raise_for_status()
        raw = resp.json()
        table_info = {
            "logical_name": raw.get("LogicalName"),
            "schema_name": raw.get("SchemaName"),
            "entity_set_name": raw.get("EntitySetName"),
            "metadata_id": raw.get("MetadataId"),
            "primary_id_attribute": raw.get("PrimaryIdAttribute"),
            "primary_name_attribute": raw.get("PrimaryNameAttribute"),
        }
        return json.dumps({"table": table_info})
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_table_metadata")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_list_columns",
    annotations={
        "title": "List Columns",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_columns(params: ListColumnsInput, ctx: Context) -> str:
    """List all column (attribute) definitions for a Dataverse table.
    Returns metadata for every column on the specified table. Use
    attribute_type to narrow by type (e.g., 'Lookup', 'Picklist').
    Use select to choose which metadata properties to include.

    Use this before querying records to discover available columns and
    their types. For full metadata on a single column, use
    dataverse_get_column. For choice options, use
    dataverse_list_choice_column_options.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = _url_quote(params.table_logical_name, safe="")
    select = params.select or _DEFAULT_COLUMN_SELECT
    query_params: dict[str, str] = {"$select": ",".join(select)}
    if params.attribute_type:
        query_params["$filter"] = f"AttributeType eq '{odata_quote(params.attribute_type)}'"

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
        f"EntityDefinitions(LogicalName='{table_enc}')/Attributes"
        f"?{urlencode(query_params, safe='$,')}"
    )

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        columns = await paginate_records(url, headers, 5000, app_ctx.http_client)
        return json.dumps({
            "columns": columns,
            "count": len(columns),
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_columns")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_column",
    annotations={
        "title": "Get Column",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_column(params: GetColumnInput, ctx: Context) -> str:
    """Get full metadata for a single column on a Dataverse table.
    Returns all metadata properties for the specified column, including
    type-specific properties such as MaxLength (String), Precision
    (Decimal/Money), RequiredLevel, Format, and IsValidForCreate.

    Use dataverse_list_columns first to discover available column names.
    For Picklist/MultiSelectPicklist option values, use
    dataverse_list_choice_column_options.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = _url_quote(params.table_logical_name, safe="")
    col_filter = (
        "LogicalName eq "
        f"'{odata_quote(params.column_logical_name)}'"
    )
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
        f"EntityDefinitions(LogicalName='{table_enc}')/Attributes"
        f"?{urlencode({'$filter': col_filter}, safe='$,')}"
    )

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        columns = await paginate_records(url, headers, 1, app_ctx.http_client)
        if not columns:
            return json.dumps({
                "error": True,
                "message": (
                    f"Column '{params.column_logical_name}' not found on table "
                    f"'{params.table_logical_name}'."
                ),
            })
        column = columns[0]
        column.pop("@odata.context", None)
        return json.dumps({"column": column})
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_column")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


def _extract_options(items: list) -> list[dict]:
    """Extract integer Value + Label text from raw OptionSet option list."""
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        option_set = item.get("OptionSet") or {}
        options = option_set.get("Options") if isinstance(option_set, dict) else []
        if not isinstance(options, list):
            continue
        for opt in options:
            if not isinstance(opt, dict):
                continue
            value = opt.get("Value")
            label_def = opt.get("Label") or {}
            localized = label_def.get("LocalizedLabels") or []
            label_text = None
            for loc in localized:
                if isinstance(loc, dict) and loc.get("Label"):
                    label_text = loc["Label"]
                    break
            results.append({"value": value, "label": label_text})
    return results


async def _fetch_picklist_options(
    base_url: str,
    headers: dict,
    table: str,
    column: str,
    cast: str,
    http_client: httpx.AsyncClient,
) -> list[dict]:
    """Fetch option values for a Picklist or MultiSelectPicklist column."""
    table_enc = _url_quote(table, safe="")
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
        f"EntityDefinitions(LogicalName='{table_enc}')"
        f"/Attributes/{cast}"
    )
    params = {
        "$select": "LogicalName",
        "$expand": "OptionSet($select=Options)",
        "$filter": f"LogicalName eq '{odata_quote(column)}'",
    }

    response = await http_client.get(url, params=params, headers=headers)
    response.raise_for_status()
    payload = response.json()
    return _extract_options(payload.get("value", []))


@mcp.tool(
    name="dataverse_list_choice_column_options",
    annotations={
        "title": "List Choice Column Options",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_choice_column_options(
    params: ListChoiceColumnOptionsInput, ctx: Context
) -> str:
    """Get all option values for a Picklist or MultiSelectPicklist column.
    Returns the integer value and display label for each option in the
    column's local option set. Handles both Picklist and
    MultiSelectPicklist column types automatically.

    Use this before filtering or writing records that contain choice
    columns — the integer value is required for OData filter expressions
    (e.g., "statuscode eq 1"). The label helps identify the correct value.

    Only works for columns with a local (non-global) option set. Global
    option sets shared across tables are not currently supported.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )

        picklist_opts, multi_opts = await asyncio.gather(
            _fetch_picklist_options(
                base_url,
                headers,
                params.table_logical_name,
                params.column_logical_name,
                "Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
                app_ctx.http_client,
            ),
            _fetch_picklist_options(
                base_url,
                headers,
                params.table_logical_name,
                params.column_logical_name,
                "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata",
                app_ctx.http_client,
            ),
        )
        options = picklist_opts or multi_opts

        if not options:
            return json.dumps({
                "error": True,
                "message": (
                    f"Column '{params.column_logical_name}' on table "
                    f"'{params.table_logical_name}' was not found or is not a "
                    "Picklist/MultiSelectPicklist column."
                ),
            })

        return json.dumps({
            "options": options,
            "count": len(options),
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_choice_column_options")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


# ---------------------------------------------------------------------------
# Relationship metadata tools
# ---------------------------------------------------------------------------

_ONE_TO_MANY_SELECT = ",".join([
    "SchemaName",
    "RelationshipType",
    "ReferencedEntity",
    "ReferencedAttribute",
    "ReferencingEntity",
    "ReferencingAttribute",
    "ReferencingEntityNavigationPropertyName",
    "ReferencedEntityNavigationPropertyName",
])

_MANY_TO_MANY_SELECT = ",".join([
    "SchemaName",
    "RelationshipType",
    "Entity1LogicalName",
    "Entity2LogicalName",
    "Entity1IntersectAttribute",
    "Entity2IntersectAttribute",
    "IntersectEntityName",
])

_REL_TYPE_SELECT = {
    "OneToMany": _ONE_TO_MANY_SELECT,
    "ManyToOne": _ONE_TO_MANY_SELECT,
    "ManyToMany": _MANY_TO_MANY_SELECT,
}


async def _fetch_relationships_httpx(
    base_url: str,
    headers: dict,
    url_path: str,
    top: int,
    http_client: httpx.AsyncClient,
    select: str | None = None,
) -> list[dict]:
    """Fetch relationship definitions from the Dataverse metadata API via httpx."""
    params: dict[str, str | int] = {}
    if select:
        params["$select"] = select
    params["$top"] = top

    response = await http_client.get(
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{url_path}",
        params=params,
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("value", [])


@mcp.tool(
    name="dataverse_list_relationships",
    annotations={
        "title": "List Relationships",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_relationships(
    params: ListRelationshipsInput, ctx: Context
) -> str:
    """List relationship definitions for a table or the whole environment.
    When table_logical_name is supplied, queries that table's OneToMany,
    ManyToOne, and/or ManyToMany relationships depending on relationship_type.
    When table_logical_name is omitted, returns all RelationshipDefinitions
    in the environment (relationship_type is ignored in this case).

    Use the returned SchemaName to call dataverse_get_relationship for full
    cascade and navigation property details. Use
    ReferencingEntityNavigationPropertyName /
    ReferencedEntityNavigationPropertyName in OData $expand queries.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )

        if params.table_logical_name:
            table_enc = _url_quote(params.table_logical_name, safe="")
            base_entity = f"EntityDefinitions(LogicalName='{table_enc}')"
            types_to_fetch = (
                [params.relationship_type]
                if params.relationship_type
                else ["OneToMany", "ManyToOne", "ManyToMany"]
            )
            results = await asyncio.gather(*[
                _fetch_relationships_httpx(
                    base_url,
                    headers,
                    f"{base_entity}/{rel_type}Relationships",
                    params.top,
                    app_ctx.http_client,
                    select=_REL_TYPE_SELECT[rel_type],
                )
                for rel_type in types_to_fetch
            ])
            all_rels: list[dict] = [r for sublist in results for r in sublist]
        else:
            # Polymorphic collection — omit $select to avoid type-specific field errors
            all_rels = await _fetch_relationships_httpx(
                base_url,
                headers,
                "RelationshipDefinitions",
                params.top,
                app_ctx.http_client,
            )

        return json.dumps({
            "relationships": all_rels,
            "count": len(all_rels),
            "has_more": len(all_rels) >= params.top,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse metadata API error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_relationships")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_relationship",
    annotations={
        "title": "Get Relationship",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_relationship(
    params: GetRelationshipInput, ctx: Context
) -> str:
    """Get full metadata for a single relationship by schema name.
    Returns cascade configuration, navigation property names, and all
    structural details for the relationship. Schema names are case-sensitive
    and must exactly match the SchemaName returned by
    dataverse_list_relationships (e.g., 'account_contacts').

    Use dataverse_list_relationships first to discover the correct
    SchemaName. The navigation property names are required for OData
    $expand queries.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    schema_enc = _url_quote(params.schema_name, safe="")

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        response = await app_ctx.http_client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"RelationshipDefinitions(SchemaName='{schema_enc}')",
            headers=headers,
        )
        response.raise_for_status()
        relationship = response.json()
        relationship.pop("@odata.context", None)
        return json.dumps({"relationship": relationship})
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse metadata API error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_relationship")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


# ---------------------------------------------------------------------------
# Choice (global option set) metadata tools
# ---------------------------------------------------------------------------

_DEFAULT_CHOICE_SELECT = ",".join([
    "MetadataId",
    "Name",
    "DisplayName",
    "OptionSetType",
    "IsGlobal",
    "IsManaged",
])


@mcp.tool(
    name="dataverse_list_choices",
    annotations={
        "title": "List Global Choices",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_choices(params: ListChoicesInput, ctx: Context) -> str:
    """List global choice (option set) definitions in the Dataverse environment.
    Returns metadata for all global choices. The Dataverse API does not
    support $filter or $top on this endpoint; top is applied client-side.
    Option values and labels are not included — use dataverse_get_choice
    to retrieve full option details for a specific choice by name or MetadataId.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    # Strip 'Options' — not selectable on the polymorphic collection type
    raw_select = [f for f in (params.select or [])] if params.select else None
    select = ",".join(raw_select) if raw_select else _DEFAULT_CHOICE_SELECT

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        query_params: dict[str, str] = {"$select": select}
        response = await app_ctx.http_client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/GlobalOptionSetDefinitions",
            params=query_params,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        all_choices = payload.get("value", [])
        # API ignores $top on this endpoint — slice client-side
        top = params.top or 50
        choices = all_choices[:top]
        has_more = len(all_choices) > top or "@odata.nextLink" in payload
        return json.dumps({
            "choices": choices,
            "count": len(choices),
            "has_more": has_more,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse metadata API error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_choices")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_choice",
    annotations={
        "title": "Get Global Choice",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_choice(params: GetChoiceInput, ctx: Context) -> str:
    """Get a specific global choice (option set) definition by name or MetadataId.
    Returns all option values, integer codes, and labels for the choice.
    Use this when you need the full option set for a global choice before
    filtering records or building picklist column definitions.

    Provide either name (logical name, e.g., 'incident_prioritycode') or
    metadata_id (GUID). If both are provided, name takes precedence.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})
    if params.name:
        name_enc = _url_quote(params.name, safe="")
        path = f"GlobalOptionSetDefinitions(Name='{name_enc}')"
    else:
        path = f"GlobalOptionSetDefinitions({params.metadata_id})"

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        response = await app_ctx.http_client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{path}",
            headers=headers,
        )
        response.raise_for_status()
        choice = response.json()
        choice.pop("@odata.context", None)
        return json.dumps({"choice": choice})
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse metadata API error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_choice")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_check_relationship_eligibility",
    annotations={
        "title": "Check Relationship Eligibility",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_check_relationship_eligibility(
    params: CheckRelationshipEligibilityInput, ctx: Context
) -> str:
    """Pre-validate whether a table supports a specific relationship role before
    attempting to create a relationship. Only call this immediately before
    dataverse_create_one_to_many_relationship or
    dataverse_create_many_to_many_relationship — do not call it for general
    queries, data reads, or any other purpose.

    check_type options:
    - 'referenced'    — can be the primary (one) side of a 1:N
    - 'referencing'   — can be the related (many) side of a 1:N
    - 'many_to_many'  — can participate in an N:N relationship

    Returns eligible (bool) for the requested check_type.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})
    check_type_to_action = {
        "referenced": ("CanBeReferenced", "CanBeReferenced"),
        "referencing": ("CanBeReferencing", "CanBeReferencing"),
        "many_to_many": ("CanManyToMany", "CanManyToMany"),
    }
    action_name, result_key = check_type_to_action[params.check_type]

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{action_name}",
            json={"EntityName": params.table_logical_name},
            headers=headers,
        )
        response.raise_for_status()
        result = response.json()
        eligible = _extract_action_bool(result, result_key)
        return json.dumps({
            "table_logical_name": params.table_logical_name,
            "check_type": params.check_type,
            "eligible": eligible,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse eligibility API error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_check_relationship_eligibility")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


# ---------------------------------------------------------------------------
# Table schema write tools
# ---------------------------------------------------------------------------


def _make_label(text: str | None, language_code: int = 1033) -> dict:
    """Build a Dataverse Label object for use in entity/attribute definitions."""
    localized = []
    if text:
        localized = [{
            "@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
            "Label": text,
            "LanguageCode": language_code,
        }]
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.Label",
        "LocalizedLabels": localized,
    }


def _build_create_table_body(params: CreateTableInput) -> dict:
    """Build the entity definition body for POST /EntityDefinitions."""
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.EntityMetadata",
        "SchemaName": params.schema_name,
        "DisplayName": _make_label(params.display_name),
        "DisplayCollectionName": _make_label(params.display_collection_name),
        "Description": _make_label(params.description),
        "OwnershipType": params.ownership_type,
        "HasActivities": False,
        "HasNotes": False,
        "Attributes": [
            {
                "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
                "AttributeType": "String",
                "AttributeTypeName": {"Value": "StringType"},
                "SchemaName": params.primary_name_attribute_schema_name,
                "DisplayName": _make_label("Name"),
                "Description": _make_label(None),
                "IsPrimaryName": True,
                "RequiredLevel": {
                    "Value": "None",
                    "CanBeChanged": True,
                    "ManagedPropertyLogicalName": "canmodifyrequirementlevelsettings",
                },
                "MaxLength": 100,
                "FormatName": {"Value": "Text"},
            }
        ],
    }


@write_tool(
    name="dataverse_create_table",
    annotations={
        "title": "Create Table",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_table(params: CreateTableInput, ctx: Context) -> str:
    """Create a new custom table (entity) in the Dataverse environment.
    Builds and POSTs an EntityMetadata definition including the table schema,
    display names, ownership type, and a required primary name string attribute.

    The schema_name must include a publisher prefix (e.g., 'cr123_Widget').
    The table logical name will be the lowercase form of schema_name.
    """
    body = _build_create_table_body(params)

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/EntityDefinitions",
            json=body,
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        location = response.headers.get("OData-EntityId") or response.headers.get("location", "")
        logger.info("Created table %s — location: %s", params.schema_name, location)
        return json.dumps({
            "created": True,
            "schema_name": params.schema_name,
            "logical_name": params.schema_name.lower(),
            "location": location,
        })
    except httpx.TimeoutException as e:
        logger.warning("Timeout in dataverse_create_table — operation may have succeeded: %s", e)
        return json.dumps({
            "error": True,
            "created": None,
            "is_transient": True,
            "message": (
                "The request timed out before the server responded. Dataverse table "
                "creation can take several minutes. Use dataverse_list_tables or "
                "dataverse_get_table_metadata to verify whether the table was created."
            ),
            "schema_name": params.schema_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse create table error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_create_table")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_update_table",
    annotations={
        "title": "Update Table",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_table(params: UpdateTableInput, ctx: Context) -> str:
    """Update an existing table's display name or description.
    Fetches the current full entity definition, applies the requested changes,
    and PUTs the merged definition back. The Dataverse metadata API requires a
    full PUT — partial updates (PATCH) are not supported on EntityDefinitions.

    At least one of display_name or description must be provided.
    """
    if not params.display_name and params.description is None:
        return json.dumps({
            "error": True,
            "message": "Provide at least one of display_name or description to update.",
        })

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    table_enc = _url_quote(params.table_logical_name, safe="")

    try:
        headers = await build_headers(app_ctx, base_url)
        get_resp = await app_ctx.http_client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')",
            headers=headers,
        )
        get_resp.raise_for_status()
        definition = get_resp.json()
        definition.pop("@odata.context", None)

        if params.display_name:
            definition["DisplayName"] = _make_label(params.display_name)
        if params.description is not None:
            definition["Description"] = _make_label(params.description)

        metadata_id = definition.get("MetadataId")
        if not metadata_id:
            logger.error("MetadataId missing from table definition for %s", params.table_logical_name)
            return json.dumps({
                "error": True,
                "message": (
                    f"MetadataId not found in table definition for '{params.table_logical_name}'. "
                    "Cannot construct update URL without MetadataId."
                ),
            })

        headers_ct = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.put(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions({metadata_id})",
            json=definition,
            headers=headers_ct,
        )
        response.raise_for_status()
        logger.info("Updated table %s", params.table_logical_name)
        return json.dumps({
            "updated": True,
            "table_logical_name": params.table_logical_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse update table error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_update_table")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@delete_tool(
    name="dataverse_delete_table",
    annotations={
        "title": "Delete Table",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_table(params: DeleteTableInput, ctx: Context) -> str:
    """Permanently delete a custom table and all its data from the environment.
    WARNING: This operation is irreversible. All records in the table will be
    permanently deleted along with the table definition.

    Only custom tables (IsCustomEntity=true) can be deleted. Attempting to
    delete a system table will return an error from the API.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    table_enc = _url_quote(params.table_logical_name, safe="")

    try:
        headers = await build_headers(app_ctx, base_url)
        get_resp = await app_ctx.http_client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')"
            f"?$select=LogicalName,SchemaName,DisplayName,IsCustomEntity,IsManaged",
            headers=headers,
        )
        get_resp.raise_for_status()
        definition = get_resp.json()
        definition.pop("@odata.context", None)

        # Safety check: only allow deletion of custom, unmanaged tables
        is_custom = definition.get("IsCustomEntity", False)
        is_managed = definition.get("IsManaged", False)
        if not is_custom or is_managed:
            logger.error(
                "Cannot delete table %s: IsCustomEntity=%s, IsManaged=%s",
                params.table_logical_name,
                is_custom,
                is_managed,
            )
            return json.dumps({
                "error": True,
                "message": (
                    f"Cannot delete table '{params.table_logical_name}': "
                    f"only custom, unmanaged tables can be deleted "
                    f"(IsCustomEntity={is_custom}, IsManaged={is_managed})."
                ),
            })

        response = await app_ctx.http_client.delete(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')",
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        logger.info("Deleted table %s", params.table_logical_name)
        return json.dumps({
            "deleted": True,
            "table_logical_name": params.table_logical_name,
        })
    except httpx.TimeoutException as e:
        logger.warning("Timeout in dataverse_delete_table — operation may have succeeded: %s", e)
        return json.dumps({
            "error": True,
            "deleted": None,
            "is_transient": True,
            "message": (
                "The request timed out before the server responded. Dataverse table "
                "deletion can take several minutes. Use dataverse_list_tables or "
                "dataverse_get_table_metadata to verify whether the table was deleted."
            ),
            "table_logical_name": params.table_logical_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse delete table error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_delete_table")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


# ---------------------------------------------------------------------------
# Column schema write tools
# ---------------------------------------------------------------------------

_ATTRIBUTE_TYPE_ODATA_MAP = {
    "String": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
    "Integer": "Microsoft.Dynamics.CRM.IntegerAttributeMetadata",
    "Decimal": "Microsoft.Dynamics.CRM.DecimalAttributeMetadata",
    "DateTime": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
    "Boolean": "Microsoft.Dynamics.CRM.BooleanAttributeMetadata",
    "Lookup": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
    "Picklist": "Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
    "MultiSelectPicklist": "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata",
}

@write_tool(
    name="dataverse_create_column",
    annotations={
        "title": "Create Column",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_column(params: CreateColumnInput, ctx: Context) -> str:
    """Add a new column (attribute) to a Dataverse table.
    Constructs the correct attribute metadata body based on attribute_type and
    POSTs it to /EntityDefinitions(LogicalName='{table}')/Attributes.

    Use type_specific_properties to supply type-specific fields such as:
    - String: {"MaxLength": 100}
    - Integer: {"MinValue": 0, "MaxValue": 100000}
    - Decimal: {"Precision": 2}
    - DateTime: {"Format": "DateOnly"}  (DateOnly | DateAndTime)
    - Picklist/MultiSelectPicklist: {"OptionSet": {...}}

    Call dataverse_publish_customizations after creating columns to make the
    changes visible in the application.
    """
    if params.type_specific_properties:
        conflicting_keys = sorted(
            _CREATE_COLUMN_RESERVED_KEYS.intersection(params.type_specific_properties)
        )
        if conflicting_keys:
            return json.dumps({
                "error": True,
                "message": (
                    "type_specific_properties contains reserved keys that are managed by "
                    f"this tool: {', '.join(conflicting_keys)}."
                ),
            })

    odata_type = _ATTRIBUTE_TYPE_ODATA_MAP[params.attribute_type]
    body: dict = {
        "@odata.type": odata_type,
        "SchemaName": params.schema_name,
        "DisplayName": _make_label(params.display_name),
    }
    if params.required_level is not None:
        body["RequiredLevel"] = {
            "@odata.type": "Microsoft.Dynamics.CRM.AttributeRequiredLevelManagedProperty",
            "Value": params.required_level,
            "CanBeChanged": True,
        }
    if params.type_specific_properties:
        body.update(params.type_specific_properties)

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    table_enc = _url_quote(params.table_logical_name, safe="")

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')/Attributes",
            json=body,
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        entity_id = response.headers.get("OData-EntityId", "")
        logger.info(
            "Created column %s on table %s", params.schema_name, params.table_logical_name
        )
        return json.dumps({
            "created": True,
            "table_logical_name": params.table_logical_name,
            "schema_name": params.schema_name,
            "entity_id": entity_id,
        })
    except httpx.TimeoutException as e:
        logger.warning("Timeout in dataverse_create_column — operation may have succeeded: %s", e)
        return json.dumps({
            "error": True,
            "created": None,
            "is_transient": True,
            "message": (
                "The request timed out before the server responded. The column may "
                "have been created. Use dataverse_list_columns to verify."
            ),
            "table_logical_name": params.table_logical_name,
            "schema_name": params.schema_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse create column error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_create_column")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_update_column",
    annotations={
        "title": "Update Column",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_column(params: UpdateColumnInput, ctx: Context) -> str:
    """Update an existing column's metadata via full PUT replacement.
    The Dataverse metadata API does not support partial updates (PATCH) on
    attribute definitions. You must provide the complete column definition JSON.

    Call dataverse_publish_customizations after updating columns.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    table_enc = _url_quote(params.table_logical_name, safe="")
    column_enc = _url_quote(params.column_logical_name, safe="")

    try:
        definition = dict(params.full_definition)
        definition.pop("@odata.context", None)
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.put(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')"
            f"/Attributes(LogicalName='{column_enc}')",
            json=definition,
            headers=headers,
        )
        response.raise_for_status()
        logger.info(
            "Updated column %s on table %s",
            params.column_logical_name,
            params.table_logical_name,
        )
        return json.dumps({
            "updated": True,
            "table_logical_name": params.table_logical_name,
            "column_logical_name": params.column_logical_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse update column error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_update_column")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@delete_tool(
    name="dataverse_delete_column",
    annotations={
        "title": "Delete Column",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_column(params: DeleteColumnInput, ctx: Context) -> str:
    """Permanently delete a custom column and all its data from a table.
    WARNING: Deletion is permanent and irreversible. All data stored in this
    column across every record will be lost.

    Call dataverse_publish_customizations after deleting columns.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    table_enc = _url_quote(params.table_logical_name, safe="")
    column_enc = _url_quote(params.column_logical_name, safe="")

    try:
        headers = await build_headers(app_ctx, base_url)
        get_resp = await app_ctx.http_client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')"
            f"/Attributes(LogicalName='{column_enc}')"
            f"?$select=LogicalName,SchemaName,AttributeType,DisplayName,"
            f"IsCustomAttribute,IsManaged",
            headers=headers,
        )
        get_resp.raise_for_status()
        column_def = get_resp.json()
        column_def.pop("@odata.context", None)

        # Safety check: only allow deletion of custom, unmanaged columns
        is_custom = column_def.get("IsCustomAttribute", False)
        is_managed = column_def.get("IsManaged", False)
        if not is_custom or is_managed:
            logger.error(
                "Cannot delete column %s on table %s: IsCustomAttribute=%s, IsManaged=%s",
                params.column_logical_name,
                params.table_logical_name,
                is_custom,
                is_managed,
            )
            return json.dumps({
                "error": True,
                "message": (
                    f"Cannot delete column '{params.column_logical_name}' on table "
                    f"'{params.table_logical_name}': only custom, unmanaged columns "
                    f"can be deleted (IsCustomAttribute={is_custom}, IsManaged={is_managed})."
                ),
            })

        response = await app_ctx.http_client.delete(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')"
            f"/Attributes(LogicalName='{column_enc}')",
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        logger.info(
            "Deleted column %s from table %s",
            params.column_logical_name,
            params.table_logical_name,
        )
        return json.dumps({
            "deleted": True,
            "table_logical_name": params.table_logical_name,
            "column_logical_name": params.column_logical_name,
        })
    except httpx.TimeoutException as e:
        logger.warning("Timeout in dataverse_delete_column — operation may have succeeded: %s", e)
        return json.dumps({
            "error": True,
            "deleted": None,
            "is_transient": True,
            "message": (
                "The request timed out before the server responded. The column may "
                "have been deleted. Use dataverse_list_columns to verify."
            ),
            "table_logical_name": params.table_logical_name,
            "column_logical_name": params.column_logical_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse delete column error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_delete_column")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


# ---------------------------------------------------------------------------
# Relationship schema write tools
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_one_to_many_relationship",
    annotations={
        "title": "Create One-to-Many Relationship",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_one_to_many_relationship(
    params: CreateOneToManyRelationshipInput, ctx: Context
) -> str:
    """Create a 1:N relationship between two tables.
    This simultaneously creates the lookup column on the referencing (many) side.
    Optionally use dataverse_check_relationship_eligibility to pre-validate eligibility.

    Call dataverse_publish_customizations after creating relationships.
    """
    body = {
        "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
        "SchemaName": params.schema_name,
        "ReferencedEntity": params.referenced_entity,
        "ReferencingEntity": params.referencing_entity,
        "CascadeConfiguration": {
            "Assign": "NoCascade",
            "Delete": "RemoveLink",
            "Merge": "NoCascade",
            "Reparent": "NoCascade",
            "Share": "NoCascade",
            "Unshare": "NoCascade",
        },
        "Lookup": {
            "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
            "SchemaName": params.lookup_schema_name,
            "DisplayName": _make_label(params.lookup_display_name),
        },
    }

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/RelationshipDefinitions",
            json=body,
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        entity_id = response.headers.get("OData-EntityId", "")
        logger.info("Created 1:N relationship %s", params.schema_name)
        return json.dumps({
            "created": True,
            "schema_name": params.schema_name,
            "entity_id": entity_id,
        })
    except httpx.TimeoutException as e:
        logger.warning(
            "Timeout in dataverse_create_one_to_many_relationship — operation may have succeeded: %s",
            e,
        )
        return json.dumps({
            "error": True,
            "created": None,
            "is_transient": True,
            "message": (
                "The request timed out. The relationship may have been created. "
                "Use dataverse_get_relationship to verify."
            ),
            "schema_name": params.schema_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse create 1:N relationship error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_create_one_to_many_relationship")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_create_many_to_many_relationship",
    annotations={
        "title": "Create Many-to-Many Relationship",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_many_to_many_relationship(
    params: CreateManyToManyRelationshipInput, ctx: Context
) -> str:
    """Create an N:N relationship and its intersect (junction) table between two tables.
    Optionally use dataverse_check_relationship_eligibility to pre-validate eligibility.

    Call dataverse_publish_customizations after creating relationships.
    """
    body = {
        "@odata.type": "Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata",
        "SchemaName": params.schema_name,
        "Entity1LogicalName": params.entity1_logical_name,
        "Entity2LogicalName": params.entity2_logical_name,
        "IntersectEntityName": params.intersect_entity_name,
    }

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/RelationshipDefinitions",
            json=body,
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        entity_id = response.headers.get("OData-EntityId", "")
        logger.info("Created N:N relationship %s", params.schema_name)
        return json.dumps({
            "created": True,
            "schema_name": params.schema_name,
            "entity_id": entity_id,
        })
    except httpx.TimeoutException as e:
        logger.warning(
            "Timeout in dataverse_create_many_to_many_relationship — operation may have succeeded: %s",
            e,
        )
        return json.dumps({
            "error": True,
            "created": None,
            "is_transient": True,
            "message": (
                "The request timed out. The relationship may have been created. "
                "Use dataverse_get_relationship to verify."
            ),
            "schema_name": params.schema_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse create N:N relationship error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_create_many_to_many_relationship")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_create_multi_table_lookup",
    annotations={
        "title": "Create Multi-Table (Polymorphic) Lookup",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_multi_table_lookup(
    params: CreateMultiTableLookupInput, ctx: Context
) -> str:
    """Create a polymorphic lookup column that can reference multiple tables.
    Uses the CreatePolymorphicLookupAttribute bound action. The lookup column
    is added to owning_entity and can point to any of the target_entities.

    Call dataverse_publish_customizations after creating lookup columns.
    """
    relationships = [
        {
            "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
            "ReferencedEntity": target,
            "ReferencingEntity": params.owning_entity,
            "SchemaName": f"{params.lookup_schema_name}_{target}",
        }
        for target in params.target_entities
    ]
    lookup = {
        "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
        "SchemaName": params.lookup_schema_name,
        "DisplayName": _make_label(params.lookup_display_name),
    }
    body = {
        "OneToManyRelationships": relationships,
        "Lookup": lookup,
    }

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/CreatePolymorphicLookupAttribute",
            json=body,
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        result = response.json()
        logger.info("Created polymorphic lookup %s", params.lookup_schema_name)
        return json.dumps({
            "created": True,
            "lookup_schema_name": params.lookup_schema_name,
            "result": result,
        })
    except httpx.TimeoutException as e:
        logger.warning(
            "Timeout in dataverse_create_multi_table_lookup — operation may have succeeded: %s",
            e,
        )
        return json.dumps({
            "error": True,
            "created": None,
            "is_transient": True,
            "message": (
                "The request timed out. The polymorphic lookup may have been created. "
                "Use dataverse_list_columns to verify."
            ),
            "lookup_schema_name": params.lookup_schema_name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse create polymorphic lookup error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_create_multi_table_lookup")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_update_relationship",
    annotations={
        "title": "Update Relationship",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_relationship(
    params: UpdateRelationshipInput, ctx: Context
) -> str:
    """Update an existing relationship's cascade behavior or configuration.
    The Dataverse metadata API requires a full PUT — partial updates are not
    supported on RelationshipDefinitions.

    Call dataverse_publish_customizations after updating relationships.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        definition = dict(params.full_definition)
        definition.pop("@odata.context", None)
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.put(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/RelationshipDefinitions({params.metadata_id})",
            json=definition,
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Updated relationship %s", params.metadata_id)
        return json.dumps({
            "updated": True,
            "metadata_id": params.metadata_id,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse update relationship error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_update_relationship")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@delete_tool(
    name="dataverse_delete_relationship",
    annotations={
        "title": "Delete Relationship",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_relationship(
    params: DeleteRelationshipInput, ctx: Context
) -> str:
    """Delete a custom relationship by MetadataId.
    WARNING: Deletion is permanent. For 1:N relationships, the associated
    lookup column on the referencing entity is also deleted.

    Call dataverse_publish_customizations after deleting relationships.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        try:
            get_resp = await app_ctx.http_client.get(
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/RelationshipDefinitions({params.metadata_id})",
                headers=headers,
            )
            get_resp.raise_for_status()
            relationship_def = get_resp.json()
            relationship_def.pop("@odata.context", None)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": (
                        f"Relationship with MetadataId '{params.metadata_id}' was not found "
                        "or is not accessible."
                    ),
                })
            raise

        is_custom = relationship_def.get("IsCustomRelationship", False)
        is_managed = relationship_def.get("IsManaged", False)
        if not is_custom or is_managed:
            logger.error(
                "Cannot delete relationship %s: IsCustomRelationship=%s, IsManaged=%s",
                params.metadata_id,
                is_custom,
                is_managed,
            )
            return json.dumps({
                "error": True,
                "message": (
                    f"Cannot delete relationship '{params.metadata_id}': only custom, "
                    f"unmanaged relationships can be deleted "
                    f"(IsCustomRelationship={is_custom}, IsManaged={is_managed})."
                ),
            })

        response = await app_ctx.http_client.delete(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/RelationshipDefinitions({params.metadata_id})",
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        logger.info("Deleted relationship %s", params.metadata_id)
        return json.dumps({
            "deleted": True,
            "metadata_id": params.metadata_id,
        })
    except httpx.TimeoutException as e:
        logger.warning(
            "Timeout in dataverse_delete_relationship — operation may have succeeded: %s", e
        )
        return json.dumps({
            "error": True,
            "deleted": None,
            "is_transient": True,
            "message": (
                "The request timed out. The relationship may have been deleted. "
                "Use dataverse_get_relationship to verify."
            ),
            "metadata_id": params.metadata_id,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse delete relationship error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_delete_relationship")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


# ---------------------------------------------------------------------------
# Choice (option set) write tools
# ---------------------------------------------------------------------------


def _build_option_set_target_params(
    option_set_name: str | None,
    entity_logical_name: str | None,
    attribute_logical_name: str | None,
) -> dict:
    """Return OData query params for targeting a global or local choice."""
    if option_set_name:
        return {"OptionSetName": option_set_name}
    return {
        "EntityLogicalName": entity_logical_name,
        "AttributeLogicalName": attribute_logical_name,
    }


@write_tool(
    name="dataverse_create_choice",
    annotations={
        "title": "Create Global Choice",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_choice(
    params: CreateChoiceInput, ctx: Context
) -> str:
    """Create a new global choice (option set) in Dataverse.
    Global choices can be reused across multiple columns. After creating, use
    dataverse_publish_customizations to make the choice visible in the UI.
    """
    options_body = [
        {
            "Value": opt.value,
            "Label": _make_label(opt.label),
        }
        for opt in params.options
    ]
    body = {
        "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
        "Name": params.name,
        "DisplayName": _make_label(params.display_name),
        "IsGlobal": True,
        "OptionSetType": "Picklist",
        "Options": options_body,
    }

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/GlobalOptionSetDefinitions",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        entity_id = response.headers.get("OData-EntityId", "")
        logger.info("Created global choice %s", params.name)
        return json.dumps({
            "created": True,
            "name": params.name,
            "entity_id": entity_id,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse create choice error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_create_choice")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_update_choice",
    annotations={
        "title": "Update Global Choice",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_choice(
    params: UpdateChoiceInput, ctx: Context
) -> str:
    """Update an existing global choice via full PUT replacement.
    The Dataverse metadata API requires a full PUT — partial updates are not
    supported on GlobalOptionSetDefinitions.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        definition = dict(params.full_definition)
        definition.pop("@odata.context", None)
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await app_ctx.http_client.put(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/GlobalOptionSetDefinitions({params.metadata_id})",
            json=definition,
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Updated global choice %s", params.metadata_id)
        return json.dumps({
            "updated": True,
            "metadata_id": params.metadata_id,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse update choice error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_update_choice")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@delete_tool(
    name="dataverse_delete_choice",
    annotations={
        "title": "Delete Global Choice",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_choice(
    params: DeleteChoiceInput, ctx: Context
) -> str:
    """Delete a global choice (option set) by logical name.
    Call dataverse_publish_customizations after deleting global choices.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await app_ctx.http_client.delete(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/GlobalOptionSetDefinitions(Name='{_url_quote(params.name)}')",
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Deleted global choice %s", params.name)
        return json.dumps({
            "deleted": True,
            "name": params.name,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse delete choice error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_delete_choice")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_add_choice_option",
    annotations={
        "title": "Add Choice Option",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_add_choice_option(
    params: AddChoiceOptionInput, ctx: Context
) -> str:
    """Add a new option to a global or local choice column.
    Provide option_set_name for a global choice, or entity_logical_name +
    attribute_logical_name for a local (column-specific) choice — not both.
    """
    target_params = _build_option_set_target_params(
        params.option_set_name, params.entity_logical_name, params.attribute_logical_name
    )
    body: dict = {
        **target_params,
        "Label": _make_label(params.label),
        "SolutionUniqueName": params.solution_unique_name,
    }
    if params.value is not None:
        body["Value"] = params.value

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/InsertOptionValue",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        result = response.json()
        logger.info("Added option to choice (target=%s)", target_params)
        return json.dumps({
            "created": True,
            "value": result.get("NewOptionValue"),
            "label": params.label,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse add choice option error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_add_choice_option")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_update_choice_option",
    annotations={
        "title": "Update Choice Option",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_choice_option(
    params: UpdateChoiceOptionInput, ctx: Context
) -> str:
    """Update the display label of an existing option in a global or local choice.
    Provide option_set_name for a global choice, or entity_logical_name +
    attribute_logical_name for a local choice — not both.
    """
    target_params = _build_option_set_target_params(
        params.option_set_name, params.entity_logical_name, params.attribute_logical_name
    )
    body = {
        **target_params,
        "Value": params.value,
        "Label": _make_label(params.label),
        "MergeLabels": params.merge_labels,
        "SolutionUniqueName": params.solution_unique_name,
    }

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/UpdateOptionValue",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Updated option %d in choice (target=%s)", params.value, target_params)
        return json.dumps({
            "updated": True,
            "value": params.value,
            "label": params.label,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse update choice option error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_update_choice_option")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@delete_tool(
    name="dataverse_delete_choice_option",
    annotations={
        "title": "Delete Choice Option",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_choice_option(
    params: DeleteChoiceOptionInput, ctx: Context
) -> str:
    """Remove a specific option value from a global or local choice.
    Provide option_set_name for a global choice, or entity_logical_name +
    attribute_logical_name for a local choice — not both.
    """
    target_params = _build_option_set_target_params(
        params.option_set_name, params.entity_logical_name, params.attribute_logical_name
    )
    body = {
        **target_params,
        "Value": params.value,
        "SolutionUniqueName": params.solution_unique_name,
    }

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/DeleteOptionValue",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Deleted option %d from choice (target=%s)", params.value, target_params)
        return json.dumps({
            "deleted": True,
            "value": params.value,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse delete choice option error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_delete_choice_option")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_reorder_choice_options",
    annotations={
        "title": "Reorder Choice Options",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_reorder_choice_options(
    params: ReorderChoiceOptionsInput, ctx: Context
) -> str:
    """Reorder the options of a global or local choice.
    Provide option_set_name for a global choice, or entity_logical_name +
    attribute_logical_name for a local choice — not both.
    """
    target_params = _build_option_set_target_params(
        params.option_set_name, params.entity_logical_name, params.attribute_logical_name
    )
    body = {
        **target_params,
        "Values": params.values,
        "SolutionUniqueName": params.solution_unique_name,
    }

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/OrderOption",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Reordered choice options (target=%s)", target_params)
        return json.dumps({
            "reordered": True,
            "values": params.values,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse reorder choice options error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_reorder_choice_options")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


# ---------------------------------------------------------------------------
# Publish tools
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_publish_customizations",
    annotations={
        "title": "Publish Customizations",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_publish_customizations(
    params: PublishCustomizationsInput, ctx: Context
) -> str:
    """Publish Dataverse customizations to make schema changes visible in the UI.
    After creating or modifying tables, columns, relationships, or choices,
    customizations must be published before they appear in model-driven apps.

    Two modes:
    - Targeted: provide entities, option_sets, and/or relationships lists to
      publish only those components. Calls PublishXml with ParameterXml.
    - All: set publish_all=True to publish every unpublished customization in
      the environment via PublishAllXml. May take several minutes.
    """
    if params.publish_all:
        app_ctx = _get_app_ctx(ctx)
        try:
            base_url = resolve_base_url(app_ctx, params.dataverse_url)
        except ValueError as e:
            return json.dumps({'error': True, 'message': str(e)})

        try:
            headers = await build_headers(app_ctx, base_url, include_content_type=True)
            response = await app_ctx.http_client.post(
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/PublishAllXml",
                headers=headers,
                timeout=600.0,
            )
            response.raise_for_status()
            logger.info("Published all customizations")
            return json.dumps({"published": True, "action": "PublishAllXml"})
        except httpx.TimeoutException as e:
            logger.warning(
                "Timeout in dataverse_publish_customizations (PublishAllXml) — "
                "operation may have succeeded: %s",
                e,
            )
            return json.dumps({
                "error": True,
                "published": None,
                "is_transient": True,
                "message": (
                    "The request timed out. PublishAllXml may have succeeded in the background. "
                    "Check your model-driven app to verify."
                ),
            })
        except httpx.HTTPStatusError as e:
            logger.error(
                "Dataverse PublishAllXml error: %s (status=%d)",
                extract_error_message(e.response),
                e.response.status_code,
            )
            return json.dumps({
                "error": True,
                "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
            })
        except Exception as e:
            logger.exception("Unexpected error in dataverse_publish_customizations (PublishAllXml)")
            return json.dumps({
                "error": True,
                "message": f"Unexpected error: {type(e).__name__}: {e}",
            })

    # Targeted PublishXml
    root = ET.Element("importexportxml")

    entities_node = ET.SubElement(root, "entities")
    for entity_name in params.entities:
        entity_node = ET.SubElement(entities_node, "entity")
        entity_node.text = entity_name

    option_sets_node = ET.SubElement(root, "optionsets")
    for option_set_name in params.option_sets:
        option_set_node = ET.SubElement(option_sets_node, "optionset")
        option_set_node.text = option_set_name

    relationships_node = ET.SubElement(root, "relationships")
    for relationship_name in params.relationships:
        relationship_node = ET.SubElement(relationships_node, "relationship")
        relationship_node.text = relationship_name

    parameter_xml = ET.tostring(root, encoding="unicode", short_empty_elements=True)

    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await app_ctx.http_client.post(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/PublishXml",
            json={"ParameterXml": parameter_xml},
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        logger.info("Published customizations: %s", parameter_xml)
        return json.dumps({
            "published": True,
            "action": "PublishXml",
            "parameter_xml": parameter_xml,
        })
    except httpx.TimeoutException as e:
        logger.warning(
            "Timeout in dataverse_publish_customizations (PublishXml) — "
            "operation may have succeeded: %s",
            e,
        )
        return json.dumps({
            "error": True,
            "published": None,
            "is_transient": True,
            "message": (
                "The request timed out. PublishXml may have succeeded. "
                "Check your model-driven app to verify."
            ),
            "parameter_xml": parameter_xml,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse PublishXml error: %s (status=%d)",
            extract_error_message(e.response),
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {extract_error_message(e.response)}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_publish_customizations (PublishXml)")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
