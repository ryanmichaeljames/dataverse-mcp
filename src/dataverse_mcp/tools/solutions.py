"""Solution-related tools for the Dataverse MCP server."""

import json
import logging
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import delete_tool, mcp, write_tool
from dataverse_mcp.batch import build_batch_body, parse_batch_response
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
    BatchSetCloudFlowsStateInput,
    CreatePublisherInput,
    CreateSolutionInput,
    GetSolutionInput,
    ListCloudFlowsInput,
    ListSolutionComponentsInput,
    ListSolutionsInput,
    RemoveComponentFromSolutionInput,
    SetCloudFlowStateInput,
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

_DEFAULT_CLOUD_FLOW_SELECT = [
    "workflowid",
    "name",
    "category",
    "statecode",
    "statuscode",
    "type",
    "ondemand",
    "createdon",
    "modifiedon",
]

_CLOUD_FLOW_COMPONENT_TYPE = 29
_CLOUD_FLOW_CATEGORY_FILTER = "category eq 5"


def _combine_filters(*filters: str | None) -> str | None:
    active = [f"({f})" for f in filters if f]
    if not active:
        return None
    return " and ".join(active)


def _resolve_flow_state_values(enabled: bool, statuscode: int | None) -> tuple[int, int]:
    statecode = 1 if enabled else 0
    if statuscode is None:
        # Workflow defaults: 1=Draft, 2=Activated
        statuscode = 2 if enabled else 1
    return statecode, statuscode


async def _set_cloud_flow_state(
    app_ctx: AppContext,
    base_url: str,
    headers: dict[str, str],
    flow_id: str,
    enabled: bool,
    statuscode: int | None,
) -> dict:
    statecode, target_statuscode = _resolve_flow_state_values(enabled, statuscode)
    patch_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/workflows({flow_id})"

    try:
        patch_resp = await app_ctx.http_client.patch(
            patch_url,
            json={"statecode": statecode, "statuscode": target_statuscode},
            headers=headers,
            timeout=120.0,
        )
        patch_resp.raise_for_status()
        return {
            "updated": True,
            "flow_id": flow_id,
            "enabled": enabled,
            "statecode": statecode,
            "statuscode": target_statuscode,
            "method": "patch",
        }
    except httpx.HTTPStatusError as patch_error:
        patch_message = extract_error_message(patch_error.response)

    # Fallback for orgs requiring SetState message instead of direct PATCH.
    set_state_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/SetState"
    set_state_body = {
        "EntityMoniker": {
            "@odata.type": "Microsoft.Dynamics.CRM.workflow",
            "workflowid": flow_id,
        },
        "State": {"Value": statecode},
        "Status": {"Value": target_statuscode},
    }
    try:
        action_resp = await app_ctx.http_client.post(
            set_state_url,
            json=set_state_body,
            headers=headers,
            timeout=120.0,
        )
        action_resp.raise_for_status()
        return {
            "updated": True,
            "flow_id": flow_id,
            "enabled": enabled,
            "statecode": statecode,
            "statuscode": target_statuscode,
            "method": "set_state",
        }
    except httpx.HTTPStatusError as action_error:
        action_message = extract_error_message(action_error.response)
        return {
            "error": True,
            "message": (
                f"Failed to update cloud flow state. "
                f"PATCH attempt: {patch_message}. "
                f"SetState attempt: {action_message}."
            ),
        }


async def _list_solution_cloud_flow_ids(
    app_ctx: AppContext,
    base_url: str,
    headers: dict[str, str],
    solution_id: str | None,
    solution_unique_name: str | None,
) -> tuple[str, list[str]]:
    solution = await _resolve_solution_record(
        app_ctx,
        base_url,
        headers,
        solution_id,
        solution_unique_name,
    )
    if solution is None:
        raise ValueError(_solution_not_found_message(solution_id, solution_unique_name))

    resolved_solution_id = solution.get("solutionid")
    if not resolved_solution_id:
        raise ValueError("Resolved solution is missing solutionid")

    query_params = {
        "$select": "objectid",
        "$filter": (
            f"_solutionid_value eq '{resolved_solution_id}' and "
            f"componenttype eq {_CLOUD_FLOW_COMPONENT_TYPE}"
        ),
        "$top": "5000",
    }
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutioncomponents"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    components = await paginate_records(full_url, headers, 5000, app_ctx.http_client)
    flow_ids: list[str] = []
    seen: set[str] = set()
    for component in components:
        object_id = component.get("objectid")
        if not object_id:
            continue
        lower_id = object_id.lower()
        if lower_id in seen:
            continue
        seen.add(lower_id)
        flow_ids.append(object_id)

    return resolved_solution_id, flow_ids


async def _execute_cloud_flow_state_batch(
    app_ctx: AppContext,
    base_url: str,
    flow_ids: list[str],
    enabled: bool,
    statuscode: int | None,
    continue_on_error: bool,
) -> dict:
    statecode, target_statuscode = _resolve_flow_state_values(enabled, statuscode)
    operations = [
        SimpleNamespace(
            method="PATCH",
            url=f"/workflows({flow_id})",
            body={"statecode": statecode, "statuscode": target_statuscode},
            # Use one-op change sets so a single failing flow does not roll back
            # all other flow updates in the same batch request.
            change_set_id=f"cloud_flows_state_{idx}",
        )
        for idx, flow_id in enumerate(flow_ids)
    ]

    batch_boundary = "batch_cloud_flows_state"
    batch_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/$batch"
    batch_body = build_batch_body(operations, base_url, batch_boundary)
    headers = await build_headers(app_ctx, base_url)
    request_headers = {
        **headers,
        "Content-Type": f"multipart/mixed; boundary={batch_boundary}",
        "Accept": "multipart/mixed",
    }
    if continue_on_error:
        request_headers["Prefer"] = "odata.continue-on-error"

    response = await app_ctx.http_client.post(
        batch_url,
        headers=request_headers,
        content=batch_body.encode("utf-8"),
        timeout=120.0,
    )
    if response.status_code not in (200, 202):
        try:
            response_error = response.json()
        except Exception:
            response_error = response.text
        return {
            "error": True,
            "message": f"HTTP {response.status_code}: {response_error}",
        }

    content_type = response.headers.get("Content-Type", "")
    response_boundary = batch_boundary
    if "boundary=" in content_type:
        response_boundary = content_type.split("boundary=")[1].split(";")[0].strip()

    parsed_results = parse_batch_response(response.text, response_boundary)
    results = []
    for idx, item in enumerate(parsed_results):
        flow_id = flow_ids[idx] if idx < len(flow_ids) else None
        status_code = item.get("status_code", 0)
        results.append({
            "index": idx,
            "flow_id": flow_id,
            "status_code": status_code,
            "ok": 200 <= status_code < 300,
            "body": item.get("body"),
        })

    if len(results) < len(flow_ids):
        for idx in range(len(results), len(flow_ids)):
            results.append({
                "index": idx,
                "flow_id": flow_ids[idx],
                "status_code": 0,
                "ok": False,
                "body": {"error": True, "message": "No batch result returned"},
            })

    succeeded = sum(1 for item in results if item.get("ok"))
    failed = len(results) - succeeded
    return {
        "results": results,
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "enabled": enabled,
        "statecode": statecode,
        "statuscode": target_statuscode,
    }


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


@mcp.tool(
    name="dataverse_get_cloud_flows",
    annotations={
        "title": "Get Cloud Flows",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_cloud_flows(params: ListCloudFlowsInput, ctx: Context) -> str:
    """Get cloud flows by query and optionally scoped to a solution."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_CLOUD_FLOW_SELECT
    top = params.top

    try:
        headers = await build_headers(app_ctx, base_url)
        base_filter = _combine_filters(_CLOUD_FLOW_CATEGORY_FILTER, params.filter)

        if not params.solution_id and not params.solution_unique_name:
            query_params = {
                "$select": ",".join(select),
                "$top": str(top),
            }
            if base_filter:
                query_params["$filter"] = base_filter

            url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/workflows"
            full_url = f"{url}?{urlencode(query_params, safe='$,')}"
            records = await paginate_records(full_url, headers, top, app_ctx.http_client)
            return json.dumps({
                "records": records,
                "count": len(records),
                "has_more": len(records) >= top,
            })

        resolved_solution_id, flow_ids = await _list_solution_cloud_flow_ids(
            app_ctx,
            base_url,
            headers,
            params.solution_id,
            params.solution_unique_name,
        )
        if not flow_ids:
            return json.dumps({
                "records": [],
                "count": 0,
                "has_more": False,
                "solution_id": resolved_solution_id,
            })

        records: list[dict] = []
        remaining = top
        chunk_size = 100
        for i in range(0, len(flow_ids), chunk_size):
            if remaining <= 0:
                break
            chunk = flow_ids[i : i + chunk_size]
            chunk_filter = " or ".join([f"workflowid eq '{flow_id}'" for flow_id in chunk])
            query_params = {
                "$select": ",".join(select),
                "$top": str(remaining),
                "$filter": _combine_filters(base_filter, chunk_filter) or chunk_filter,
            }
            url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/workflows"
            full_url = f"{url}?{urlencode(query_params, safe='$,')}"
            chunk_records = await paginate_records(
                full_url, headers, remaining, app_ctx.http_client
            )
            records.extend(chunk_records)
            remaining = top - len(records)

        return json.dumps({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
            "solution_id": resolved_solution_id,
        })
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_cloud_flows")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_enable_cloud_flow",
    annotations={
        "title": "Enable Cloud Flow",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_enable_cloud_flow(
    params: SetCloudFlowStateInput, ctx: Context
) -> str:
    """Enable a cloud flow."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
        headers = await build_headers(app_ctx, base_url)
        result = await _set_cloud_flow_state(
            app_ctx,
            base_url,
            headers,
            params.flow_id,
            enabled=True,
            statuscode=params.statuscode,
        )
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})
    except httpx.TimeoutException:
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": "Request timed out; verify flow state before retrying",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_enable_cloud_flow")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_disable_cloud_flow",
    annotations={
        "title": "Disable Cloud Flow",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_disable_cloud_flow(
    params: SetCloudFlowStateInput, ctx: Context
) -> str:
    """Disable a cloud flow."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
        headers = await build_headers(app_ctx, base_url)
        result = await _set_cloud_flow_state(
            app_ctx,
            base_url,
            headers,
            params.flow_id,
            enabled=False,
            statuscode=params.statuscode,
        )
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})
    except httpx.TimeoutException:
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": "Request timed out; verify flow state before retrying",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_disable_cloud_flow")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_batch_enable_cloud_flows",
    annotations={
        "title": "Batch Enable Cloud Flows",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_batch_enable_cloud_flows(
    params: BatchSetCloudFlowsStateInput, ctx: Context
) -> str:
    """Enable cloud flows in batch for improved performance."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
        result = await _execute_cloud_flow_state_batch(
            app_ctx,
            base_url,
            params.flow_ids,
            enabled=True,
            statuscode=params.statuscode,
            continue_on_error=params.continue_on_error,
        )
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})
    except httpx.TimeoutException:
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": "Batch request timed out; verify per-flow state before retrying",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_batch_enable_cloud_flows")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@write_tool(
    name="dataverse_batch_disable_cloud_flows",
    annotations={
        "title": "Batch Disable Cloud Flows",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_batch_disable_cloud_flows(
    params: BatchSetCloudFlowsStateInput, ctx: Context
) -> str:
    """Disable cloud flows in batch for improved performance."""
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
        result = await _execute_cloud_flow_state_batch(
            app_ctx,
            base_url,
            params.flow_ids,
            enabled=False,
            statuscode=params.statuscode,
            continue_on_error=params.continue_on_error,
        )
        return json.dumps(result)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})
    except httpx.TimeoutException:
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": "Batch request timed out; verify per-flow state before retrying",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_batch_disable_cloud_flows")
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


@delete_tool(
    name="dataverse_remove_component_from_solution",
    annotations={
        "title": "Remove Component From Solution",
        "readOnlyHint": False,
        "destructiveHint": True,
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
            "SolutionComponent": {
                # Dataverse RemoveSolutionComponent expects this nested entity key.
                # For some component types, this must be the underlying object id.
                "solutioncomponentid": params.component_id,
            },
            "ComponentType": params.component_type,
            "SolutionUniqueName": solution_unique_name,
        }

        resp = await app_ctx.http_client.post(action_url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({
            "removed": True,
            "solution_unique_name": solution_unique_name,
            "solution_id": solution_id,
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
