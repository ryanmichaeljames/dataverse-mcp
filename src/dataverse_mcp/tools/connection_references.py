"""Connection reference tools for the Dataverse MCP server."""

import json
import logging
from urllib.parse import urlencode

from mcp.server.fastmcp import Context

from dataverse_mcp._app import delete_tool, mcp, write_tool
from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    build_headers,
    finalize_response,
    get_app_ctx,
    odata_quote,
    paginate_records,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import (
    CreateConnectionReferenceInput,
    DeleteConnectionReferenceInput,
    GetConnectionReferenceInput,
    ListConnectionReferencesInput,
    UpdateConnectionReferenceInput,
)

logger = logging.getLogger(__name__)

_SELECT = (
    "connectionreferenceid,"
    "connectionreferencedisplayname,"
    "connectionreferencelogicalname,"
    "connectorid,"
    "connectionid,"
    "description,"
    "statecode,"
    "statuscode,"
    "ismanaged,"
    "componentstate,"
    "createdon,"
    "modifiedon"
)


def _strip_odata(record: dict) -> dict:
    return {k: v for k, v in record.items() if not k.startswith("@")}


# ---------------------------------------------------------------------------
# Tool: dataverse_list_connection_references
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_list_connection_references",
    annotations={
        "title": "List Connection References",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_connection_references(
    params: ListConnectionReferencesInput, ctx: Context
) -> str:
    """List connection references in the Dataverse environment.

    Returns connectionreferenceid, logical name, display name, connector ID,
    connection ID (empty string if not yet assigned), status, and managed state.

    Filter by connector_id to find all references for a specific connector type.
    Filter by statecode=0 to show only active references. Use the connection_id
    field to identify which references still need a connection assigned — an empty
    connectionid means the flow or app using it will fail at runtime.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    filters: list[str] = []
    if params.connector_id:
        filters.append(f"connectorid eq '{odata_quote(params.connector_id)}'")
    if params.statecode is not None:
        filters.append(f"statecode eq {params.statecode}")
    if params.filter:
        filters.append(f"({params.filter})")

    query: dict[str, str] = {
        "$select": _SELECT,
        "$top": str(params.top),
        "$orderby": "connectionreferencedisplayname asc",
    }
    if filters:
        query["$filter"] = " and ".join(filters)

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/connectionreferences?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        return finalize_response({
            "records": [_strip_odata(r) for r in records],
            "count": len(records),
            "has_more": len(records) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_connection_references")


# ---------------------------------------------------------------------------
# Tool: dataverse_get_connection_reference
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_get_connection_reference",
    annotations={
        "title": "Get Connection Reference",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_connection_reference(
    params: GetConnectionReferenceInput, ctx: Context
) -> str:
    """Get a single connection reference by GUID or logical name.

    Returns the full record including the assigned connection ID, connector ID,
    status, and managed state. An empty connectionid means no connection has
    been wired up yet — use dataverse_update_connection_reference to assign one.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        if params.connection_reference_id:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/connectionreferences({params.connection_reference_id})"
                f"?$select={_SELECT}"
            )
            resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
            if resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": f"Connection reference '{params.connection_reference_id}' not found.",
                })
            resp.raise_for_status()
            return finalize_response({"record": _strip_odata(resp.json())})

        # Lookup by logical name
        escaped = odata_quote(params.connection_reference_logical_name)  # type: ignore[arg-type]
        query = urlencode(
            {
                "$select": _SELECT,
                "$filter": f"connectionreferencelogicalname eq '{escaped}'",
                "$top": "1",
            },
            safe="$,",
        )
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/connectionreferences?{query}"
        )
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        if not items:
            return json.dumps({
                "error": True,
                "message": (
                    f"Connection reference with logical name "
                    f"'{params.connection_reference_logical_name}' not found."
                ),
            })
        return finalize_response({"record": _strip_odata(items[0])})

    except Exception as e:
        return tool_error_response(e, "dataverse_get_connection_reference")


# ---------------------------------------------------------------------------
# Tool: dataverse_create_connection_reference
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_connection_reference",
    annotations={
        "title": "Create Connection Reference",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_connection_reference(
    params: CreateConnectionReferenceInput, ctx: Context
) -> str:
    """Create a new connection reference in the Dataverse environment.

    A connection reference links a logical connector type (connectorid) to an
    actual connection (connectionid). Cloud flows and apps reference the logical
    name rather than a specific connection, enabling portable solutions.

    Provide connection_id to assign a connection immediately, or omit and use
    dataverse_update_connection_reference later to wire up the connection.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "connectionreferencedisplayname": params.display_name,
        "connectionreferencelogicalname": params.logical_name,
        "connectorid": params.connector_id,
    }
    if params.connection_id is not None:
        body["connectionid"] = params.connection_id
    if params.description is not None:
        body["description"] = params.description

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/connectionreferences"

    try:
        headers = await build_headers(app_ctx, base_url)
        if params.solution_unique_name:
            headers = {**headers, "MSCRM.SolutionUniqueName": params.solution_unique_name}
        resp = await request_with_retry(app_ctx.http_client, "POST", url, json=body, headers=headers)
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        logger.info("Created connection reference '%s' (%s)", params.logical_name, location)
        return json.dumps({
            "created": True,
            "logical_name": params.logical_name,
            "display_name": params.display_name,
            "connector_id": params.connector_id,
            "solution_unique_name": params.solution_unique_name,
            "location": location,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_connection_reference")


# ---------------------------------------------------------------------------
# Tool: dataverse_update_connection_reference
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_connection_reference",
    annotations={
        "title": "Update Connection Reference",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_connection_reference(
    params: UpdateConnectionReferenceInput, ctx: Context
) -> str:
    """Update a connection reference — most commonly used to assign a connection.

    Setting connection_id wires up an actual connection to the reference, enabling
    cloud flows and apps that use this reference to run successfully. Pass an
    empty string for connection_id to clear the assigned connection.

    Provide solution_unique_name to associate this reference with a solution as
    part of the update (passed as MSCRM.SolutionUniqueName request header).

    Use dataverse_list_connection_references to find references with an empty
    connectionid (not yet wired up) after solution import.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.connection_id is not None:
        body["connectionid"] = params.connection_id
    if params.display_name is not None:
        body["connectionreferencedisplayname"] = params.display_name
    if params.description is not None:
        body["description"] = params.description

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/connectionreferences({params.connection_reference_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        patch_headers = {**headers, "Content-Type": "application/json"}
        if params.solution_unique_name:
            patch_headers["MSCRM.SolutionUniqueName"] = params.solution_unique_name
        resp = await request_with_retry(app_ctx.http_client, "PATCH", url, json=body, headers=patch_headers)
        resp.raise_for_status()
        logger.info("Updated connection reference %s", params.connection_reference_id)
        return json.dumps({
            "updated": True,
            "connection_reference_id": params.connection_reference_id,
            "solution_unique_name": params.solution_unique_name,
            "changes": body,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_update_connection_reference")


# ---------------------------------------------------------------------------
# Tool: dataverse_delete_connection_reference
# ---------------------------------------------------------------------------


@delete_tool(
    name="dataverse_delete_connection_reference",
    annotations={
        "title": "Delete Connection Reference",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_connection_reference(
    params: DeleteConnectionReferenceInput, ctx: Context
) -> str:
    """Delete a connection reference from the Dataverse environment.

    WARNING: Deleting a connection reference will break any cloud flows or apps
    that reference it. Verify nothing depends on this reference before deleting.

    Only unmanaged connection references can be deleted. Managed references
    (ismanaged=true) must be removed by uninstalling the solution that owns them.

    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/connectionreferences({params.connection_reference_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "DELETE", url, headers=headers)
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Connection reference '{params.connection_reference_id}' not found.",
            })
        resp.raise_for_status()
        logger.info("Deleted connection reference %s", params.connection_reference_id)
        return json.dumps({
            "deleted": True,
            "connection_reference_id": params.connection_reference_id,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_connection_reference")
