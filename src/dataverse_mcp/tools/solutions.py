"""Solution-related tools for the Dataverse MCP server."""

import asyncio
import json
import logging

from mcp.server.fastmcp import Context
from PowerPlatform.Dataverse.core.errors import DataverseError, HttpError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import (
    GetSolutionInput,
    ListSolutionComponentsInput,
    ListSolutionsInput,
)
from dataverse_mcp._app import mcp

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


def _get_client(ctx: Context):
    """Extract the DataverseClient from the FastMCP lifespan context."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    return app_ctx.client


def _flatten_records(pages, limit: int) -> list[dict]:
    """Flatten paginated Record results into a list of dicts, up to limit."""
    records = []
    for page in pages:
        for record in page:
            records.append(dict(record))
            if len(records) >= limit:
                return records
    return records


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
    """List solutions in the connected Dataverse environment.

    Returns solutions with their unique name, friendly name, version, and
    managed status. Use the optional filter parameter to narrow results
    (e.g., "ismanaged eq false" for unmanaged solutions only).

    Use this tool to discover which solutions exist before drilling into
    specific solution details or components.
    """
    client = _get_client(ctx)
    select = params.select or _DEFAULT_SOLUTION_SELECT
    top = params.top

    try:

        def _query():
            pages = client.records.get(
                "solution",
                select=select,
                filter=params.filter,
                top=top,
            )
            return _flatten_records(pages, top)

        records = await asyncio.to_thread(_query)
        return json.dumps({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
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
    client = _get_client(ctx)
    select = params.select or _DEFAULT_SOLUTION_SELECT

    try:

        def _query():
            if params.solution_id:
                return dict(
                    client.records.get(
                        "solution",
                        record_id=params.solution_id,
                        select=select,
                    )
                )
            # Query by unique name — escape single quotes for OData safety
            escaped_name = params.solution_unique_name.replace("'", "''")
            odata_filter = f"uniquename eq '{escaped_name}'"
            pages = client.records.get(
                "solution",
                select=select,
                filter=odata_filter,
                top=1,
            )
            results = _flatten_records(pages, 1)
            return results[0] if results else None

        record = await asyncio.to_thread(_query)

        if record is None:
            identifier = params.solution_unique_name or params.solution_id
            return json.dumps({
                "error": True,
                "message": f"Solution not found: '{identifier}'",
            })

        return json.dumps({"record": record})
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

    Returns the components (entities, web resources, workflows, etc.)
    that belong to the specified solution. Each component includes both
    the integer type code and a human-readable type name.

    Use component_type to filter by a specific type (e.g., 1 for Entity,
    61 for Web Resource, 300 for Canvas App). Use dataverse_get_solution
    first to find the solution_id.
    """
    client = _get_client(ctx)
    top = params.top

    # solution_id is already GUID-validated by Pydantic
    odata_filter = f"_solutionid_value eq '{params.solution_id}'"
    if params.component_type is not None:
        odata_filter += f" and componenttype eq {params.component_type}"

    try:

        def _query():
            pages = client.records.get(
                "solutioncomponent",
                select=_DEFAULT_COMPONENT_SELECT,
                filter=odata_filter,
                top=top,
            )
            records = _flatten_records(pages, top)
            return [_enrich_component_type(r) for r in records]

        records = await asyncio.to_thread(_query)
        return json.dumps({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
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
        logger.exception("Unexpected error in dataverse_list_solution_components")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
