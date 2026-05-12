"""Solution-related tools for the Dataverse MCP server."""

import asyncio
import json
import logging
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import mcp
from dataverse_mcp.client import (
    AppContext,
    _DATAVERSE_API_VERSION,
    build_headers,
    extract_error_message,
    paginate_records,
    resolve_base_url,
)
from dataverse_mcp.models import (
    GetSolutionInput,
    ListSolutionComponentsInput,
    ListSolutionsInput,
)

logger = logging.getLogger(__name__)

# Solution component type code → display name
# https://learn.microsoft.com/en-us/power-apps/developer/data-platform/reference/entities/solutioncomponent
COMPONENT_TYPE_NAMES: dict[int, str] = {
    1: "Entity",
    2: "Attribute",
    3: "Relationship",
    9: "Option Set",
    10: "Entity Relationship",
    20: "Security Role",
    26: "Saved Query",
    29: "Workflow",
    59: "Saved Query Visualization",
    60: "System Form",
    61: "Web Resource",
    62: "Site Map",
    63: "Connection Role",
    66: "Custom Control",
    70: "Field Security Profile",
    90: "Plugin Type",
    91: "Plugin Assembly",
    92: "SDK Message Processing Step",
    300: "Canvas App",
    371: "Connector",
    372: "Environment Variable Definition",
    373: "Environment Variable Value",
    380: "AI Project Type",
    381: "AI Project",
    382: "AI Configuration",
}

_DEFAULT_SOLUTION_SELECT = [
    "solutionid",
    "uniquename",
    "friendlyname",
    "version",
    "ismanaged",
    "installedon",
    "modifiedon",
    "description",
]

_DEFAULT_COMPONENT_SELECT = [
    "solutioncomponentid",
    "componenttype",
    "objectid",
    "rootcomponentbehavior",
]


def _get_app_ctx(ctx: Context) -> AppContext:
    return ctx.request_context.lifespan_context


def _enrich_component_type(record: dict) -> dict:
    """Add component_type_name to a solution component record."""
    comp_type = record.get("componenttype")
    if comp_type is not None:
        record["componenttype_name"] = COMPONENT_TYPE_NAMES.get(
            comp_type, f"Unknown ({comp_type})"
        )
    return record


@mcp.tool(
    name="dataverse_list_solutions",
    annotations={
        "title": "List Solutions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_solutions(params: ListSolutionsInput, ctx: Context) -> str:
    """List solutions in the specified Dataverse environment.

    Returns solutions with their unique name, friendly name, version, and
    managed status. Use the optional filter parameter to narrow results
    (e.g., "ismanaged eq false" for unmanaged solutions only).

    Use this tool to discover which solutions exist before drilling into
    specific solution details or components.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_SOLUTION_SELECT
    top = params.top
    query_params: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
    }
    if params.filter:
        query_params["$filter"] = params.filter

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    try:
        headers = await asyncio.to_thread(build_headers, app_ctx, base_url)
        records = await asyncio.to_thread(paginate_records, full_url, headers, top)
        return json.dumps({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_solutions")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_solution",
    annotations={
        "title": "Get Solution",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_solution(params: GetSolutionInput, ctx: Context) -> str:
    """Retrieve a single solution by its unique name or ID.

    Provide either solution_unique_name or solution_id (not both).
    Returns full solution details including version, publisher, and
    managed status.

    Use this after dataverse_list_solutions to get full details for
    a specific solution.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_SOLUTION_SELECT
    headers = await asyncio.to_thread(build_headers, app_ctx, base_url)

    try:
        if params.solution_id:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions({params.solution_id})"
                f"?$select={','.join(select)}"
            )

            def _request() -> dict:
                with httpx.Client(timeout=30.0) as http_client:
                    resp = http_client.get(url, headers=headers)
                    resp.raise_for_status()
                    return resp.json()

            record = await asyncio.to_thread(_request)
        else:
            escaped_name = params.solution_unique_name.replace("'", "''")
            query_params = {
                "$select": ",".join(select),
                "$filter": f"uniquename eq '{escaped_name}'",
                "$top": "1",
            }
            url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions"
            full_url = f"{url}?{urlencode(query_params, safe='$,')}"
            records = await asyncio.to_thread(paginate_records, full_url, headers, 1)
            record = records[0] if records else None

        if record is None:
            identifier = params.solution_unique_name or params.solution_id
            return json.dumps({
                "error": True,
                "message": f"Solution not found: '{identifier}'",
            })

        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_solution")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_list_solution_components",
    annotations={
        "title": "List Solution Components",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_solution_components(
    params: ListSolutionComponentsInput, ctx: Context
) -> str:
    """List components within a specific solution.

    Returns the components (entities, web resources, workflows, etc.) that belong
    to the specified solution. Each component includes both the integer type code
    and a human-readable type name.

    Use component_type to filter by a specific type code (e.g., 1=Entity,
    61=Web Resource, 300=Canvas App). Use dataverse_get_solution first to
    find the solution_id.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    top = params.top
    odata_filter = f"_solutionid_value eq '{params.solution_id}'"
    if params.component_type is not None:
        odata_filter += f" and componenttype eq {params.component_type}"

    query_params = {
        "$select": ",".join(_DEFAULT_COMPONENT_SELECT),
        "$filter": odata_filter,
        "$top": str(top),
    }
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutioncomponents"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    try:
        headers = await asyncio.to_thread(build_headers, app_ctx, base_url)
        records = await asyncio.to_thread(paginate_records, full_url, headers, top)
        records = [_enrich_component_type(r) for r in records]
        return json.dumps({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_solution_components")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
