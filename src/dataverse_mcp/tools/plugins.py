"""Plug-in performance tools for the Dataverse MCP server."""

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
from dataverse_mcp.models import ListPluginTypeStatisticsInput

logger = logging.getLogger(__name__)

_SELECT = (
    "plugintypestatisticid,"
    "averageexecutetimeinmilliseconds,"
    "executecount,"
    "failurecount,"
    "failurepercent,"
    "crashcount,"
    "crashpercent,"
    "crashcontributionpercent,"
    "terminatecpucontributionpercent,"
    "terminatememorycontributionpercent,"
    "terminatehandlescontributionpercent,"
    "terminateothercontributionpercent,"
    "createdon,"
    "modifiedon"
)

_PLUGIN_TYPE_EXPAND = "plugintypeid($select=name,typename,assemblyname)"


def _get_app_ctx(ctx: Context) -> AppContext:
    return ctx.request_context.lifespan_context


# ---------------------------------------------------------------------------
# Tool: dataverse_list_plugin_type_statistics
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_list_plugin_type_statistics",
    annotations={
        "title": "List Plug-in Type Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_plugin_type_statistics(
    params: ListPluginTypeStatisticsInput, ctx: Context
) -> str:
    """List runtime performance statistics for Dataverse plug-in types.

    Returns execution counts, failure rates, crash metrics, and worker-process
    termination contribution percentages per plug-in type. Records are populated
    by Dataverse within 30–60 minutes of plug-in execution; all fields are
    read-only.

    Filter to a specific plug-in type with plugin_type_id, or omit to return
    statistics for all plug-in types. Set include_plugin_type_details=true to
    expand each row with the plug-in type name, typename, and assemblyname.

    Use this to identify slow, high-failure, or crash-prone plug-ins before
    investigating further with the Power Platform Admin Center analytics dashboard.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    query: dict[str, str] = {
        "$select": _SELECT,
        "$top": str(params.top),
        "$orderby": "averageexecutetimeinmilliseconds desc",
    }
    if params.plugin_type_id:
        query["$filter"] = (
            f"_plugintypeid_value eq {params.plugin_type_id}"
        )
    if params.include_plugin_type_details:
        query["$expand"] = _PLUGIN_TYPE_EXPAND

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/plugintypestatistics?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        return json.dumps({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= params.top,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_list_plugin_type_statistics")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
