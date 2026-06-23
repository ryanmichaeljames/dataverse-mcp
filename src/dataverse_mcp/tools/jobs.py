"""Async operation (system job) monitoring and management tools.

Covers asyncoperation records (entity set: asyncoperations).
Verified against the Dataverse Web API v9.2 asyncoperation entity reference
(Microsoft Learn, June 2026):
  - Entity set: asyncoperations
  - Primary key: asyncoperationid
  - statecode: 0=Ready, 1=Suspended, 2=Locked, 3=Completed
  - statuscode: 0=Waiting For Resources, 10=Waiting, 20=In Progress,
                21=Pausing, 22=Canceling, 30=Succeeded, 31=Failed, 32=Canceled
  - Cancel: PATCH asyncoperations(<id>) with {"statecode": 3, "statuscode": 32}
    (asyncoperation DOES support statecode/statuscode PATCH, unlike systemuser)
"""

import json
import logging
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("jobs")
from dataverse_mcp.client import (  # noqa: E402
    _DATAVERSE_API_VERSION,
    build_headers,
    finalize_response,
    get_app_ctx,
    paginate_records,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import (  # noqa: E402
    CancelAsyncOperationInput,
    GetAsyncOperationInput,
    ListAsyncOperationsInput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default column selection (keep payloads small)
# ---------------------------------------------------------------------------

_DEFAULT_ASYNC_OP_SELECT = [
    "asyncoperationid",
    "name",
    "operationtype",
    "statecode",
    "statuscode",
    "message",
    "friendlymessage",
    "startedon",
    "completedon",
    "createdon",
    "modifiedon",
    "_regardingobjectid_value",
]

# ---------------------------------------------------------------------------
# Human-readable labels for common statecode / statuscode values
# ---------------------------------------------------------------------------

_STATECODE_LABELS: dict[int, str] = {
    0: "Ready",
    1: "Suspended",
    2: "Locked",
    3: "Completed",
}

_STATUSCODE_LABELS: dict[int, str] = {
    0: "Waiting For Resources",
    10: "Waiting",
    20: "In Progress",
    21: "Pausing",
    22: "Canceling",
    30: "Succeeded",
    31: "Failed",
    32: "Canceled",
}


def _enrich_record(record: dict) -> dict:
    """Add human-readable statecode_label / statuscode_label to *record* in-place."""
    sc = record.get("statecode")
    if sc is not None:
        record["statecode_label"] = _STATECODE_LABELS.get(sc, str(sc))
    ssc = record.get("statuscode")
    if ssc is not None:
        record["statuscode_label"] = _STATUSCODE_LABELS.get(ssc, str(ssc))
    return record


# ---------------------------------------------------------------------------
# Read: list async operations
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_async_operations",
    annotations={
        "title": "List Async Operations",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_async_operations(
    params: ListAsyncOperationsInput, ctx: Context
) -> str:
    """List asyncoperation (system job) records in the Dataverse environment.

    Returns asyncoperationid, name, operationtype, statecode, statuscode,
    message, friendlymessage, startedon, completedon, createdon, modifiedon,
    and _regardingobjectid_value. Also includes statecode_label and
    statuscode_label for human readability.
    Filter by state_code (0=Ready,1=Suspended,2=Locked,3=Completed),
    status_code (0=WaitingForResources,10=Waiting,20=InProgress,21=Pausing,
    22=Canceling,30=Succeeded,31=Failed,32=Canceled), or operation_type (raw int).
    Use dataverse_get_async_operation for full details on a specific job.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_ASYNC_OP_SELECT
    top = params.top

    # Build optional $filter from state_code / status_code / operation_type
    filter_parts: list[str] = []
    if params.state_code is not None:
        filter_parts.append(f"statecode eq {params.state_code}")
    if params.status_code is not None:
        filter_parts.append(f"statuscode eq {params.status_code}")
    if params.operation_type is not None:
        filter_parts.append(f"operationtype eq {params.operation_type}")

    query_params: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
        "$orderby": "createdon desc",
    }
    if filter_parts:
        query_params["$filter"] = " and ".join(filter_parts)

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/asyncoperations"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
        enriched = [_enrich_record(r) for r in records]
        return finalize_response({
            "records": enriched,
            "count": len(enriched),
            "has_more": len(enriched) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_async_operations")


# ---------------------------------------------------------------------------
# Read: get a single async operation
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_get_async_operation",
    annotations={
        "title": "Get Async Operation",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_async_operation(
    params: GetAsyncOperationInput, ctx: Context
) -> str:
    """Retrieve a single asyncoperation (system job) record by its GUID.

    Returns the full record including name, operationtype, statecode,
    statuscode, message, friendlymessage, startedon, completedon, and
    _regardingobjectid_value. Also includes statecode_label and
    statuscode_label for human readability.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select_str = ",".join(_DEFAULT_ASYNC_OP_SELECT)
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/asyncoperations({params.async_operation_id})"
        f"?$select={select_str}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        _enrich_record(record)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_async_operation")


# ---------------------------------------------------------------------------
# Write: cancel an async operation
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_cancel_async_operation",
    annotations={
        "title": "Cancel Async Operation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_cancel_async_operation(
    params: CancelAsyncOperationInput, ctx: Context
) -> str:
    """Cancel a running or waiting asyncoperation (system job).

    PATCHes asyncoperations(<id>) with statecode=3 (Completed) and
    statuscode=32 (Canceled). The asyncoperation entity supports direct
    statecode/statuscode PATCH (verified against the Dataverse Web API v9.2
    asyncoperation entity reference — unlike systemuser which has no
    statecode, asyncoperation's state IS mutable via PATCH).
    The job must be in a cancellable state (statecode 0=Ready, 1=Suspended,
    or 2=Locked). Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    patch_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/asyncoperations({params.async_operation_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client,
            "PATCH",
            patch_url,
            headers=headers,
            json={"statecode": 3, "statuscode": 32},
        )
        resp.raise_for_status()
        logger.info(
            "Cancelled async operation %s via PATCH statecode=3 statuscode=32",
            params.async_operation_id,
        )
        return json.dumps({
            "cancelled": True,
            "async_operation_id": params.async_operation_id,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_cancel_async_operation")
