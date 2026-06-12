"""Plug-in performance and trace log tools for the Dataverse MCP server."""

import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import mcp, write_tool
from dataverse_mcp.client import (
    AppContext,
    _DATAVERSE_API_VERSION,
    build_headers,
    extract_error_message,
    odata_quote,
    paginate_records,
    request_with_retry,
    resolve_base_url,
)
from dataverse_mcp.models import (
    GetPluginTraceLogSettingInput,
    ListPluginTraceLogsInput,
    ListPluginTypeStatisticsInput,
    SetPluginTraceLogSettingInput,
)

logger = logging.getLogger(__name__)

_SETTING_VALUE_MAP = {"off": 0, "exception": 1, "all": 2}
_SETTING_LABEL_MAP = {0: "off", 1: "exception", 2: "all"}

_TRACE_LOG_SELECT = (
    "plugintracelogid,"
    "typename,"
    "messagename,"
    "primaryentity,"
    "operationtype,"
    "mode,"
    "depth,"
    "createdon,"
    "exceptiondetails,"
    "messageblock,"
    "performanceexecutionduration,"
    "correlationid,"
    "requestid,"
    "issystemcreated"
)

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


# ---------------------------------------------------------------------------
# Tool: dataverse_get_plugin_trace_log_setting
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_get_plugin_trace_log_setting",
    annotations={
        "title": "Get Plug-in Trace Log Setting",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_plugin_trace_log_setting(
    params: GetPluginTraceLogSettingInput, ctx: Context
) -> str:
    """Get the current plug-in trace log setting for the Dataverse organization.

    Returns the verbosity level: off (0), exception (1), or all (2).

    This setting controls whether Dataverse records plug-in execution traces in
    the plugintracelog entity. Use dataverse_set_plugin_trace_log_setting to
    change the setting, and dataverse_list_plugin_trace_logs to read the logs.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        "/organizations?$select=organizationid,plugintracelogsetting&$top=1"
    )
    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        records = resp.json().get("value", [])
        if not records:
            return json.dumps({"error": True, "message": "No organization record found."})
        org = records[0]
        raw = org.get("plugintracelogsetting", 0)
        return json.dumps({
            "organization_id": org.get("organizationid"),
            "plugin_trace_log_setting": raw,
            "plugin_trace_log_setting_label": _SETTING_LABEL_MAP.get(raw, "unknown"),
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_plugin_trace_log_setting")
        return json.dumps({"error": True, "message": f"Unexpected error: {type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# Tool: dataverse_set_plugin_trace_log_setting
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_set_plugin_trace_log_setting",
    annotations={
        "title": "Set Plug-in Trace Log Setting",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_set_plugin_trace_log_setting(
    params: SetPluginTraceLogSettingInput, ctx: Context
) -> str:
    """Enable or disable plug-in trace logging for the Dataverse organization.

    Sets the organization-wide PluginTraceLogSetting:
      - 'off'       (0): No plug-in trace logs are written.
      - 'exception' (1): Logs written only when a plug-in throws an exception.
      - 'all'       (2): Logs written for every plug-in execution.

    Requires DATAVERSE_ALLOW_WRITE=true on the server.

    WARNING: Setting 'all' generates high log volume. Disable logging once
    debugging is complete to avoid excessive storage usage.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    setting_value = _SETTING_VALUE_MAP[params.setting]

    try:
        headers = await build_headers(app_ctx, base_url)
        org_url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            "/organizations?$select=organizationid&$top=1"
        )
        org_resp = await request_with_retry(app_ctx.http_client, "GET", org_url, headers=headers)
        org_resp.raise_for_status()
        records = org_resp.json().get("value", [])
        if not records:
            return json.dumps({"error": True, "message": "No organization record found."})
        org_id = records[0]["organizationid"]

        patch_url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/organizations({org_id})"
        )
        patch_resp = await request_with_retry(app_ctx.http_client, "PATCH",
            patch_url,
            headers={**headers, "Content-Type": "application/json"},
            json={"plugintracelogsetting": setting_value},
        )
        patch_resp.raise_for_status()
        return json.dumps({
            "success": True,
            "organization_id": org_id,
            "plugin_trace_log_setting": setting_value,
            "plugin_trace_log_setting_label": params.setting,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_set_plugin_trace_log_setting")
        return json.dumps({"error": True, "message": f"Unexpected error: {type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# Tool: dataverse_list_plugin_trace_logs
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_list_plugin_trace_logs",
    annotations={
        "title": "List Plug-in Trace Logs",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_plugin_trace_logs(
    params: ListPluginTraceLogsInput, ctx: Context
) -> str:
    """List plug-in trace log records with optional filters.

    Returns trace and exception information generated by plug-ins and custom
    workflow activities. Records are ordered newest-first.

    Useful filters:
    - type_name: partial match on the plug-in class name (typename)
    - exceptions_only: true to show only failed executions
    - hours_ago: narrow to recent logs (e.g. 1 for the last hour)
    - message_name: filter by triggering message (e.g. 'Create', 'Update')
    - primary_entity: filter by entity the plug-in ran against

    Plug-in trace logging must be enabled via dataverse_set_plugin_trace_log_setting
    before logs will be generated. Use dataverse_get_plugin_trace_log_setting to
    check the current setting.
    """
    app_ctx = _get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    filters: list[str] = []
    if params.type_name:
        filters.append(f"contains(typename,'{odata_quote(params.type_name)}')")
    if params.message_name:
        filters.append(f"messagename eq '{odata_quote(params.message_name)}'")
    if params.primary_entity:
        filters.append(f"primaryentity eq '{odata_quote(params.primary_entity)}'")
    if params.operation_type is not None:
        filters.append(f"operationtype eq {params.operation_type}")
    if params.exceptions_only:
        filters.append("exceptiondetails ne null")
    if params.hours_ago is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=params.hours_ago)
        filters.append(f"createdon ge {cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    query: dict[str, str] = {
        "$select": _TRACE_LOG_SELECT,
        "$top": str(params.top),
        "$orderby": "createdon desc",
    }
    if filters:
        query["$filter"] = " and ".join(filters)

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/plugintracelogs?{urlencode(query, safe='$,')}"
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
        logger.exception("Unexpected error in dataverse_list_plugin_trace_logs")
        return json.dumps({"error": True, "message": f"Unexpected error: {type(e).__name__}: {e}"})
