"""Table and column metadata tools for the Dataverse MCP server."""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import httpx
from mcp.server.mcpserver import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("schema")
from dataverse_mcp.client import (
    encode_odata_literal,
    _DATAVERSE_API_VERSION,
    build_headers,
    extract_error_message,
    finalize_response,
    get_app_ctx,
    odata_quote,
    paginate_records,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import (
    AddChoiceOptionInput,
    CheckRelationshipEligibilityInput,
    CreateAlternateKeyInput,
    CreateChoiceInput,
    CreateColumnInput,
    CreateManyToManyRelationshipInput,
    CreateMultiTableLookupInput,
    CreateOneToManyRelationshipInput,
    CreateTableInput,
    DeleteAlternateKeyInput,
    DeleteChoiceInput,
    DeleteChoiceOptionInput,
    DeleteColumnInput,
    DeleteRelationshipInput,
    DeleteTableInput,
    GetChoiceInput,
    GetColumnInput,
    GetRelationshipInput,
    GetTableMetadataInput,
    GetValidRelationshipEntitiesInput,
    IsComponentCustomizableInput,
    ListAlternateKeysInput,
    ListChoiceColumnOptionsInput,
    ListChoicesInput,
    ListColumnsInput,
    ListLanguagesInput,
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
from dataverse_mcp.tools.solutions import COMPONENT_TYPE_NAMES

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

# Metadata writes (EntityDefinitions, attributes, relationships) can be slow;
# publishing all customizations slower still.
_METADATA_TIMEOUT = 300.0
_PUBLISH_TIMEOUT = 600.0


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


@tool(
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
    """List tables (entities) in the Dataverse environment with their logical names and display names.

    Use filter to narrow results (e.g., "IsCustomEntity eq true" for custom tables only).
    Use dataverse_get_table_metadata for full schema details on one table.
    Use dataverse_get_entity_sets to discover OData collection names for record queries.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        tables = await paginate_records(url, headers, params.top, app_ctx.http_client)
        return finalize_response({
            "tables": tables,
            "count": len(tables),
            "has_more": len(tables) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_tables")


@tool(
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
    """Get schema details for a single Dataverse table by logical name.

    Returns the entity set name, primary key attribute, and primary name attribute.
    Use dataverse_list_tables to discover available table logical names.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = encode_odata_literal(params.table_name)
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
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
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
    except Exception as e:
        return tool_error_response(e, "dataverse_get_table_metadata")


@tool(
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
    """List column (attribute) definitions for a Dataverse table.

    Use attribute_type to narrow by column type (e.g., 'Lookup', 'Picklist').
    For full metadata on a single column use dataverse_get_column.
    For Picklist/MultiSelectPicklist option values use dataverse_list_choice_column_options.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = encode_odata_literal(params.table_logical_name)
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
        return finalize_response({
            "columns": columns,
            "count": len(columns),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_columns")


@tool(
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
    """Get full metadata for a single column on a Dataverse table, including type-specific properties.

    Returns all properties including MaxLength, Precision, RequiredLevel, Format,
    and IsValidForCreate. Use before updating a column — pass the returned object
    as full_definition to dataverse_update_column.
    For Picklist/MultiSelectPicklist option values use dataverse_list_choice_column_options.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = encode_odata_literal(params.table_logical_name)
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
        return finalize_response({"column": column})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_column")


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
    table_enc = encode_odata_literal(table)
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

    response = await request_with_retry(
        http_client, "GET", url, params=params, headers=headers
    )
    response.raise_for_status()
    payload = response.json()
    return _extract_options(payload.get("value", []))


@tool(
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
    """Get option values and labels for a Picklist or MultiSelectPicklist column's LOCAL option set.

    Use this before filtering records with choice columns — the integer value is required for
    OData filter expressions (e.g., "statuscode eq 1"). For GLOBAL choices shared across tables
    use dataverse_get_choice instead. Handles both Picklist and MultiSelectPicklist automatically.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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

        return finalize_response({
            "options": options,
            "count": len(options),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_choice_column_options")


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

    response = await request_with_retry(
        http_client,
        "GET",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{url_path}",
        params=params,
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("value", [])


@tool(
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
    """List relationship definitions for a table (OneToMany, ManyToOne, ManyToMany) or the whole environment.

    Use the returned SchemaName with dataverse_get_relationship for full cascade and
    navigation property details. Navigation property names from the results are required
    for OData $expand queries and for dataverse_associate_records.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )

        if params.table_logical_name:
            table_enc = encode_odata_literal(params.table_logical_name)
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

        return finalize_response({
            "relationships": all_rels,
            "count": len(all_rels),
            "has_more": len(all_rels) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_relationships")


@tool(
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
    """Get full metadata for a single relationship by schema name — cascade configuration and navigation properties.

    Schema names are case-sensitive; use the exact SchemaName from dataverse_list_relationships.
    Use this to fetch the full definition before updating with dataverse_update_relationship.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    schema_enc = encode_odata_literal(params.schema_name)

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        response = await request_with_retry(app_ctx.http_client, "GET",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"RelationshipDefinitions(SchemaName='{schema_enc}')",
            headers=headers,
        )
        response.raise_for_status()
        relationship = response.json()
        relationship.pop("@odata.context", None)
        return finalize_response({"relationship": relationship})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_relationship")


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


@tool(
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
    """List GLOBAL choice (option set) definitions in the Dataverse environment.

    Option values and labels are not returned here — use dataverse_get_choice to retrieve
    the full option set for a specific choice. $filter is not supported by this endpoint;
    top is applied client-side.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        response = await request_with_retry(app_ctx.http_client, "GET",
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
        return finalize_response({
            "choices": choices,
            "count": len(choices),
            "has_more": has_more,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_choices")


@tool(
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
    """Get one GLOBAL choice (option set) — all option values, codes, and labels — by name or MetadataId.

    For the options of a specific column's LOCAL choice use
    dataverse_list_choice_column_options instead.
    Provide either name or metadata_id; name takes precedence when both are given.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})
    if params.name:
        name_enc = encode_odata_literal(params.name)
        path = f"GlobalOptionSetDefinitions(Name='{name_enc}')"
    else:
        path = f"GlobalOptionSetDefinitions({params.metadata_id})"

    try:
        headers = await build_headers(
            app_ctx, base_url,
            extra=_build_extra_headers(consistency_strong=params.consistency_strong),
        )
        response = await request_with_retry(app_ctx.http_client, "GET",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{path}",
            headers=headers,
        )
        response.raise_for_status()
        choice = response.json()
        choice.pop("@odata.context", None)
        return finalize_response({"choice": choice})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_choice")


@tool(
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
    """Pre-validate whether a table supports a specific relationship role before creating a relationship.

    Only call this immediately before dataverse_create_one_to_many_relationship or
    dataverse_create_many_to_many_relationship — do not use for general queries or data reads.
    Returns eligible (bool) for the requested check_type (see check_type field for valid values).

    This tool answers "is THIS ONE table OK?" — a boolean about a table you can
    already name. To answer "WHICH tables are OK?" — the enumeration, when you do
    not yet know which table to point at — use
    dataverse_get_valid_relationship_entities, whose role values mirror this
    tool's check_type values one for one.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        response = await request_with_retry(app_ctx.http_client, "POST",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_check_relationship_eligibility")


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

    The schema_name must include a publisher prefix (e.g., 'cr123_Widget');
    the logical name is its lowercase form. Call dataverse_publish_customizations afterward.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    body = _build_create_table_body(params)

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/EntityDefinitions",
            json=body,
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_create_table")


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
    """Update a table's display name or description via a full PUT replacement.

    The tool fetches the current definition and applies your changes before PUTting it back.
    Call dataverse_publish_customizations afterward. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    if not params.display_name and params.description is None:
        return json.dumps({
            "error": True,
            "message": "Provide at least one of display_name or description to update.",
        })

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    table_enc = encode_odata_literal(params.table_logical_name)

    try:
        headers = await build_headers(app_ctx, base_url)
        get_resp = await request_with_retry(app_ctx.http_client, "GET",
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
        response = await request_with_retry(app_ctx.http_client, "PUT",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_update_table")


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
    """Permanently delete a custom table and all its records — irreversible.

    Only custom, unmanaged tables (IsCustomEntity=true, IsManaged=false) can be deleted.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})
    table_enc = encode_odata_literal(params.table_logical_name)

    try:
        headers = await build_headers(app_ctx, base_url)
        get_resp = await request_with_retry(app_ctx.http_client, "GET",
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

        response = await request_with_retry(app_ctx.http_client, "DELETE",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')",
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_table")


# ---------------------------------------------------------------------------
# Column schema write tools
# ---------------------------------------------------------------------------

_ATTRIBUTE_TYPE_ODATA_MAP = {
    "String": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
    "Memo": "Microsoft.Dynamics.CRM.MemoAttributeMetadata",
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
    """Add a new typed column (attribute) to a Dataverse table.

    Supported types: String, Memo, Integer, Decimal, DateTime, Boolean, Lookup, Picklist, MultiSelectPicklist.
    Boolean columns automatically get an OptionSet — use boolean_true_label/boolean_false_label to set option labels.
    Picklist/MultiSelectPicklist columns can bind to an existing global choice via global_choice_name.
    Memo columns default MaxLength to 2000 and omit IsValidForAdvancedFind (Dataverse rejects it for Memo type).
    Use type_specific_properties for additional type-specific fields (e.g., String → {"MaxLength": 100}).
    Call dataverse_publish_customizations after creating columns. Requires DATAVERSE_ALLOW_WRITE=true.
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
    if params.attribute_type == "Memo":
        # Dataverse requires MaxLength on Memo; default to 2000 if not supplied.
        body.setdefault("MaxLength", 2000)
    if params.attribute_type == "Boolean":
        body["OptionSet"] = {
            "@odata.type": "Microsoft.Dynamics.CRM.BooleanOptionSetMetadata",
            "TrueOption": {
                "@odata.type": "Microsoft.Dynamics.CRM.OptionMetadata",
                "Value": 1,
                "Label": _make_label(params.boolean_true_label or "Yes"),
            },
            "FalseOption": {
                "@odata.type": "Microsoft.Dynamics.CRM.OptionMetadata",
                "Value": 0,
                "Label": _make_label(params.boolean_false_label or "No"),
            },
        }
    if params.type_specific_properties:
        body.update(params.type_specific_properties)
    if params.attribute_type == "Memo":
        # Dataverse rejects IsValidForAdvancedFind on Memo (nested resource error).
        body.pop("IsValidForAdvancedFind", None)

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    table_enc = encode_odata_literal(params.table_logical_name)

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )

        # Resolve global choice name → MetadataId GUID before the POST.
        # @odata.bind requires a GUID key; Name= is not supported by the metadata API.
        if params.global_choice_name and params.attribute_type in ("Picklist", "MultiSelectPicklist"):
            lookup_headers = await build_headers(app_ctx, base_url)
            choice_name_enc = encode_odata_literal(params.global_choice_name)
            lookup_resp = await request_with_retry(
                app_ctx.http_client, "GET",
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/GlobalOptionSetDefinitions(Name='{choice_name_enc}')"
                "?$select=MetadataId",
                headers=lookup_headers,
            )
            lookup_resp.raise_for_status()
            metadata_id = lookup_resp.json().get("MetadataId")
            if not metadata_id:
                return json.dumps({
                    "error": True,
                    "message": f"Global choice '{params.global_choice_name}' was not found.",
                })
            body["GlobalOptionSet@odata.bind"] = (
                f"/GlobalOptionSetDefinitions({metadata_id})"
            )

        response = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')/Attributes",
            json=body,
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_create_column")


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
    """Update a column's metadata — the metadata API requires a full PUT, not a partial update.

    First fetch the current definition with dataverse_get_column, change the fields you need,
    then pass the whole object as full_definition.
    Call dataverse_publish_customizations after updating columns.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    table_enc = encode_odata_literal(params.table_logical_name)
    column_enc = encode_odata_literal(params.column_logical_name)

    try:
        definition = dict(params.full_definition)
        definition.pop("@odata.context", None)
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "PUT",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_update_column")


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
    """Permanently delete a custom column and all its data from a table — irreversible.

    Only custom, unmanaged columns can be deleted.
    Call dataverse_publish_customizations after deleting columns. Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    table_enc = encode_odata_literal(params.table_logical_name)
    column_enc = encode_odata_literal(params.column_logical_name)

    try:
        headers = await build_headers(app_ctx, base_url)
        get_resp = await request_with_retry(app_ctx.http_client, "GET",
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

        response = await request_with_retry(app_ctx.http_client, "DELETE",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')"
            f"/Attributes(LogicalName='{column_enc}')",
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_column")


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
    """Create a 1:N relationship between two tables and its lookup column on the referencing side.

    Optionally call dataverse_check_relationship_eligibility first to pre-validate eligibility.
    Call dataverse_publish_customizations after creating relationships. Requires DATAVERSE_ALLOW_WRITE=true.
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

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/RelationshipDefinitions",
            json=body,
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_create_one_to_many_relationship")


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

    Optionally call dataverse_check_relationship_eligibility first to pre-validate eligibility.
    Call dataverse_publish_customizations after creating relationships. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    body = {
        "@odata.type": "Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata",
        "SchemaName": params.schema_name,
        "Entity1LogicalName": params.entity1_logical_name,
        "Entity2LogicalName": params.entity2_logical_name,
        "IntersectEntityName": params.intersect_entity_name,
    }

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/RelationshipDefinitions",
            json=body,
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_create_many_to_many_relationship")


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
    """Create a polymorphic (multi-table) lookup column that can reference multiple tables.

    The lookup is added to owning_entity and can point to any of the target_entities.
    Call dataverse_publish_customizations after creating lookup columns. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    # CreatePolymorphicLookupAttribute rejects @odata.type annotations on its
    # entity-typed parameters ("Incompatible type kinds"); the documented
    # payload supplies plain objects with AttributeType/AttributeTypeName.
    relationships = [
        {
            "ReferencedEntity": target,
            "ReferencingEntity": params.owning_entity,
            "SchemaName": f"{params.lookup_schema_name}_{target}",
        }
        for target in params.target_entities
    ]
    lookup = {
        "AttributeType": "Lookup",
        "AttributeTypeName": {"Value": "LookupType"},
        "SchemaName": params.lookup_schema_name,
        "DisplayName": _make_label(params.lookup_display_name),
    }
    body = {
        "OneToManyRelationships": relationships,
        "Lookup": lookup,
    }

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/CreatePolymorphicLookupAttribute",
            json=body,
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_create_multi_table_lookup")


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
    """Update a relationship's cascade behavior or configuration — the metadata API requires a full PUT.

    First fetch the current definition with dataverse_get_relationship, change the fields you need,
    then pass the whole object as full_definition.
    Call dataverse_publish_customizations after updating relationships.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        definition = dict(params.full_definition)
        definition.pop("@odata.context", None)
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "PUT",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_update_relationship")


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
    """Delete a custom relationship by MetadataId — permanent; deletes the associated lookup column for 1:N.

    Only custom, unmanaged relationships can be deleted.
    Call dataverse_publish_customizations afterward. Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        try:
            get_resp = await request_with_retry(app_ctx.http_client, "GET",
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

        response = await request_with_retry(app_ctx.http_client, "DELETE",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/RelationshipDefinitions({params.metadata_id})",
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_relationship")


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
    """Create a new GLOBAL choice (option set) that can be reused across multiple columns.

    Call dataverse_publish_customizations after creating global choices. Requires DATAVERSE_ALLOW_WRITE=true.
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

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "POST",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_create_choice")


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
    """Update a global choice's metadata — the metadata API requires a full PUT, not a partial update.

    First fetch the current definition with dataverse_get_choice, change the fields you need,
    then pass the whole object as full_definition.
    Call dataverse_publish_customizations after updating global choices.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        definition = dict(params.full_definition)
        definition.pop("@odata.context", None)
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "PUT",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_update_choice")


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
    """Delete a GLOBAL choice (option set) by logical name.

    Deleting a global choice still referenced by a column will fail; remove
    those columns first. Call dataverse_publish_customizations afterward.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(app_ctx.http_client, "DELETE",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/GlobalOptionSetDefinitions(Name='{encode_odata_literal(params.name)}')",
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Deleted global choice %s", params.name)
        return json.dumps({
            "deleted": True,
            "name": params.name,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_choice")


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
    """Add a new option to a global or local (column-specific) choice.

    Provide option_set_name for a global choice, or entity_logical_name +
    attribute_logical_name for a local choice — not both. Requires DATAVERSE_ALLOW_WRITE=true.
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

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await request_with_retry(app_ctx.http_client, "POST",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_add_choice_option")


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
    attribute_logical_name for a local choice — not both. Requires DATAVERSE_ALLOW_WRITE=true.
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

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await request_with_retry(app_ctx.http_client, "POST",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_update_choice_option")


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
    attribute_logical_name for a local choice — not both. Requires DATAVERSE_ALLOW_DELETE=true.
    """
    target_params = _build_option_set_target_params(
        params.option_set_name, params.entity_logical_name, params.attribute_logical_name
    )
    body = {
        **target_params,
        "Value": params.value,
        "SolutionUniqueName": params.solution_unique_name,
    }

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await request_with_retry(app_ctx.http_client, "POST",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_choice_option")


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
    """Reorder all options of a global or local choice by supplying the full ordered list of values.

    Provide option_set_name for a global choice, or entity_logical_name +
    attribute_logical_name for a local choice — not both. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    target_params = _build_option_set_target_params(
        params.option_set_name, params.entity_logical_name, params.attribute_logical_name
    )
    body = {
        **target_params,
        "Values": params.values,
        "SolutionUniqueName": params.solution_unique_name,
    }

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await request_with_retry(app_ctx.http_client, "POST",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_reorder_choice_options")


# ---------------------------------------------------------------------------
# Alternate key metadata tools
# ---------------------------------------------------------------------------

_DEFAULT_KEY_SELECT = ",".join([
    "MetadataId",
    "SchemaName",
    "LogicalName",
    "DisplayName",
    "KeyAttributes",
    "EntityKeyIndexStatus",
    "IsManaged",
])


@tool(
    name="dataverse_list_alternate_keys",
    annotations={
        "title": "List Alternate Keys",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_alternate_keys(
    params: ListAlternateKeysInput, ctx: Context
) -> str:
    """List alternate keys (EntityKeyMetadata) defined on a Dataverse table.

    Alternate keys let integration tools upsert records by business values
    instead of GUIDs. Returns SchemaName, LogicalName, KeyAttributes, and
    EntityKeyIndexStatus (which tracks async index build progress).
    Use the LogicalName with dataverse_delete_alternate_key to remove a key.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = encode_odata_literal(params.table_logical_name)
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
        f"EntityDefinitions(LogicalName='{table_enc}')/Keys"
        f"?$select={_DEFAULT_KEY_SELECT}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        keys = await paginate_records(url, headers, params.top, app_ctx.http_client)
        return finalize_response({
            "alternate_keys": keys,
            "count": len(keys),
            "has_more": len(keys) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_alternate_keys")


@write_tool(
    name="dataverse_create_alternate_key",
    annotations={
        "title": "Create Alternate Key",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_alternate_key(
    params: CreateAlternateKeyInput, ctx: Context
) -> str:
    """Create an alternate key on a Dataverse table.

    Alternate keys let integration tools identify records by business values
    (e.g., an account number or external ID) instead of GUIDs, which is
    required for alternate-key upserts.

    Key creation triggers an asynchronous SQL index build. The response
    includes entity_key_index_status and async_job_id so callers can track
    progress. Wait until EntityKeyIndexStatus='Active' before using the key
    for upserts. Use dataverse_list_alternate_keys to poll status.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    body = {
        "@odata.type": "Microsoft.Dynamics.CRM.EntityKeyMetadata",
        "SchemaName": params.schema_name,
        "DisplayName": _make_label(params.display_name),
        "KeyAttributes": params.key_attributes,
    }

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = encode_odata_literal(params.table_logical_name)

    try:
        headers = await build_headers(
            app_ctx, base_url, include_content_type=True,
            extra=_build_extra_headers(solution_unique_name=params.solution_unique_name),
        )
        response = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')/Keys",
            json=body,
            headers=headers,
            timeout=_METADATA_TIMEOUT,
        )
        response.raise_for_status()

        # Attempt to parse the returned EntityKeyMetadata for async status
        entity_key_index_status: str | None = None
        async_job_id: str | None = None
        metadata_id: str | None = None
        try:
            payload = response.json()
            entity_key_index_status = payload.get("EntityKeyIndexStatus")
            # AsyncJob is a navigation property (EntityReference), not a
            # selectable scalar. It only appears when the POST returns a body;
            # extract its id when present, otherwise leave None.
            async_job = payload.get("AsyncJob")
            async_job_id = async_job.get("Id") if isinstance(async_job, dict) else None
            metadata_id = payload.get("MetadataId")
        except Exception:
            pass  # Body is optional; OData-EntityId header is the primary ID

        location = response.headers.get("OData-EntityId") or response.headers.get("location", "")
        logger.info(
            "Created alternate key %s on table %s — status: %s",
            params.schema_name, params.table_logical_name, entity_key_index_status,
        )
        return json.dumps({
            "created": True,
            "table_logical_name": params.table_logical_name,
            "schema_name": params.schema_name,
            "logical_name": params.schema_name.lower(),
            "key_attributes": params.key_attributes,
            "entity_key_index_status": entity_key_index_status,
            "async_job_id": async_job_id,
            "metadata_id": metadata_id,
            "location": location,
            "note": (
                "Index build is asynchronous. Poll dataverse_list_alternate_keys "
                "until EntityKeyIndexStatus='Active' before using the key for upserts."
            ),
        })
    except httpx.TimeoutException as e:
        logger.warning(
            "Timeout in dataverse_create_alternate_key — operation may have succeeded: %s", e
        )
        return json.dumps({
            "error": True,
            "created": None,
            "is_transient": True,
            "message": (
                "The request timed out before the server responded. The alternate key "
                "may have been created. Use dataverse_list_alternate_keys to verify."
            ),
            "table_logical_name": params.table_logical_name,
            "schema_name": params.schema_name,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_alternate_key")


@delete_tool(
    name="dataverse_delete_alternate_key",
    annotations={
        "title": "Delete Alternate Key",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_delete_alternate_key(
    params: DeleteAlternateKeyInput, ctx: Context
) -> str:
    """Delete an alternate key from a Dataverse table by its logical name.

    Removes the alternate key definition and drops the underlying SQL index.
    Any upsert operations that reference this key will fail after deletion.
    Use dataverse_list_alternate_keys to find the key_logical_name before deleting.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    table_enc = encode_odata_literal(params.table_logical_name)
    key_enc = encode_odata_literal(params.key_logical_name)

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(app_ctx.http_client, "DELETE",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')"
            f"/Keys(LogicalName='{key_enc}')",
            headers=headers,
            timeout=_METADATA_TIMEOUT,
        )
        response.raise_for_status()
        logger.info(
            "Deleted alternate key %s from table %s",
            params.key_logical_name, params.table_logical_name,
        )
        return json.dumps({
            "deleted": True,
            "table_logical_name": params.table_logical_name,
            "key_logical_name": params.key_logical_name,
        })
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return json.dumps({
                "error": True,
                "message": (
                    f"Alternate key '{params.key_logical_name}' was not found on table "
                    f"'{params.table_logical_name}'."
                ),
            })
        return tool_error_response(e, "dataverse_delete_alternate_key")
    except httpx.TimeoutException as e:
        logger.warning(
            "Timeout in dataverse_delete_alternate_key — operation may have succeeded: %s", e
        )
        return json.dumps({
            "error": True,
            "deleted": None,
            "is_transient": True,
            "message": (
                "The request timed out. The alternate key may have been deleted. "
                "Use dataverse_list_alternate_keys to verify."
            ),
            "table_logical_name": params.table_logical_name,
            "key_logical_name": params.key_logical_name,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_alternate_key")


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
    """Publish Dataverse schema customizations to make changes visible in model-driven apps.

    Use targeted mode (entities/option_sets/relationships) to publish specific components,
    or set publish_all=True to publish all unpublished customizations (may take several minutes).
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    if params.publish_all:
        app_ctx = get_app_ctx(ctx)
        try:
            base_url = resolve_base_url(params.dataverse_url)
        except ValueError as e:
            return json.dumps({'error': True, 'message': str(e)})

        try:
            headers = await build_headers(app_ctx, base_url, include_content_type=True)
            response = await request_with_retry(app_ctx.http_client, "POST",
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/PublishAllXml",
                headers=headers,
                timeout=_PUBLISH_TIMEOUT,
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
        except Exception as e:
            return tool_error_response(e, "dataverse_publish_customizations")

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

    if params.web_resource_ids:
        webresources_node = ET.SubElement(root, "webresources")
        for wr_id in params.web_resource_ids:
            wr_node = ET.SubElement(webresources_node, "webresource")
            wr_node.text = wr_id

    parameter_xml = ET.tostring(root, encoding="unicode", short_empty_elements=True)

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({'error': True, 'message': str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/PublishXml",
            json={"ParameterXml": parameter_xml},
            headers=headers,
            timeout=_METADATA_TIMEOUT,
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
    except Exception as e:
        return tool_error_response(e, "dataverse_publish_customizations")


# ---------------------------------------------------------------------------
# Component customizability tools
# ---------------------------------------------------------------------------


def _resolve_component_type(code: int) -> str:
    """Human-readable name for a solution component type code."""
    return COMPONENT_TYPE_NAMES.get(code, f"ComponentType({code})")


# Confirmed live shape of IsComponentCustomizableResponse: flat, with exactly one
# property whose name matches the function name — {"IsComponentCustomizable": true}
# — observed for component types 1 (Entity), 2 (Attribute) and 61 (Web Resource).
_CUSTOMIZABLE_PROPERTY = "IsComponentCustomizable"


def _extract_verdict(payload: Any) -> tuple[str, bool] | None:
    """Return the customizability verdict as ``(source, value)``, or None.

    Two tiers, mirroring ``_extract_version`` in tools/environments.py:

    1. ``IsComponentCustomizable`` is the confirmed property name (verified live
       against a real org), so it is read directly.
    2. A defensive fallback for a differently-shaped payload: a lone boolean —
       either the body itself or the payload's single boolean-valued property —
       is unambiguous, so it is accepted with its source recorded.

    Zero booleans (nothing to report), two or more without the confirmed property
    (no way to tell which is the verdict), or a non-object payload all return None
    so the caller can OMIT ``is_customizable`` rather than guess.

    ``isinstance(v, bool)`` is deliberately the test, and it is the safe direction
    of the bool/int subclass trap: it matches real booleans only, so an integer
    flag such as ``1`` or ``0`` is never promoted to a verdict. (The dangerous
    direction — ``isinstance(v, int)`` silently matching ``True``/``False`` —
    is the one dataverse_get_total_record_counts had to exclude.)
    """
    if isinstance(payload, bool):
        return ("<response body>", payload)
    if not isinstance(payload, dict):
        return None
    verdict = payload.get(_CUSTOMIZABLE_PROPERTY)
    if isinstance(verdict, bool):
        return (_CUSTOMIZABLE_PROPERTY, verdict)
    booleans = [(k, v) for k, v in payload.items() if isinstance(v, bool)]
    if len(booleans) != 1:
        return None
    return booleans[0]


@tool(
    name="dataverse_is_component_customizable",
    annotations={
        "title": "Is Component Customizable",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_is_component_customizable(
    params: IsComponentCustomizableInput, ctx: Context
) -> str:
    """Check whether a solution component can be customized BEFORE trying to edit it.

    Calls the unbound IsComponentCustomizable function. Nothing is read or
    modified — it is a cheap pre-flight check for the metadata write tools
    (dataverse_update_table, dataverse_update_column, dataverse_update_relationship,
    dataverse_update_choice, dataverse_delete_column, ...), which otherwise fail
    late and opaquely when the target belongs to a managed solution that locked it
    down. Run this first when editing anything you did not create yourself.

    Pass the component's own GUID (a table's or column's MetadataId, a form's or
    web resource's record id — NOT a solution id) plus the integer component_type
    code that says what the GUID refers to. The codes are the same set
    dataverse_analyze_dependencies uses (1=Entity, 2=Attribute, 3=Relationship,
    9=OptionSet, 60=SystemForm, 61=WebResource, 300=CanvasApp, ...); the resolved
    name is echoed back as component_type_name so a mismatched code is easy to
    spot.

    The verdict is returned as a top-level is_customizable boolean. Dataverse's
    response was verified live and is flat, carrying exactly one property named
    after the function itself — {"IsComponentCustomizable": true} — which is read
    directly; is_customizable_source names the property the value came from. If a
    future platform version answers in some other shape, a lone boolean anywhere
    in the payload is still accepted as a fallback, and when no verdict can be
    identified unambiguously the key is OMITTED rather than guessed or returned as
    null, with normalized false and a message saying so. Never read a missing
    is_customizable as false. The payload is always echoed unchanged under
    raw_response (minus the @odata.* envelope).

    A false answer means the component belongs to a managed solution whose
    publisher locked it down. A true answer is not a guarantee that every edit will
    succeed: individual managed properties (for example IsRenameable or
    IsValidForAdvancedFind) can still block a specific change on an otherwise
    customizable component.

    DO NOT ASSUME SYSTEM COMPONENTS ANSWER false. Core out-of-the-box tables
    report true (systemuser, component type 1, was verified as true) because the
    platform permits customizations such as adding columns even though the base
    asset itself is managed. The tool does discriminate — a managed web resource
    (type 61) returned false while an unmanaged one returned true.

    A well-formed GUID that matches no component is an HTTP 400 carrying
    [0x80040216] "There should be at least one metadata entity returned for
    EntityName: ...", surfaced through the standard {"error": true, "message": ...}
    envelope: it means the component id (or the component_type paired with it) is
    wrong, not that the component is locked.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    # Parameter aliases, mirroring the RetrieveDependenciesForDelete call in
    # tools/dependencies.py. ComponentId is Edm.Guid and ComponentType is
    # Edm.Int32: an OData Guid literal is written BARE — no surrounding quotes and
    # no guid'...' prefix — so there is no string literal to break out of and
    # encode_odata_literal deliberately does NOT apply here (unlike
    # dataverse_validate_fetchxml, whose Edm.String parameter does need it). The
    # entire defence is the input model: component_id must match _GUID_PATTERN and
    # component_type is a bounded int, so neither value can carry a character that
    # is significant in a URL.
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/IsComponentCustomizable(ComponentId=@cid,ComponentType=@ct)"
        f"?@cid={params.component_id}&@ct={params.component_type}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_is_component_customizable")
    except Exception as e:
        return tool_error_response(e, "dataverse_is_component_customizable")

    # Parse defensively: a non-JSON or empty body is surfaced as-is rather than
    # raising.
    try:
        payload: Any = response.json()
    except ValueError:
        logger.warning("IsComponentCustomizable returned a non-JSON body")
        payload = response.text or None

    body: Any = payload
    if isinstance(payload, dict):
        body = {k: v for k, v in payload.items() if not k.startswith("@odata.")}

    type_name = _resolve_component_type(params.component_type)
    result: dict[str, Any] = {
        "component_id": params.component_id,
        "component_type": params.component_type,
        "component_type_name": type_name,
    }

    found = _extract_verdict(body)
    if found is None:
        logger.warning(
            "IsComponentCustomizable response carried neither a %s property nor a "
            "single boolean value; returning it raw without a verdict",
            _CUSTOMIZABLE_PROPERTY,
        )
        result["normalized"] = False
        result["message"] = (
            "Dataverse answered successfully, but the payload carried neither an "
            f"{_CUSTOMIZABLE_PROPERTY} boolean nor exactly one boolean value, so "
            "the customizability verdict could not be identified. "
            "is_customizable is OMITTED rather than guessed — do not read its "
            "absence as 'not customizable'. The full payload is under "
            "raw_response; read the verdict from there."
        )
    else:
        source, value = found
        result["normalized"] = True
        result["is_customizable"] = value
        result["is_customizable_source"] = source
        result["message"] = (
            (
                f"{type_name} {params.component_id} is customizable. Edits are "
                "expected to be accepted, though individual managed properties "
                "can still block a specific change."
            )
            if value
            else (
                f"{type_name} {params.component_id} is NOT customizable — it "
                "belongs to a managed solution whose publisher locked it down. "
                "Attempting to update or delete it will fail; change a component "
                "you own, or ask the publisher for an unmanaged/customizable "
                "version."
            )
        )

    result["raw_response"] = body
    return finalize_response(result)


# ---------------------------------------------------------------------------
# Shared helpers for unbound metadata functions
# ---------------------------------------------------------------------------


def _is_locale_id_list(value: Any) -> bool:
    """True when *value* is a list of real integers (LCIDs).

    ``isinstance(item, bool)`` is excluded deliberately: ``bool`` is a subclass
    of ``int`` in Python, so ``[True, False]`` would otherwise be accepted as a
    list of locale ids. The exclusion has already been needed twice elsewhere in
    this codebase — never test a value that could be either by truthiness or by
    a bare ``isinstance(x, int)``.
    """
    return isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    )


def _is_name_list(value: Any) -> bool:
    """True when *value* is a list of strings (logical names)."""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _extract_named_list(
    payload: Any,
    preferred_key: str,
    function_name: str,
    is_wanted: Callable[[Any], bool],
) -> tuple[str, list] | None:
    """Locate a list in *payload* as ``(source, items)``, or None.

    Mirrors ``_extract_entry_list`` in tools/security.py. Every round of these
    unbound-function tools has found at least one response whose real shape
    differed from the documented one, so the list is found by shape as well as by
    name, in three tiers:

    1. A bare top-level array (the response body IS the list).
    2. *preferred_key* at the top level — the documented container name.
    3. Exactly ONE list-valued top-level property, whatever it is called.

    Ambiguity (two or more top-level lists without the preferred key) returns
    None, as does a sole list whose items are the wrong type. The caller then
    degrades to ``normalized: false`` plus the raw body. A MISSING container is
    never turned into an empty list: "no container" and "no results" are
    different answers and must not be collapsed. *function_name* only labels the
    warnings.
    """
    if is_wanted(payload):
        return ("<response body>", payload)
    if not isinstance(payload, dict):
        return None

    named = payload.get(preferred_key)
    if is_wanted(named):
        return (preferred_key, named)

    candidates = [(k, v) for k, v in payload.items() if isinstance(v, list)]
    if len(candidates) > 1:
        logger.warning(
            "%s returned %d candidate lists at the top level (%s); refusing to "
            "pick one",
            function_name,
            len(candidates),
            ", ".join(k for k, _ in candidates),
        )
        return None
    if len(candidates) == 1 and is_wanted(candidates[0][1]):
        return candidates[0]
    return None


async def _call_unbound_function(
    app_ctx: Any,
    headers: dict[str, str],
    function_name: str,
    url: str,
    partial_errors: list[dict[str, str]],
    tool_name: str,
) -> Any | None:
    """GET an unbound function, recording a partial error instead of propagating.

    Mirrors ``_call_share_function`` in tools\\security.py. Returns the
    envelope-stripped payload, or None when the call failed — in which case an
    entry is appended to *partial_errors* so one unavailable or privilege-gated
    function cannot fail a whole multi-call tool.
    """
    try:
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError:
            logger.warning("%s returned a non-JSON body", function_name)
            return response.text or None
        if isinstance(payload, dict):
            return {k: v for k, v in payload.items() if not k.startswith("@odata.")}
        return payload
    except httpx.HTTPStatusError as e:
        message = (
            f"Dataverse returned HTTP {e.response.status_code}: "
            f"{extract_error_message(e.response)}"
        )
    except Exception as e:  # network, auth — never fatal for the sibling calls
        message = f"{type(e).__name__}: {e}"
    logger.warning("%s failed during %s: %s", function_name, tool_name, message)
    partial_errors.append({"function": function_name, "message": message})
    return None


# ---------------------------------------------------------------------------
# Language tools
# ---------------------------------------------------------------------------

# Three unbound, zero-parameter GET functions, each answering under a DIFFERENT
# top-level property name — two repeat the function name, one does not:
#
#   RetrieveProvisionedLanguages   -> {"RetrieveProvisionedLanguages": [1033, ...]}
#   RetrieveAvailableLanguages     -> {"LocaleIds": [1033, ...]}
#   RetrieveInstalledLanguagePacks -> {"RetrieveInstalledLanguagePacks": [1033, ...]}
#
# All three container names are LIVE-CONFIRMED (verified against a real org and
# against $metadata; the preferred-key tier fired for all three).
#
# Do NOT "simplify" this table by deriving the property name from the function
# name: that holds for two of the three and silently breaks the third. The
# by-shape fallback in _extract_named_list stays regardless — it is what keeps a
# future shape change from being reported as an empty list.
#
# A FOURTH source exists and is deliberately NOT wrapped here:
# RetrieveDeprovisionedLanguages() answers HTTP 200 under its own name (observed
# returning [] live). Add it only with its own live evidence about what it means.
#
# The function names are module constants and never caller input, so they are
# safe as URL path segments by construction.
_LANGUAGE_FUNCTIONS: tuple[tuple[str, str, str], ...] = (
    ("provisioned", "RetrieveProvisionedLanguages", "RetrieveProvisionedLanguages"),
    ("available", "RetrieveAvailableLanguages", "LocaleIds"),
    (
        "installed_packs",
        "RetrieveInstalledLanguagePacks",
        "RetrieveInstalledLanguagePacks",
    ),
)


@tool(
    name="dataverse_list_languages",
    annotations={
        "title": "List Languages",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_languages(params: ListLanguagesInput, ctx: Context) -> str:
    """List an environment's language codes (LCIDs) — which are usable, which are merely installed.

    Call this before writing any localized label. provisioned is the load-bearing
    answer: those are the LCIDs that are actually ENABLED in this environment and
    therefore the only ones a LocalizedLabels entry may use (the LanguageCode on
    a DisplayName / Description label passed to dataverse_create_table,
    dataverse_create_column, dataverse_create_choice, ...). A LocalizedLabel for a
    language that is installed but NOT provisioned is rejected or silently
    dropped, so never assume 1033 (English) is provisioned — verify it.

    Three unbound, zero-parameter functions are called CONCURRENTLY and their
    answers reconciled. THE THREE SETS CAN BE MUTUALLY DISJOINT — on the org this
    tool was verified against, available was [1033], provisioned was [1033], and
    installed_packs held 44 OTHER LCIDs not including 1033. Read each for what it
    literally reports and do not infer one from another:

    * provisioned (RetrieveProvisionedLanguages) — the LCIDs enabled for use in
      this environment. THE LOAD-BEARING ONE: this is the set a LocalizedLabels
      entry may use, and the only one worth deciding anything from.
    * available (RetrieveAvailableLanguages, container LocaleIds) — what this
      function reports the environment as offering. Observed live as a SHORT list
      that matched provisioned exactly and shared nothing with installed_packs;
      it is NOT "every language whose pack is on the server".
    * installed_packs (RetrieveInstalledLanguagePacks) — the language packs
      present on the server. Observed live as by far the largest of the three and
      DISJOINT from both of the others. A pack being installed does not make its
      LCID provisioned, and this tool does not know which of them could be.

    Each list is echoed exactly as Dataverse sent it (order included, not sorted)
    with a matching *_count. Two derived diffs are computed only when both of
    their inputs were read successfully, and are named after the exact subtraction
    they perform rather than implying one is the actionable answer:
    available_not_provisioned (available minus provisioned) and
    installed_not_provisioned (installed_packs minus provisioned). Neither is a
    list of languages an administrator can simply turn on — provisioning has its
    own prerequisites — so treat both as leads to investigate, not as a to-do
    list.

    THE THREE CALLS FAIL INDEPENDENTLY. A function that is unavailable or
    privilege-gated is reported in partial_errors and the others' data is still
    returned, so ALWAYS read partial_errors before concluding a language is
    absent — a missing key means "not answered", never "empty". Only a failure of
    all three yields the standard {"error": true, "message": ...} envelope.

    The three container property names genuinely differ (LocaleIds for the
    available list; the function's own name for the other two) and all three are
    LIVE-CONFIRMED. Each is still tried by name and then by shape (a sole list of
    integers), so a future change degrades rather than lies. sources reports the
    property each list was actually found under — check it. If a payload cannot
    be read unambiguously, its keys are OMITTED, normalized is false, and the
    untouched body appears under raw_responses; nothing is fabricated and no empty
    list is invented.

    LCIDs are Windows locale ids (1033 = English (United States), 1036 = French
    (France), 1031 = German (Germany), 3082 = Spanish (Spain)); they are returned
    as raw integers and are not mapped to language names here.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
    partial_errors: list[dict[str, str]] = []

    # The whole body is guarded, not just the HTTP calls: CLAUDE.md's "do not
    # raise uncaught exceptions from tools" is unconditional, and the
    # reconciliation below reads payloads whose shapes are only documented.
    try:
        headers = await build_headers(app_ctx, base_url)

        payloads = await asyncio.gather(*[
            _call_unbound_function(
                app_ctx,
                headers,
                function_name,
                # Zero-parameter unbound function: empty parentheses, no query
                # string, and nothing caller-supplied anywhere in the URL.
                f"{api_root}/{function_name}()",
                partial_errors,
                "dataverse_list_languages",
            )
            for _key, function_name, _property_name in _LANGUAGE_FUNCTIONS
        ])

        if len(partial_errors) == len(_LANGUAGE_FUNCTIONS):
            details = "; ".join(
                f"{entry['function']}: {entry['message']}" for entry in partial_errors
            )
            return json.dumps({
                "error": True,
                "message": (
                    "Could not list this environment's languages: all three "
                    f"functions failed. {details}"
                ),
            })

        result: dict[str, Any] = {}
        sources: dict[str, str] = {}
        raw_responses: dict[str, Any] = {}
        locale_lists: dict[str, list[int]] = {}

        for (key, function_name, property_name), payload in zip(
            _LANGUAGE_FUNCTIONS, payloads
        ):
            if payload is None:
                continue  # already recorded in partial_errors
            found = _extract_named_list(
                payload, property_name, function_name, _is_locale_id_list
            )
            if found is None:
                logger.warning(
                    "%s response carried no unambiguous list of locale ids; "
                    "returning it raw",
                    function_name,
                )
                raw_responses[function_name] = payload
                continue
            source, locale_ids = found
            sources[key] = source
            result[key] = locale_ids
            result[f"{key}_count"] = len(locale_ids)
            locale_lists[key] = locale_ids

        # Derived answers, each computed ONLY when both of its inputs were read.
        # A missing input must not be treated as an empty set: that would report
        # every language on the other side as "not provisioned".
        #
        # TWO diffs, each named after the exact subtraction it performs. A single
        # "not_provisioned" was live-misleading: available minus provisioned came
        # out EMPTY on an org carrying 44 installed packs, reporting "nothing to
        # look at" while the largest of the three lists went undiffed entirely.
        available = locale_lists.get("available")
        provisioned = locale_lists.get("provisioned")
        installed = locale_lists.get("installed_packs")
        if available is not None and provisioned is not None:
            available_diff = sorted(set(available) - set(provisioned))
            result["available_not_provisioned"] = available_diff
            result["available_not_provisioned_count"] = len(available_diff)
        if installed is not None and provisioned is not None:
            installed_diff = sorted(set(installed) - set(provisioned))
            result["installed_not_provisioned"] = installed_diff
            result["installed_not_provisioned_count"] = len(installed_diff)

        result["sources"] = sources
        result["normalized"] = not raw_responses
        if raw_responses:
            result["raw_responses"] = raw_responses
            result["message"] = (
                "These functions answered successfully but their payloads carried "
                "no unambiguous list of locale ids, so their keys are OMITTED "
                "rather than guessed and no empty list is implied: "
                f"{', '.join(sorted(raw_responses))}. Read the untouched bodies "
                "under raw_responses."
            )
        result["partial_errors"] = partial_errors

        return finalize_response(result)
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_list_languages")
    except Exception as e:
        return tool_error_response(e, "dataverse_list_languages")


# ---------------------------------------------------------------------------
# Relationship eligibility enumeration
# ---------------------------------------------------------------------------

# Closed allowlist: role -> (function name, parameter name or None).
#
# READ THE PARAMETER NAMES TWICE. They are INVERTED relative to the function
# names, and this is the single likeliest implementation error here:
#
#   GetValidReferencedEntities(ReferencingEntityName='x')
#       -> the tables x can REFERENCE (x holds the lookup)
#   GetValidReferencingEntities(ReferencedEntityName='x')
#       -> the tables that can REFERENCE x (they hold the lookup)
#
# You always name the OTHER side of the relationship: the parameter says which
# table you already have, the function says which side you want back. Do not
# "fix" the apparent mismatch — the pairing below is confirmed against $metadata.
#
# A SWAP WOULD NOT FAIL SILENTLY, contrary to what this comment used to warn.
# These are function OVERLOADS keyed on the parameter name, so an unrecognized
# pairing is not resolved at all: live, GetValidReferencedEntities with
# ReferencedEntityName answers HTTP 404 [0x80060888] "Resource not found for the
# segment 'GetValidReferencedEntities'". The inversion is still counter-intuitive
# enough to invite the edit; it is just loud when made.
#
# The function name lands in a URL PATH SEGMENT and this repo has a confirmed
# live OData key-predicate injection, so the name is SELECTED from this frozen
# table by role and never interpolated from caller input; the build site
# re-checks membership even though the Literal on the input model already
# constrains it.
_RELATIONSHIP_ROLE_FUNCTIONS: dict[str, tuple[str, str | None]] = {
    "referenced": ("GetValidReferencedEntities", "ReferencingEntityName"),
    "referencing": ("GetValidReferencingEntities", "ReferencedEntityName"),
    "many_to_many": ("GetValidManyToMany", None),
}

# All three functions are documented as returning {"EntityNames": ["account", ...]}.
_ENTITY_NAMES_PROPERTY = "EntityNames"


@tool(
    name="dataverse_get_valid_relationship_entities",
    annotations={
        "title": "Get Valid Relationship Entities",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_valid_relationship_entities(
    params: GetValidRelationshipEntitiesInput, ctx: Context
) -> str:
    """List WHICH tables may take part in a relationship — the enumeration, before you pick a target.

    Answers "which tables are eligible?" when you do not yet know what to point a
    lookup at. Its counterpart dataverse_check_relationship_eligibility answers
    "is THIS ONE table OK?" — a boolean about a table you can already name. The
    role values here mirror that tool's check_type values one for one, so use
    this to discover a candidate and that one to confirm a specific choice.

    Call it before dataverse_create_one_to_many_relationship or
    dataverse_create_many_to_many_relationship: a table the platform excludes
    (many system and virtual tables) fails the create late and opaquely.

    role selects one of three unbound functions:

    * 'referenced' — tables that can be the PRIMARY (one) side of a 1:N, i.e.
      valid lookup TARGETS.
    * 'referencing' — tables that can be the RELATED (many) side of a 1:N, i.e.
      tables that can HOLD a lookup.
    * 'many_to_many' — tables that can participate in an N:N. Takes no
      table_logical_name; supplying one is an input error rather than being
      ignored, because ignoring it would answer the environment-wide question
      while looking scoped.

    table_logical_name DOES NOT NARROW THE ANSWER. It is optional for the two 1:N
    roles and Dataverse does validate it server-side (an unknown table is HTTP
    400 [0x80041102] "not found in the MetadataCache"), but supplying it was
    measured live to return a BYTE-IDENTICAL list to omitting it, for every table
    tried. Every role therefore answers the environment-wide question: the tables
    eligible for that role at all. Passing a name buys you exactly one thing —
    proof the table exists — so pass it only when you want that check, and never
    read the result as "the tables THIS table may point at". This holds for
    CUSTOM tables as well as system ones: scoping by a custom table returned the
    same byte-identical 575-name list that 'account' and 'systemuser' did.
    The response says so explicitly via table_logical_name_filtered.

    To ask about one specific table, use
    dataverse_check_relationship_eligibility, which returns a real per-table
    boolean. This tool cannot answer that question.

    Supply a lowercase logical name ('account', not 'accounts'). Omitting it
    removes the parameter from the call entirely rather than sending an empty one
    — the two are different requests, even though they answer the same.

    THE ANSWER IS BIG and 'referenced' is the biggest. Measured live:
    referenced 575 names / ~13 KB, many_to_many 305 / ~7 KB, referencing 166 /
    ~4 KB. None of these functions pages server-side, so names are trimmed to top
    (default 250) while count, total_count and has_more always describe the full
    set Dataverse returned.

    The list is returned under EntityNames (live-confirmed for all three
    functions), which is tried first, then a by-shape fallback (a sole top-level
    list of strings); source names where it was actually found. If it cannot be
    located unambiguously, table_logical_names and the counts are OMITTED,
    normalized is false, and the untouched body is returned under raw_response —
    an unreadable payload is never reported as an empty list.

    An empty list from a readable payload IS a real answer: it means no table
    qualifies for that role. A nonexistent table name is an HTTP error, not an
    empty list.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"

    # Defence in depth: the Literal on the input model already constrains role,
    # but the function name goes into a URL PATH SEGMENT, so membership of the
    # frozen allowlist is re-checked here. This survives the model being bypassed
    # or loosened later.
    selected = _RELATIONSHIP_ROLE_FUNCTIONS.get(params.role)
    if selected is None:
        return json.dumps({
            "error": True,
            "message": (
                f"Unsupported role {params.role!r}. Valid roles: "
                f"{', '.join(sorted(_RELATIONSHIP_ROLE_FUNCTIONS))}."
            ),
        })
    function_name, parameter_name = selected

    if parameter_name is None and params.table_logical_name is not None:
        # Unreachable through the input model (its model_validator rejects this),
        # kept so a bypassed model can never silently drop the scope.
        return json.dumps({
            "error": True,
            "message": (
                f"{function_name} takes no parameter, so table_logical_name cannot "
                f"be applied to role={params.role!r}. Omit it, or use "
                "dataverse_check_relationship_eligibility to ask about one table."
            ),
        })

    # Two branches, never one template with an optional hole: an OPTIONAL OData
    # function parameter is omitted from the parameter list in the URL PATH as
    # well as from the query string, and `Fn(Name=@p1)?@p1=''` is a DIFFERENT
    # request from `Fn()`. The test is `is not None`, never truthiness — the
    # model rejects an empty string, and an empty string must not be silently
    # read as "omitted" if it ever reached here.
    if parameter_name is not None and params.table_logical_name is not None:
        # ReferencingEntityName / ReferencedEntityName are Edm.String, so the
        # value is a SINGLE-QUOTED OData literal: the two delimiting quotes stay
        # bare and everything inside goes through encode_odata_literal (double
        # any ' first, then percent-encode). This is the same regime as
        # RetrieveSetting, and NOT the JSON-alias regime used for
        # Collection(Edm.String) / EntityReference parameters, where the literal
        # escaper would corrupt the value.
        #
        # The alias name is written literally as @p1 rather than via urlencode,
        # exactly as the shipped Edm.String-alias tools do; that also keeps it
        # from becoming %40p1 (which Dataverse would not recognize as an alias)
        # and avoids double-encoding the output of encode_odata_literal.
        #
        # Both layers apply: _DATAVERSE_NAME_PATTERN at the model means the value
        # cannot express a quote at all, and the escaper here means the URL stays
        # safe even if that pattern is loosened later. That pairing is exactly
        # what the confirmed key-predicate injection in this repo lacked.
        table_enc = encode_odata_literal(params.table_logical_name)
        url = f"{api_root}/{function_name}({parameter_name}=@p1)?@p1='{table_enc}'"
    else:
        url = f"{api_root}/{function_name}()"

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_get_valid_relationship_entities")
    except Exception as e:
        return tool_error_response(e, "dataverse_get_valid_relationship_entities")

    try:
        # A non-JSON or empty body is surfaced as-is rather than raising.
        try:
            payload: Any = response.json()
        except ValueError:
            logger.warning("%s returned a non-JSON body", function_name)
            payload = response.text or None

        body: Any = payload
        if isinstance(payload, dict):
            body = {k: v for k, v in payload.items() if not k.startswith("@odata.")}

        result: dict[str, Any] = {
            "role": params.role,
            "table_logical_name": params.table_logical_name,
            "function": function_name,
            # The (inverted) parameter this function takes — null for
            # GetValidManyToMany, which takes none. It is echoed even when the
            # call omitted it, so a caller can see which side would be named.
            "parameter": parameter_name,
            # ALWAYS false, and deliberately so. This replaced a `scope` field
            # that reported "table" vs "environment": measured live, supplying
            # table_logical_name returns a byte-identical list to omitting it, so
            # "table" was a claim the data did not support. Sending the parameter
            # still validates the name server-side; it just does not filter.
            # Flip this to a real computation only if a live run ever shows the
            # scoped and unscoped answers differing.
            "table_logical_name_filtered": False,
        }
        if params.table_logical_name is not None:
            result["table_logical_name_note"] = (
                f"{function_name} accepted and validated "
                f"{params.table_logical_name!r} (an unknown table would be an HTTP "
                "400), but it did NOT narrow the result: this is the same "
                "environment-wide list of tables eligible for "
                f"role={params.role!r} that omitting it returns. Do not read it as "
                "the tables related to this one — use "
                "dataverse_check_relationship_eligibility for a per-table answer."
            )

        found = _extract_named_list(
            body, _ENTITY_NAMES_PROPERTY, function_name, _is_name_list
        )
        if found is None:
            logger.warning(
                "%s response carried no unambiguous list of table names; "
                "returning it raw",
                function_name,
            )
            result["normalized"] = False
            result["message"] = (
                f"{function_name} answered successfully, but no unambiguous list "
                f"of table names could be located (expected an "
                f"{_ENTITY_NAMES_PROPERTY} array). No names and no counts are "
                "reported and none are guessed — do NOT read this as 'no eligible "
                "tables'. The response is returned unchanged (minus the @odata.* "
                "envelope) under raw_response; read the list from there."
            )
            result["raw_response"] = body
            return finalize_response(result)

        source, all_names = found
        page = all_names[: params.top]
        result["normalized"] = True
        result["source"] = source
        result["count"] = len(page)
        result["total_count"] = len(all_names)
        result["has_more"] = len(all_names) > len(page)
        result["table_logical_names"] = page
        if result["has_more"]:
            result["message"] = (
                f"{function_name} returned {len(all_names)} table names; the first "
                f"{len(page)} are included. Raise top (max 5000) to see more, or "
                "use dataverse_check_relationship_eligibility to test one specific "
                "table instead of scanning this list."
            )
        return finalize_response(result)
    except Exception as e:
        return tool_error_response(e, "dataverse_get_valid_relationship_entities")
