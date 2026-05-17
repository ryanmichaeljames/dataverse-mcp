"""Solution-related tools for the Dataverse MCP server."""

import json
import logging
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import mcp, write_tool
from dataverse_mcp.client import (
    AppContext,
    _DATAVERSE_API_VERSION,
    build_headers,
    extract_error_message,
    paginate_records,
    resolve_base_url,
)
from dataverse_mcp.models import (
    AddComponentToSolutionInput,
    CreatePublisherInput,
    CreateSolutionInput,
    GetSolutionInput,
    ListSolutionComponentsInput,
    ListSolutionsInput,
    RemoveComponentFromSolutionInput,
    UpdatePublisherInput,
    UpdateSolutionInput,
    UpdateSolutionVersionInput,
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


def _escape_odata_string(value: str) -> str:
    return value.replace("'", "''")


async def _resolve_solution_record(
    app_ctx: AppContext,
    base_url: str,
    headers: dict[str, str],
    solution_id: str | None,
    solution_unique_name: str | None,
) -> dict | None:
    """Resolve a solution by ID or unique name and return key fields."""
    select = "solutionid,uniquename"

    if solution_id:
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions({solution_id})"
            f"?$select={select}"
        )
        resp = await app_ctx.http_client.get(url, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    escaped_name = _escape_odata_string(solution_unique_name or "")
    query_params = {
        "$select": select,
        "$filter": f"uniquename eq '{escaped_name}'",
        "$top": "1",
    }
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"
    records = await paginate_records(full_url, headers, 1, app_ctx.http_client)
    return records[0] if records else None


def _solution_not_found_message(
    solution_id: str | None, solution_unique_name: str | None
) -> str:
    identifier = solution_unique_name or solution_id
    return f"Solution not found: '{identifier}'"


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
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
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
    headers = await build_headers(app_ctx, base_url)

    try:
        if params.solution_id:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions({params.solution_id})"
                f"?$select={','.join(select)}"
            )
            resp = await app_ctx.http_client.get(url, headers=headers)
            resp.raise_for_status()
            record = resp.json()
        else:
            escaped_name = params.solution_unique_name.replace("'", "''")
            query_params = {
                "$select": ",".join(select),
                "$filter": f"uniquename eq '{escaped_name}'",
                "$top": "1",
            }
            url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions"
            full_url = f"{url}?{urlencode(query_params, safe='$,')}"
            records = await paginate_records(full_url, headers, 1, app_ctx.http_client)
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
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
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


@write_tool(
    name="dataverse_create_publisher",
    annotations={
        "title": "Create Publisher",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_publisher(params: CreatePublisherInput, ctx: Context) -> str:
    """Create a Dataverse publisher."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body = {
        "uniquename": params.uniquename,
        "friendlyname": params.display_name,
        "customizationprefix": params.customization_prefix,
        "customizationoptionvalueprefix": params.option_value_prefix,
    }

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/publishers"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await app_ctx.http_client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "uniquename": params.uniquename,
            "display_name": params.display_name,
            "location": location,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_create_publisher")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_update_publisher",
    annotations={
        "title": "Update Publisher",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_publisher(params: UpdatePublisherInput, ctx: Context) -> str:
    """Update mutable publisher fields."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict[str, object] = {}
    if params.display_name is not None:
        body["friendlyname"] = params.display_name
    if params.customization_prefix is not None:
        body["customizationprefix"] = params.customization_prefix
    if params.option_value_prefix is not None:
        body["customizationoptionvalueprefix"] = params.option_value_prefix

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/publishers({params.publisher_id})"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await app_ctx.http_client.patch(url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({"updated": True, "publisher_id": params.publisher_id})
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_update_publisher")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_create_solution",
    annotations={
        "title": "Create Solution",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_solution(params: CreateSolutionInput, ctx: Context) -> str:
    """Create a Dataverse solution."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict[str, object] = {
        "uniquename": params.solution_unique_name,
        "friendlyname": params.display_name,
        "version": params.version,
        "publisherid@odata.bind": f"/publishers({params.publisher_id})",
    }
    if params.description is not None:
        body["description"] = params.description

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await app_ctx.http_client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "solution_unique_name": params.solution_unique_name,
            "version": params.version,
            "location": location,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_create_solution")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_update_solution",
    annotations={
        "title": "Update Solution",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_solution(params: UpdateSolutionInput, ctx: Context) -> str:
    """Update mutable solution fields."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict[str, object] = {}
    if params.display_name is not None:
        body["friendlyname"] = params.display_name
    if params.description is not None:
        body["description"] = params.description
    if params.publisher_id is not None:
        body["publisherid@odata.bind"] = f"/publishers({params.publisher_id})"

    try:
        headers = await build_headers(app_ctx, base_url)
        solution = await _resolve_solution_record(
            app_ctx,
            base_url,
            headers,
            params.solution_id,
            params.solution_unique_name,
        )
        if solution is None:
            return json.dumps({
                "error": True,
                "message": _solution_not_found_message(
                    params.solution_id, params.solution_unique_name
                ),
            })

        target_solution_id = solution.get("solutionid")
        if not target_solution_id:
            return json.dumps({
                "error": True,
                "message": "Resolved solution is missing solutionid",
            })

        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions({target_solution_id})"
        )
        resp = await app_ctx.http_client.patch(url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({
            "updated": True,
            "solution_id": target_solution_id,
            "solution_unique_name": solution.get("uniquename"),
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_update_solution")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_update_solution_version",
    annotations={
        "title": "Update Solution Version",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_solution_version(
    params: UpdateSolutionVersionInput, ctx: Context
) -> str:
    """Update solution version only."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        solution = await _resolve_solution_record(
            app_ctx,
            base_url,
            headers,
            params.solution_id,
            params.solution_unique_name,
        )
        if solution is None:
            return json.dumps({
                "error": True,
                "message": _solution_not_found_message(
                    params.solution_id, params.solution_unique_name
                ),
            })

        target_solution_id = solution.get("solutionid")
        if not target_solution_id:
            return json.dumps({
                "error": True,
                "message": "Resolved solution is missing solutionid",
            })

        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutions({target_solution_id})"
        )
        resp = await app_ctx.http_client.patch(
            url,
            json={"version": params.version},
            headers=headers,
        )
        resp.raise_for_status()
        return json.dumps({
            "updated": True,
            "solution_id": target_solution_id,
            "solution_unique_name": solution.get("uniquename"),
            "version": params.version,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_update_solution_version")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_add_component_to_solution",
    annotations={
        "title": "Add Component To Solution",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_add_component_to_solution(
    params: AddComponentToSolutionInput, ctx: Context
) -> str:
    """Add a component to a solution via AddSolutionComponent action."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    action_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/AddSolutionComponent"

    try:
        headers = await build_headers(app_ctx, base_url)
        solution = await _resolve_solution_record(
            app_ctx,
            base_url,
            headers,
            params.solution_id,
            params.solution_unique_name,
        )
        if solution is None:
            return json.dumps({
                "error": True,
                "message": _solution_not_found_message(
                    params.solution_id, params.solution_unique_name
                ),
            })

        solution_unique_name = solution.get("uniquename")
        if not solution_unique_name:
            return json.dumps({
                "error": True,
                "message": "Resolved solution is missing uniquename",
            })

        body = {
            "ComponentId": params.component_id,
            "ComponentType": params.component_type,
            "SolutionUniqueName": solution_unique_name,
            "AddRequiredComponents": params.add_required_components,
            "DoNotIncludeSubcomponents": params.do_not_include_subcomponents,
        }

        resp = await app_ctx.http_client.post(action_url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({
            "added": True,
            "solution_unique_name": solution_unique_name,
            "solution_id": solution.get("solutionid"),
            "component_id": params.component_id,
            "component_type": params.component_type,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_add_component_to_solution")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_remove_component_from_solution",
    annotations={
        "title": "Remove Component From Solution",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_remove_component_from_solution(
    params: RemoveComponentFromSolutionInput, ctx: Context
) -> str:
    """Remove a component from a solution via RemoveSolutionComponent action."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    action_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/RemoveSolutionComponent"

    try:
        headers = await build_headers(app_ctx, base_url)
        solution = await _resolve_solution_record(
            app_ctx,
            base_url,
            headers,
            params.solution_id,
            params.solution_unique_name,
        )
        if solution is None:
            return json.dumps({
                "error": True,
                "message": _solution_not_found_message(
                    params.solution_id, params.solution_unique_name
                ),
            })

        solution_unique_name = solution.get("uniquename")
        if not solution_unique_name:
            return json.dumps({
                "error": True,
                "message": "Resolved solution is missing uniquename",
            })

        solution_id = solution.get("solutionid")
        body = {
            # RemoveSolutionComponent expects a solutioncomponent entity parameter,
            # but Dataverse resolves the underlying ComponentId from this key value.
            "SolutionComponent": {"solutioncomponentid": params.component_id},
            "ComponentType": params.component_type,
            "SolutionUniqueName": solution_unique_name,
        }

        resp = await app_ctx.http_client.post(action_url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({
            "removed": True,
            "solution_unique_name": solution_unique_name,
            "solution_id": solution_id,
            "solution_component_id": params.component_id,
            "component_id": params.component_id,
            "component_type": params.component_type,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_remove_component_from_solution")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
