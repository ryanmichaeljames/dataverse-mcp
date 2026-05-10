"""Table and column metadata tools for the Dataverse MCP server."""

import asyncio
import json
import logging
from urllib.parse import quote as _url_quote

import httpx
from mcp.server.fastmcp import Context
from PowerPlatform.Dataverse.core.errors import DataverseError, HttpError

from dataverse_mcp._app import mcp
from dataverse_mcp.client import AppContext, get_bearer_token, get_dataverse_client
from dataverse_mcp.models import (
    CheckRelationshipEligibilityInput,
    GetChoiceInput,
    GetColumnInput,
    GetRelationshipInput,
    GetTableMetadataInput,
    ListChoiceColumnOptionsInput,
    ListChoicesInput,
    ListColumnsInput,
    ListRelationshipsInput,
    ListTablesInput,
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

_DATAVERSE_API_VERSION = "v9.2"


def _get_client(ctx: Context, dataverse_url: str | None):
    """Resolve the DataverseClient for the requested environment."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    return get_dataverse_client(app_ctx, dataverse_url)


def _get_app_ctx(ctx: Context) -> AppContext:
    """Return the application context from the request context."""
    return ctx.request_context.lifespan_context


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
    select = params.select or _DEFAULT_TABLE_SELECT

    try:
        client = _get_client(ctx, params.dataverse_url)

        def _query():
            return client.tables.list(
                filter=params.filter,
                select=select,
            )

        tables = await asyncio.to_thread(_query)
        return json.dumps({
            "tables": tables,
            "count": len(tables),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
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
    try:
        client = _get_client(ctx, params.dataverse_url)

        def _query():
            return client.tables.get(params.table_name)

        info = await asyncio.to_thread(_query)

        if info is None:
            return json.dumps({
                "error": True,
                "message": f"Table not found: '{params.table_name}'",
            })

        return json.dumps({
            "table": {
                "logical_name": info.logical_name,
                "schema_name": info.schema_name,
                "entity_set_name": info.entity_set_name,
                "metadata_id": info.metadata_id,
                "primary_id_attribute": info.primary_id_attribute,
                "primary_name_attribute": info.primary_name_attribute,
            },
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
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
    select = params.select or _DEFAULT_COLUMN_SELECT
    attr_filter = (
        f"AttributeType eq '{params.attribute_type}'"
        if params.attribute_type
        else None
    )

    try:
        client = _get_client(ctx, params.dataverse_url)

        def _query():
            return client.tables.list_columns(
                params.table_logical_name,
                select=select,
                filter=attr_filter,
            )

        columns = await asyncio.to_thread(_query)
        return json.dumps({
            "columns": columns,
            "count": len(columns),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
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
    col_filter = f"LogicalName eq '{params.column_logical_name}'"

    try:
        client = _get_client(ctx, params.dataverse_url)

        def _query():
            return client.tables.list_columns(
                params.table_logical_name,
                filter=col_filter,
            )

        columns = await asyncio.to_thread(_query)
        if not columns:
            return json.dumps({
                "error": True,
                "message": (
                    f"Column '{params.column_logical_name}' not found on "
                    f"table '{params.table_logical_name}'"
                ),
            })
        return json.dumps({"column": columns[0]})
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
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
    bearer_token: str,
    table: str,
    column: str,
    cast: str,
) -> list[dict]:
    """Fetch option values for a Picklist or MultiSelectPicklist column."""
    table_enc = _url_quote(table, safe="")
    col_enc = _url_quote(column, safe="")
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
        f"EntityDefinitions(LogicalName='{table_enc}')"
        f"/Attributes/{cast}"
    )
    params = {
        "$select": "LogicalName",
        "$expand": "OptionSet($select=Options)",
        "$filter": f"LogicalName eq '{col_enc}'",
    }

    def _request():
        with httpx.Client(timeout=30.0) as http_client:
            response = http_client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                    "Accept": "application/json",
                    "OData-MaxVersion": "4.0",
                    "OData-Version": "4.0",
                },
            )
            response.raise_for_status()
            return response.json()

    payload = await asyncio.to_thread(_request)
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
    base_url = str(params.dataverse_url)
    scope = f"{base_url}/.default"

    try:
        bearer_token = await asyncio.to_thread(get_bearer_token, app_ctx, scope)

        options = await _fetch_picklist_options(
            base_url,
            bearer_token,
            params.table_logical_name,
            params.column_logical_name,
            "Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
        )

        if not options:
            options = await _fetch_picklist_options(
                base_url,
                bearer_token,
                params.table_logical_name,
                params.column_logical_name,
                "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata",
            )

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
        logger.error(
            "Dataverse metadata API error: %s (status=%d)",
            e.response.text,
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": (
                f"Dataverse returned HTTP {e.response.status_code}: {e.response.text}"
            ),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
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
    bearer_token: str,
    url_path: str,
    top: int,
    select: str | None = None,
) -> list[dict]:
    """Fetch relationship definitions from the Dataverse metadata API via httpx."""
    params: dict[str, str | int] = {}
    if select:
        params["$select"] = select
    params["$top"] = top

    def _request():
        with httpx.Client(timeout=30.0) as http_client:
            response = http_client.get(
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{url_path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {bearer_token}",
                    "Accept": "application/json",
                    "OData-MaxVersion": "4.0",
                    "OData-Version": "4.0",
                },
            )
            response.raise_for_status()
            return response.json()

    payload = await asyncio.to_thread(_request)
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
    base_url = str(params.dataverse_url)
    scope = f"{base_url}/.default"

    try:
        bearer_token = await asyncio.to_thread(get_bearer_token, app_ctx, scope)

        if params.table_logical_name:
            table_enc = _url_quote(params.table_logical_name, safe="")
            base_entity = f"EntityDefinitions(LogicalName='{table_enc}')"
            types_to_fetch = (
                [params.relationship_type]
                if params.relationship_type
                else ["OneToMany", "ManyToOne", "ManyToMany"]
            )
            all_rels: list[dict] = []
            for rel_type in types_to_fetch:
                rels = await _fetch_relationships_httpx(
                    base_url,
                    bearer_token,
                    f"{base_entity}/{rel_type}Relationships",
                    params.top,
                    select=_REL_TYPE_SELECT[rel_type],
                )
                all_rels.extend(rels)
        else:
            # Polymorphic collection — omit $select to avoid type-specific field errors
            all_rels = await _fetch_relationships_httpx(
                base_url,
                bearer_token,
                "RelationshipDefinitions",
                params.top,
            )

        return json.dumps({
            "relationships": all_rels,
            "count": len(all_rels),
            "has_more": len(all_rels) >= params.top,
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse metadata API error: %s (status=%d)",
            e.response.text,
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {e.response.text}",
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
    base_url = str(params.dataverse_url)
    scope = f"{base_url}/.default"
    schema_enc = _url_quote(params.schema_name, safe="")

    try:
        bearer_token = await asyncio.to_thread(get_bearer_token, app_ctx, scope)

        def _request():
            with httpx.Client(timeout=30.0) as http_client:
                response = http_client.get(
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
                    f"RelationshipDefinitions(SchemaName='{schema_enc}')",
                    headers={
                        "Authorization": f"Bearer {bearer_token}",
                        "Accept": "application/json",
                        "OData-MaxVersion": "4.0",
                        "OData-Version": "4.0",
                    },
                )
                response.raise_for_status()
                return response.json()

        relationship = await asyncio.to_thread(_request)
        relationship.pop("@odata.context", None)
        return json.dumps({"relationship": relationship})
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse metadata API error: %s (status=%d)",
            e.response.text,
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {e.response.text}",
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
    Options values and labels are not available on the list endpoint — use
    dataverse_get_choice to retrieve full option details for a specific choice.

    Use dataverse_get_choice to retrieve full details for a specific choice
    by name or MetadataId.
    """
    app_ctx = _get_app_ctx(ctx)
    base_url = str(params.dataverse_url)
    scope = f"{base_url}/.default"
    # Strip 'Options' — not selectable on the polymorphic collection type
    raw_select = [f for f in (params.select or [])] if params.select else None
    select = ",".join(raw_select) if raw_select else _DEFAULT_CHOICE_SELECT

    try:
        bearer_token = await asyncio.to_thread(get_bearer_token, app_ctx, scope)

        query_params: dict[str, str] = {"$select": select}

        def _request():
            with httpx.Client(timeout=30.0) as http_client:
                response = http_client.get(
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/GlobalOptionSetDefinitions",
                    params=query_params,
                    headers={
                        "Authorization": f"Bearer {bearer_token}",
                        "Accept": "application/json",
                        "OData-MaxVersion": "4.0",
                        "OData-Version": "4.0",
                    },
                )
                response.raise_for_status()
                return response.json()

        payload = await asyncio.to_thread(_request)
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
            e.response.text,
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {e.response.text}",
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
    base_url = str(params.dataverse_url)
    scope = f"{base_url}/.default"

    if params.name:
        name_enc = _url_quote(params.name, safe="")
        path = f"GlobalOptionSetDefinitions(Name='{name_enc}')"
    else:
        path = f"GlobalOptionSetDefinitions({params.metadata_id})"

    try:
        bearer_token = await asyncio.to_thread(get_bearer_token, app_ctx, scope)

        def _request():
            with httpx.Client(timeout=30.0) as http_client:
                response = http_client.get(
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{path}",
                    headers={
                        "Authorization": f"Bearer {bearer_token}",
                        "Accept": "application/json",
                        "OData-MaxVersion": "4.0",
                        "OData-Version": "4.0",
                    },
                )
                response.raise_for_status()
                return response.json()

        choice = await asyncio.to_thread(_request)
        choice.pop("@odata.context", None)
        return json.dumps({"choice": choice})
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse metadata API error: %s (status=%d)",
            e.response.text,
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {e.response.text}",
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
    """Check whether a table can participate in a relationship.

    Calls Dataverse relationship eligibility endpoints for the target table:
    - 'referenced'    — can be the primary (one) side of a 1:N
    - 'referencing'   — can be the related (many) side of a 1:N
    - 'many_to_many'  — can participate in an N:N relationship

    Returns eligible (bool) for the requested check_type.
    """
    app_ctx = _get_app_ctx(ctx)
    base_url = str(params.dataverse_url)
    scope = f"{base_url}/.default"
    table_enc = _url_quote(params.table_logical_name, safe="")

    check_endpoint_map = {
        "referenced": "CanBeReferenced",
        "referencing": "CanBeReferencing",
        "many_to_many": "CanManyToMany",
    }
    endpoint = check_endpoint_map[params.check_type]

    def _to_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return False

    try:
        bearer_token = await asyncio.to_thread(get_bearer_token, app_ctx, scope)

        def _request():
            with httpx.Client(timeout=30.0) as http_client:
                response = http_client.get(
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
                    f"EntityDefinitions(LogicalName='{table_enc}')/{endpoint}",
                    headers={
                        "Authorization": f"Bearer {bearer_token}",
                        "Accept": "application/json",
                        "OData-MaxVersion": "4.0",
                        "OData-Version": "4.0",
                    },
                )
                response.raise_for_status()
                return response.json()

        result = await asyncio.to_thread(_request)
        raw = result.get(endpoint, result.get("value", result))
        eligible = raw.get("Value", raw) if isinstance(raw, dict) else raw
        return json.dumps({
            "table_logical_name": params.table_logical_name,
            "check_type": params.check_type,
            "eligible": _to_bool(eligible),
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Dataverse eligibility API error: %s (status=%d)",
            e.response.text,
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {e.response.text}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_check_relationship_eligibility")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
