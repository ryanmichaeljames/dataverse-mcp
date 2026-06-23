"""Solution-related tools for the Dataverse MCP server."""

import base64
import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("solutions")
flow_tool, flow_write_tool, flow_delete_tool = category_tools("flows")
from dataverse_mcp.batch import build_batch_body, parse_batch_response
from dataverse_mcp.client import (
    AppContext,
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
    AddComponentToSolutionInput,
    BatchSetCloudFlowsStateInput,
    CloneSolutionAsPatchInput,
    CreatePublisherInput,
    CreateSolutionInput,
    ExportSolutionInput,
    GetImportJobInput,
    GetSolutionHistoryInput,
    GetSolutionInput,
    ImportSolutionInput,
    ListCloudFlowsInput,
    ListImportJobsInput,
    ListSolutionComponentsInput,
    ListSolutionHistoriesInput,
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
    372: "Connector",
    373: "Connector",
    380: "Environment Variable Definition",
    381: "Environment Variable Value",
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

_DEFAULT_SOLUTION_HISTORY_SELECT = [
    "msdyn_solutionhistoryid",
    "msdyn_name",
    "msdyn_solutionversion",
    "msdyn_operation",
    "msdyn_suboperation",
    "msdyn_starttime",
    "msdyn_endtime",
    "msdyn_result",
    "msdyn_status",
    "msdyn_errorcode",
    "msdyn_exceptionmessage",
    "msdyn_publishername",
    "msdyn_ismanaged",
]

_CLOUD_FLOW_COMPONENT_TYPE = 29
_CLOUD_FLOW_CATEGORY_FILTER = "category eq 5"

# $batch requests and workflow state transitions need longer than the client default.
_BATCH_TIMEOUT = 120.0
_FLOW_STATE_TIMEOUT = 120.0

# ExportSolution and ImportSolutionAsync are slow operations; 600 s keeps us
# from timing out on large solutions while costing nothing on fast calls.
_SOLUTION_JOB_TIMEOUT = 600.0

# Base64 payload length above which inline return/accept is refused.
# Decoded zip ≈ 0.75 × this — stays well under the 5 MB finalize_response cap
# with the JSON envelope overhead.
_INLINE_FILE_MAX_BYTES = 3_000_000

# Default $select for importjob queries — deliberately excludes the 'data' column
# (MaxLength ~1 GB result XML) to keep responses small.
_DEFAULT_IMPORT_JOB_SELECT = [
    "importjobid",
    "solutionname",
    "progress",
    "startedon",
    "completedon",
    "createdon",
    "name",
    "solutionid",
]


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
        patch_resp = await request_with_retry(app_ctx.http_client, "PATCH",
            patch_url,
            json={"statecode": statecode, "statuscode": target_statuscode},
            headers=headers,
            timeout=_FLOW_STATE_TIMEOUT,
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
        action_resp = await request_with_retry(app_ctx.http_client, "POST",
            set_state_url,
            json=set_state_body,
            headers=headers,
            timeout=_FLOW_STATE_TIMEOUT,
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
            f"_solutionid_value eq '{odata_quote(resolved_solution_id)}' and "
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

    response = await request_with_retry(app_ctx.http_client, "POST",
        batch_url,
        headers=request_headers,
        content=batch_body.encode("utf-8"),
        timeout=_BATCH_TIMEOUT,
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


def _enrich_component_type(record: dict) -> dict:
    """Add component_type_name to a solution component record."""
    comp_type = record.get("componenttype")
    if comp_type is not None:
        record["componenttype_name"] = COMPONENT_TYPE_NAMES.get(
            comp_type, f"Unknown ({comp_type})"
        )
    return record


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
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    escaped_name = odata_quote(solution_unique_name or "")
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


@tool(
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
    """List solutions in the Dataverse environment with name, version, and managed status.

    Use filter to narrow results (e.g., "ismanaged eq false"). Use
    dataverse_get_solution for full details on a specific solution.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_solutions")


@tool(
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
    """Retrieve a single Dataverse solution by its unique name or GUID.

    Returns full details including version, publisher, and managed status.
    Provide solution_unique_name or solution_id — not both.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
            resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
            resp.raise_for_status()
            record = resp.json()
        else:
            escaped_name = odata_quote(params.solution_unique_name)
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
    except Exception as e:
        return tool_error_response(e, "dataverse_get_solution")


@tool(
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
    """List components within a Dataverse solution, with human-readable type names.

    Use component_type to filter by type code (1=Entity, 61=Web Resource,
    300=Canvas App, 91=Plugin Assembly, 92=SDK Message Processing Step).
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    top = params.top
    odata_filter = f"_solutionid_value eq '{odata_quote(params.solution_id)}'"
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
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_solution_components")


@tool(
    name="dataverse_get_solution_history",
    annotations={
        "title": "Get Solution History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_solution_history(
    params: GetSolutionHistoryInput, ctx: Context
) -> str:
    """Retrieve a single solution history record by its GUID.

    Returns import/upgrade/export operation details including result, timing,
    error messages, and publisher information from msdyn_solutionhistories.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_SOLUTION_HISTORY_SELECT

    try:
        headers = await build_headers(app_ctx, base_url)
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/msdyn_solutionhistories({params.solution_history_id})"
            f"?$select={','.join(select)}"
        )
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_solution_history")


@tool(
    name="dataverse_list_solution_histories",
    annotations={
        "title": "List Solution Histories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_solution_histories(
    params: ListSolutionHistoriesInput, ctx: Context
) -> str:
    """List solution history records from msdyn_solutionhistories.

    Tracks import, upgrade, and export operations on solutions. Optionally
    filter by solution_id or solution_unique_name (mutually exclusive).
    solution_id is resolved to the solution unique name first, then used to
    filter history records via msdyn_name. Omit both to list all.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_SOLUTION_HISTORY_SELECT
    top = params.top

    try:
        headers = await build_headers(app_ctx, base_url)

        odata_filter: str | None = None

        if params.solution_id or params.solution_unique_name:
            # Resolve the solution unique name. The msdyn_solutionhistory entity
            # stores the solution unique name as a plain text column (msdyn_name),
            # not a lookup. Filter by that name directly.
            if params.solution_id:
                solution = await _resolve_solution_record(
                    app_ctx,
                    base_url,
                    headers,
                    params.solution_id,
                    None,
                )
                if solution is None:
                    return json.dumps({
                        "error": True,
                        "message": _solution_not_found_message(params.solution_id, None),
                    })
                resolved_name = solution.get("uniquename")
                if not resolved_name:
                    return json.dumps({
                        "error": True,
                        "message": "Resolved solution is missing uniquename",
                    })
            else:
                resolved_name = params.solution_unique_name

            odata_filter = f"msdyn_name eq '{odata_quote(resolved_name)}'"

        query_params: dict[str, str] = {
            "$select": ",".join(select),
            "$top": str(top),
        }
        if odata_filter:
            query_params["$filter"] = odata_filter

        url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/msdyn_solutionhistories"
        full_url = f"{url}?{urlencode(query_params, safe='$,')}"

        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_solution_histories")


@flow_tool(
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
    """List cloud flows in the Dataverse environment, optionally scoped to a solution.

    Returns workflow records with statecode, statuscode, and category. Scope
    to a specific solution with solution_id or solution_unique_name.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
            return finalize_response({
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
            chunk_filter = " or ".join(
                [f"workflowid eq '{odata_quote(flow_id)}'" for flow_id in chunk]
            )
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

        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
            "solution_id": resolved_solution_id,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_get_cloud_flows")


@flow_write_tool(
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
    """Enable a single cloud flow (set statecode=1, statuscode=2).

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
    except httpx.TimeoutException:
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": "Request timed out; verify flow state before retrying",
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_enable_cloud_flow")


@flow_write_tool(
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
    """Disable a single cloud flow (set statecode=0, statuscode=1).

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
    except httpx.TimeoutException:
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": "Request timed out; verify flow state before retrying",
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_disable_cloud_flow")


@flow_write_tool(
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
    """Enable multiple cloud flows in a single $batch request for improved performance.

    Use this instead of calling dataverse_enable_cloud_flow repeatedly.
    Returns per-flow results with status_code and ok flag. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
        result = await _execute_cloud_flow_state_batch(
            app_ctx,
            base_url,
            params.flow_ids,
            enabled=True,
            statuscode=params.statuscode,
            continue_on_error=params.continue_on_error,
        )
        return json.dumps(result)
    except httpx.TimeoutException:
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": "Batch request timed out; verify per-flow state before retrying",
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_batch_enable_cloud_flows")


@flow_write_tool(
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
    """Disable multiple cloud flows in a single $batch request for improved performance.

    Use this instead of calling dataverse_disable_cloud_flow repeatedly.
    Returns per-flow results with status_code and ok flag. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
        result = await _execute_cloud_flow_state_batch(
            app_ctx,
            base_url,
            params.flow_ids,
            enabled=False,
            statuscode=params.statuscode,
            continue_on_error=params.continue_on_error,
        )
        return json.dumps(result)
    except httpx.TimeoutException:
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": "Batch request timed out; verify per-flow state before retrying",
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_batch_disable_cloud_flows")


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
    """Create a Dataverse publisher that owns the customization prefix for solutions.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        resp = await request_with_retry(app_ctx.http_client, "POST", url, json=body, headers=headers)
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "uniquename": params.uniquename,
            "display_name": params.display_name,
            "location": location,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_publisher")


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
    """Update a Dataverse publisher's display name, customization prefix, or option value prefix.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        resp = await request_with_retry(app_ctx.http_client, "PATCH", url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({"updated": True, "publisher_id": params.publisher_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_publisher")


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
    """Create a new Dataverse solution scoped to a publisher.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        resp = await request_with_retry(app_ctx.http_client, "POST", url, json=body, headers=headers)
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "solution_unique_name": params.solution_unique_name,
            "version": params.version,
            "location": location,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_solution")


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
    """Update a Dataverse solution's display name, description, or publisher.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        resp = await request_with_retry(app_ctx.http_client, "PATCH", url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({
            "updated": True,
            "solution_id": target_solution_id,
            "solution_unique_name": solution.get("uniquename"),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_update_solution")


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
    """Update a Dataverse solution's version string only (e.g., '1.0.0.1' → '1.0.0.2').

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        resp = await request_with_retry(app_ctx.http_client, "PATCH",
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
    except Exception as e:
        return tool_error_response(e, "dataverse_update_solution_version")


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
    """Add an existing component to a Dataverse solution via AddSolutionComponent.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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

        resp = await request_with_retry(app_ctx.http_client, "POST", action_url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({
            "added": True,
            "solution_unique_name": solution_unique_name,
            "solution_id": solution.get("solutionid"),
            "component_id": params.component_id,
            "component_type": params.component_type,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_add_component_to_solution")


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
    """Remove a component from a Dataverse solution via RemoveSolutionComponent.

    Removes the component from the solution only — does not delete the component.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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

        resp = await request_with_retry(app_ctx.http_client, "POST", action_url, json=body, headers=headers)
        resp.raise_for_status()
        return json.dumps({
            "removed": True,
            "solution_unique_name": solution_unique_name,
            "solution_id": solution_id,
            "component_id": params.component_id,
            "component_type": params.component_type,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_remove_component_from_solution")


# ---------------------------------------------------------------------------
# File I/O helpers (first filesystem access in this tool module)
# ---------------------------------------------------------------------------


def _decode_and_write_zip(b64_data: str, output_path: str) -> tuple[Path, int]:
    """Decode a base64 string and write the bytes to *output_path*.

    Creates the parent directory if it does not exist.

    Returns (resolved_path, size_bytes).

    Raises:
        ValueError: When output_path is empty.
        OSError: On any OS-level I/O failure (PermissionError, etc.).
    """
    if not output_path or not output_path.strip():
        raise ValueError("output_path must be a non-empty string")
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(b64_data)
    target.write_bytes(data)
    return target, len(data)


def _read_and_encode_zip(input_path: str) -> str:
    """Read a .zip file from *input_path* and return its base64-encoded content.

    Raises:
        ValueError: When input_path is empty.
        FileNotFoundError: When the file does not exist.
        OSError: On any other OS-level I/O failure.
    """
    if not input_path or not input_path.strip():
        raise ValueError("input_path must be a non-empty string")
    source = Path(input_path).expanduser().resolve()
    data = source.read_bytes()
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# ALM tools: export / import / poll / list / clone
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_export_solution",
    annotations={
        "title": "Export Solution",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_export_solution(params: ExportSolutionInput, ctx: Context) -> str:
    """Export a Dataverse solution as a base64-encoded zip.

    Calls the ExportSolution unbound action. Large solutions (>~3 MB base64)
    must be saved to disk via output_path — supply a local filesystem path and
    the server writes the decoded .zip there, returning metadata only (no base64
    in the response). Small solutions are returned inline when output_path is
    omitted. This tool is read-only for the org (no mutations); writing a local
    .zip when output_path is set is local I/O only and does not require
    DATAVERSE_ALLOW_WRITE.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "SolutionName": params.solution_name,
        "Managed": params.managed,
    }
    _optional_export_flags = {
        "ExportGeneralSettings": params.export_general_settings,
        "ExportCustomizationSettings": params.export_customization_settings,
        "ExportEmailTrackingSettings": params.export_email_tracking_settings,
        "ExportAutoNumberingSettings": params.export_auto_numbering_settings,
        "ExportCalendarSettings": params.export_calendar_settings,
        "ExportRelationshipRoles": params.export_relationship_roles,
        "ExportIsvConfig": params.export_isv_config,
        "ExportSales": params.export_sales,
        "ExportMarketingSettings": params.export_marketing_settings,
        "ExportOutlookSynchronizationSettings": params.export_outlook_synchronization_settings,
    }
    for api_key, value in _optional_export_flags.items():
        if value is not None:
            body[api_key] = value

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/ExportSolution"

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client,
            "POST",
            url,
            json=body,
            headers=headers,
            timeout=_SOLUTION_JOB_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()
        b64_file = result.get("ExportSolutionFile", "")

        # --- output_path provided: write to disk, return metadata only ---
        if params.output_path:
            try:
                resolved_path, size_bytes = _decode_and_write_zip(b64_file, params.output_path)
            except (PermissionError, FileNotFoundError, IsADirectoryError, OSError) as io_err:
                logger.error("Export write failed for '%s': %s", params.output_path, io_err)
                return json.dumps({
                    "error": True,
                    "message": f"Failed to write solution zip to '{params.output_path}': {io_err}",
                })
            except Exception as io_err:
                logger.error("Export write unexpected error: %s", io_err)
                return json.dumps({
                    "error": True,
                    "message": f"Unexpected error writing solution zip: {io_err}",
                })
            return json.dumps({
                "written": True,
                "path": str(resolved_path),
                "size_bytes": size_bytes,
                "solution": params.solution_name,
                "managed": params.managed,
            })

        # --- no output_path: inline if under threshold, error if over ---
        b64_len = len(b64_file)
        if b64_len > _INLINE_FILE_MAX_BYTES:
            size_mb = b64_len / 1_000_000
            return json.dumps({
                "error": True,
                "message": (
                    f"Exported solution is {size_mb:.1f} MB (base64); "
                    "supply output_path to write it to disk instead of returning it inline."
                ),
            })

        return finalize_response({
            "solution_file_base64": b64_file,
            "size_bytes": b64_len,
            "solution": params.solution_name,
            "managed": params.managed,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_export_solution")


@write_tool(
    name="dataverse_import_solution",
    annotations={
        "title": "Import Solution",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_import_solution(params: ImportSolutionInput, ctx: Context) -> str:
    """Import a Dataverse solution asynchronously via ImportSolutionAsync.

    Accepts the solution zip as inline base64 (customization_file) or a local
    filesystem path (input_path). Returns import_job_id, async_operation_id, and
    import_job_key immediately — poll dataverse_get_import_job with import_job_id
    to track progress and retrieve failure details. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    # Resolve the base64 payload from inline or local file.
    if params.input_path:
        try:
            customization_b64 = _read_and_encode_zip(params.input_path)
        except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as io_err:
            logger.error("Import read failed for '%s': %s", params.input_path, io_err)
            return json.dumps({
                "error": True,
                "message": f"Failed to read solution zip from '{params.input_path}': {io_err}",
            })
        except Exception as io_err:
            logger.error("Import read unexpected error: %s", io_err)
            return json.dumps({
                "error": True,
                "message": f"Unexpected error reading solution zip: {io_err}",
            })
    else:
        # Inline base64 — guard against oversized payloads.
        customization_b64 = params.customization_file or ""
        if len(customization_b64) > _INLINE_FILE_MAX_BYTES:
            size_mb = len(customization_b64) / 1_000_000
            return json.dumps({
                "error": True,
                "message": (
                    f"Inline customization_file is {size_mb:.1f} MB; "
                    "use input_path to supply the local .zip path instead."
                ),
            })

    import_job_id = params.import_job_id or str(uuid.uuid4())

    body: dict = {
        "CustomizationFile": customization_b64,
        "OverwriteUnmanagedCustomizations": params.overwrite_unmanaged_customizations,
        "PublishWorkflows": params.publish_workflows,
        "ImportJobId": import_job_id,
        "HoldingSolution": params.hold_for_upgrade,
        "SkipProductUpdateDependencies": params.skip_product_update_dependencies,
    }

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/ImportSolutionAsync"

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client,
            "POST",
            url,
            json=body,
            headers=headers,
            timeout=_SOLUTION_JOB_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()
        return json.dumps({
            "accepted": True,
            "import_job_id": import_job_id,
            "async_operation_id": result.get("AsyncOperationId"),
            "import_job_key": result.get("ImportJobKey"),
            "message": (
                "Import started; poll dataverse_get_import_job with import_job_id."
            ),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_import_solution")


@tool(
    name="dataverse_get_import_job",
    annotations={
        "title": "Get Import Job",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_import_job(params: GetImportJobInput, ctx: Context) -> str:
    """Retrieve a single importjob record by its GUID to check import progress.

    Returns progress (0–100), completedon, solutionname, and other tracking fields.
    The large result XML ('data' column) is excluded by default; set include_data=true
    to fetch it when diagnosing import failures. Use dataverse_import_solution to
    start an import and obtain the import_job_id.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select_cols = list(_DEFAULT_IMPORT_JOB_SELECT)
    if params.include_data:
        select_cols.append("data")

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/importjobs({params.import_job_id})"
        f"?$select={','.join(select_cols)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Import job not found: '{params.import_job_id}'",
            })
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        progress = record.get("progress")
        completed = record.get("completedon") is not None
        return finalize_response({
            "record": record,
            "completed": completed,
            "progress": progress,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_get_import_job")


@tool(
    name="dataverse_list_import_jobs",
    annotations={
        "title": "List Import Jobs",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_import_jobs(params: ListImportJobsInput, ctx: Context) -> str:
    """List importjob records, optionally filtered by solution unique name.

    The large result XML ('data' column) is excluded from all records by default.
    Results are ordered by createdon descending (most recent first).
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select_cols = params.select or _DEFAULT_IMPORT_JOB_SELECT
    top = params.top

    query_params: dict[str, str] = {
        "$select": ",".join(select_cols),
        "$top": str(top),
        "$orderby": "createdon desc",
    }
    if params.solution_name:
        escaped = odata_quote(params.solution_name)
        query_params["$filter"] = f"solutionname eq '{escaped}'"

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/importjobs"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_import_jobs")


@write_tool(
    name="dataverse_clone_solution_as_patch",
    annotations={
        "title": "Clone Solution As Patch",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_clone_solution_as_patch(
    params: CloneSolutionAsPatchInput, ctx: Context
) -> str:
    """Clone a solution as a patch via the unbound CloneAsPatch action.

    Resolves the parent solution to its unique name (accepting either a GUID
    or unique name), then POSTs to the unbound /CloneAsPatch action with
    ParentSolutionUniqueName in the body. Returns the new patch solution GUID.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
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

        parent_unique_name = solution.get("uniquename") or params.solution_unique_name
        if not parent_unique_name:
            return json.dumps({
                "error": True,
                "message": "Resolved solution is missing uniquename",
            })

        action_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/CloneAsPatch"
        body = {
            "ParentSolutionUniqueName": parent_unique_name,
            "DisplayName": params.display_name,
            "VersionNumber": params.version_number,
        }

        resp = await request_with_retry(
            app_ctx.http_client,
            "POST",
            action_url,
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        result = resp.json()
        return json.dumps({
            "cloned": True,
            "patch_solution_id": result.get("SolutionId"),
            "parent_solution_unique_name": parent_unique_name,
            "version_number": params.version_number,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_clone_solution_as_patch")
