"""Plug-in registration tools for the Dataverse MCP server.

Covers the full Dataverse plug-in registration model end-to-end:
  - Plug-in assemblies  (A1–A5)
  - Plug-in packages    (B1–B5)
  - Plug-in types       (C1–C5)
  - SDK messages        (D1–D2, read-only)
  - SDK message filters (E1–E2, read-only)
  - Processing steps    (F1–F5)
  - Step images         (G1–G5)

Tool count: 29 total — 14 read (@mcp.tool), 10 write (@write_tool), 5 delete (@delete_tool).
"""

import json
import logging
from urllib.parse import urlencode

import httpx
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
    CreatePluginAssemblyInput,
    CreatePluginPackageInput,
    CreatePluginStepImageInput,
    CreatePluginStepInput,
    CreatePluginTypeInput,
    DeletePluginAssemblyInput,
    DeletePluginPackageInput,
    DeletePluginStepImageInput,
    DeletePluginStepInput,
    DeletePluginTypeInput,
    GetPluginAssemblyInput,
    GetPluginPackageInput,
    GetPluginStepImageInput,
    GetPluginStepInput,
    GetPluginTypeInput,
    GetSdkMessageFilterInput,
    GetSdkMessageInput,
    ListPluginAssembliesInput,
    ListPluginPackagesInput,
    ListPluginStepImagesInput,
    ListPluginStepsInput,
    ListPluginTypesInput,
    ListSdkMessageFiltersInput,
    ListSdkMessagesInput,
    UpdatePluginAssemblyInput,
    UpdatePluginPackageInput,
    UpdatePluginStepImageInput,
    UpdatePluginStepInput,
    UpdatePluginTypeInput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default $select projections
# ---------------------------------------------------------------------------

_DEFAULT_ASSEMBLY_SELECT = [
    "pluginassemblyid",
    "name",
    "version",
    "culture",
    "publickeytoken",
    "isolationmode",
    "sourcetype",
    "description",
    "_packageid_value",
]

_DEFAULT_PACKAGE_SELECT = [
    "pluginpackageid",
    "name",
    "uniquename",
    "version",
]

_DEFAULT_TYPE_SELECT = [
    "plugintypeid",
    "typename",
    "friendlyname",
    "name",
    "assemblyname",
    "isworkflowactivity",
    "_pluginassemblyid_value",
]

_DEFAULT_MESSAGE_SELECT = ["sdkmessageid", "name"]

_DEFAULT_FILTER_SELECT = [
    "sdkmessagefilterid",
    "primaryobjecttypecode",
    "_sdkmessageid_value",
]

_DEFAULT_STEP_SELECT = [
    "sdkmessageprocessingstepid",
    "name",
    "stage",
    "mode",
    "rank",
    "filteringattributes",
    "statecode",
    "_sdkmessageid_value",
    "_sdkmessagefilterid_value",
    "_eventhandler_value",
    "description",
]

_DEFAULT_IMAGE_SELECT = [
    "sdkmessageprocessingstepimageid",
    "name",
    "imagetype",
    "entityalias",
    "messagepropertyname",
    "attributes",
    "_sdkmessageprocessingstepid_value",
]


# ---------------------------------------------------------------------------
# Helper: combine OData filter clauses
# ---------------------------------------------------------------------------


def _combine_filters(*filters: str | None) -> str | None:
    active = [f"({f})" for f in filters if f]
    if not active:
        return None
    return " and ".join(active)


# ===========================================================================
# A. Plug-in assemblies
# ===========================================================================


@mcp.tool(
    name="dataverse_get_plugin_assembly",
    annotations={
        "title": "Get Plug-in Assembly",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_plugin_assembly(
    params: GetPluginAssemblyInput, ctx: Context
) -> str:
    """Retrieve a single plug-in assembly record.

    Required: assembly_id (GUID of the pluginassembly).
    Optional: select (columns to return), dataverse_url.

    Note: the 'content' column contains the base64-encoded DLL and is very
    large — exclude it from select unless you specifically need it.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_ASSEMBLY_SELECT
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/pluginassemblies({params.assembly_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in assembly not found: '{params.assembly_id}'",
            })
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_plugin_assembly")


@mcp.tool(
    name="dataverse_list_plugin_assemblies",
    annotations={
        "title": "List Plug-in Assemblies",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_plugin_assemblies(
    params: ListPluginAssembliesInput, ctx: Context
) -> str:
    """List plug-in assemblies, optionally filtered.

    Optional: name_contains (substring on name), package_id (only assemblies
    in that plug-in package), filter (raw OData), top (default 50), select,
    dataverse_url. Results are ordered newest-modified first.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_ASSEMBLY_SELECT
    top = params.top

    filters: list[str] = []
    if params.name_contains:
        filters.append(f"contains(name,'{odata_quote(params.name_contains)}')")
    if params.package_id:
        filters.append(f"_packageid_value eq {params.package_id}")
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
        "$orderby": "modifiedon desc",
    }
    combined = _combine_filters(*filters)
    if combined:
        query["$filter"] = combined

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/pluginassemblies?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_plugin_assemblies")


@write_tool(
    name="dataverse_create_plugin_assembly",
    annotations={
        "title": "Create Plug-in Assembly",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_plugin_assembly(
    params: CreatePluginAssemblyInput, ctx: Context
) -> str:
    """Upload a new plug-in assembly from base64-encoded DLL bytes.

    Required: name, content (base64 DLL).
    Optional: isolation_mode (default 2=Sandbox), version, culture,
    public_key_token, description, dataverse_url.

    sourcetype is fixed to 0 (Database). The assembly must be strong-name
    signed. isolation_mode=2 (Sandbox) is required for Dataverse online.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "name": params.name,
        "content": params.content,
        "isolationmode": params.isolation_mode,
        "sourcetype": 0,
    }
    if params.version is not None:
        body["version"] = params.version
    if params.culture is not None:
        body["culture"] = params.culture
    if params.public_key_token is not None:
        body["publickeytoken"] = params.public_key_token
    if params.description is not None:
        body["description"] = params.description

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/pluginassemblies"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "POST", url, json=body, headers=headers
        )
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "name": params.name,
            "isolation_mode": params.isolation_mode,
            "location": location,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_plugin_assembly")


@write_tool(
    name="dataverse_update_plugin_assembly",
    annotations={
        "title": "Update Plug-in Assembly",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_plugin_assembly(
    params: UpdatePluginAssemblyInput, ctx: Context
) -> str:
    """Update an existing plug-in assembly record (most often to re-deploy DLL bytes).

    Required: assembly_id. At least one of: content (new base64 DLL),
    isolation_mode, version, description. Re-uploading content updates
    the registered code in-place.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.content is not None:
        body["content"] = params.content
    if params.isolation_mode is not None:
        body["isolationmode"] = params.isolation_mode
    if params.version is not None:
        body["version"] = params.version
    if params.description is not None:
        body["description"] = params.description

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/pluginassemblies({params.assembly_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", url, json=body, headers=headers
        )
        resp.raise_for_status()
        return json.dumps({"updated": True, "assembly_id": params.assembly_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_plugin_assembly")


@delete_tool(
    name="dataverse_delete_plugin_assembly",
    annotations={
        "title": "Delete Plug-in Assembly",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_plugin_assembly(
    params: DeletePluginAssemblyInput, ctx: Context
) -> str:
    """Delete a plug-in assembly record.

    Required: assembly_id.

    WARNING: this call fails while dependent plug-in types or steps exist.
    Delete steps, images, and types first (leaf-to-root order).
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/pluginassemblies({params.assembly_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", url, headers=headers
        )
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in assembly not found: '{params.assembly_id}'",
            })
        resp.raise_for_status()
        return json.dumps({"deleted": True, "assembly_id": params.assembly_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_plugin_assembly")


# ===========================================================================
# B. Plug-in packages
# ===========================================================================


@mcp.tool(
    name="dataverse_get_plugin_package",
    annotations={
        "title": "Get Plug-in Package",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_plugin_package(
    params: GetPluginPackageInput, ctx: Context
) -> str:
    """Retrieve a single plug-in package record.

    Required: package_id (GUID of the pluginpackage).
    Optional: select (columns), dataverse_url.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_PACKAGE_SELECT
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/pluginpackages({params.package_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in package not found: '{params.package_id}'",
            })
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_plugin_package")


@mcp.tool(
    name="dataverse_list_plugin_packages",
    annotations={
        "title": "List Plug-in Packages",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_plugin_packages(
    params: ListPluginPackagesInput, ctx: Context
) -> str:
    """List plug-in packages, optionally filtered.

    Optional: name_contains, filter (raw OData), top (default 50), select,
    dataverse_url. Results ordered newest-modified first.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_PACKAGE_SELECT
    top = params.top

    filters: list[str] = []
    if params.name_contains:
        filters.append(f"contains(name,'{odata_quote(params.name_contains)}')")
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
        "$orderby": "modifiedon desc",
    }
    combined = _combine_filters(*filters)
    if combined:
        query["$filter"] = combined

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/pluginpackages?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_plugin_packages")


@write_tool(
    name="dataverse_create_plugin_package",
    annotations={
        "title": "Create Plug-in Package",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_plugin_package(
    params: CreatePluginPackageInput, ctx: Context
) -> str:
    """Upload a new NuGet-based plug-in package.

    Required: name, unique_name, content (base64 .nupkg), version.
    Dataverse extracts the contained plug-in assemblies on create.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "name": params.name,
        "uniquename": params.unique_name,
        "content": params.content,
        "version": params.version,
    }

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/pluginpackages"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "POST", url, json=body, headers=headers
        )
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "unique_name": params.unique_name,
            "location": location,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_plugin_package")


@write_tool(
    name="dataverse_update_plugin_package",
    annotations={
        "title": "Update Plug-in Package",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_plugin_package(
    params: UpdatePluginPackageInput, ctx: Context
) -> str:
    """Update a plug-in package (re-upload content or bump version).

    Required: package_id. At least one of: content (new base64 .nupkg), version.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.content is not None:
        body["content"] = params.content
    if params.version is not None:
        body["version"] = params.version

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/pluginpackages({params.package_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", url, json=body, headers=headers
        )
        resp.raise_for_status()
        return json.dumps({"updated": True, "package_id": params.package_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_plugin_package")


@delete_tool(
    name="dataverse_delete_plugin_package",
    annotations={
        "title": "Delete Plug-in Package",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_plugin_package(
    params: DeletePluginPackageInput, ctx: Context
) -> str:
    """Delete a plug-in package.

    Required: package_id.

    WARNING: deleting a package removes its extracted assemblies, types, and
    steps. Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/pluginpackages({params.package_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", url, headers=headers
        )
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in package not found: '{params.package_id}'",
            })
        resp.raise_for_status()
        return json.dumps({"deleted": True, "package_id": params.package_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_plugin_package")


# ===========================================================================
# C. Plug-in types
# ===========================================================================


@mcp.tool(
    name="dataverse_get_plugin_type",
    annotations={
        "title": "Get Plug-in Type",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_plugin_type(
    params: GetPluginTypeInput, ctx: Context
) -> str:
    """Retrieve a single plug-in type record.

    Required: plugin_type_id (GUID of the plugintype).
    Optional: select (columns), dataverse_url.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_TYPE_SELECT
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/plugintypes({params.plugin_type_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in type not found: '{params.plugin_type_id}'",
            })
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_plugin_type")


@mcp.tool(
    name="dataverse_list_plugin_types",
    annotations={
        "title": "List Plug-in Types",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_plugin_types(
    params: ListPluginTypesInput, ctx: Context
) -> str:
    """List plug-in types, optionally scoped to an assembly.

    Optional: assembly_id (only types in one assembly), typename_contains,
    is_workflow_activity (filter to/from workflow activities), filter (raw
    OData), top (default 50), select, dataverse_url.
    Results ordered by typename ascending.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_TYPE_SELECT
    top = params.top

    filters: list[str] = []
    if params.assembly_id:
        filters.append(f"_pluginassemblyid_value eq {params.assembly_id}")
    if params.typename_contains:
        filters.append(f"contains(typename,'{odata_quote(params.typename_contains)}')")
    if params.is_workflow_activity is not None:
        val = "true" if params.is_workflow_activity else "false"
        filters.append(f"isworkflowactivity eq {val}")
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
        "$orderby": "typename asc",
    }
    combined = _combine_filters(*filters)
    if combined:
        query["$filter"] = combined

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/plugintypes?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_plugin_types")


@write_tool(
    name="dataverse_create_plugin_type",
    annotations={
        "title": "Create Plug-in Type",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_plugin_type(
    params: CreatePluginTypeInput, ctx: Context
) -> str:
    """Register a plug-in type (class) against an assembly.

    Required: assembly_id (GUID of the parent pluginassembly), typename
    (fully-qualified .NET class name e.g. 'MyOrg.Plugins.ContactPlugin').
    Optional: friendly_name, name, is_workflow_activity (default false),
    workflow_activity_group_name, dataverse_url.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "typename": params.typename,
        "isworkflowactivity": params.is_workflow_activity,
        "pluginassemblyid@odata.bind": f"/pluginassemblies({params.assembly_id})",
    }
    if params.friendly_name is not None:
        body["friendlyname"] = params.friendly_name
    if params.name is not None:
        body["name"] = params.name
    if params.workflow_activity_group_name is not None:
        body["workflowactivitygroupname"] = params.workflow_activity_group_name

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/plugintypes"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "POST", url, json=body, headers=headers
        )
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "typename": params.typename,
            "location": location,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_plugin_type")


@write_tool(
    name="dataverse_update_plugin_type",
    annotations={
        "title": "Update Plug-in Type",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_plugin_type(
    params: UpdatePluginTypeInput, ctx: Context
) -> str:
    """Update mutable fields on a plug-in type record.

    Required: plugin_type_id. At least one of: friendly_name, name,
    workflow_activity_group_name. (typename and assembly are not safely
    mutable post-registration — re-create instead.)
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.friendly_name is not None:
        body["friendlyname"] = params.friendly_name
    if params.name is not None:
        body["name"] = params.name
    if params.workflow_activity_group_name is not None:
        body["workflowactivitygroupname"] = params.workflow_activity_group_name

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/plugintypes({params.plugin_type_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", url, json=body, headers=headers
        )
        resp.raise_for_status()
        return json.dumps({"updated": True, "plugin_type_id": params.plugin_type_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_plugin_type")


@delete_tool(
    name="dataverse_delete_plugin_type",
    annotations={
        "title": "Delete Plug-in Type",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_plugin_type(
    params: DeletePluginTypeInput, ctx: Context
) -> str:
    """Delete a plug-in type record.

    Required: plugin_type_id.

    WARNING: this call fails while dependent processing steps exist — delete
    steps and images first.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/plugintypes({params.plugin_type_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", url, headers=headers
        )
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in type not found: '{params.plugin_type_id}'",
            })
        resp.raise_for_status()
        return json.dumps({"deleted": True, "plugin_type_id": params.plugin_type_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_plugin_type")


# ===========================================================================
# D. SDK messages (read-only)
# ===========================================================================


@mcp.tool(
    name="dataverse_get_sdk_message",
    annotations={
        "title": "Get SDK Message",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_sdk_message(
    params: GetSdkMessageInput, ctx: Context
) -> str:
    """Resolve an SDK message by name or ID.

    Provide message_name (e.g. 'Create', 'Update', 'Delete') OR message_id.
    Exactly one identifier required. Returns the sdkmessageid used to bind
    a processing step via dataverse_create_plugin_step.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        if params.message_id:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/sdkmessages({params.message_id})"
                f"?$select={','.join(_DEFAULT_MESSAGE_SELECT)}"
            )
            resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
            if resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": f"SDK message not found: '{params.message_id}'",
                })
            resp.raise_for_status()
            record = resp.json()
            record.pop("@odata.context", None)
            return json.dumps({"record": record})

        # Lookup by name
        escaped_name = odata_quote(params.message_name or "")
        query: dict[str, str] = {
            "$select": ",".join(_DEFAULT_MESSAGE_SELECT),
            "$filter": f"name eq '{escaped_name}'",
            "$top": "1",
        }
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/sdkmessages?{urlencode(query, safe='$,')}"
        )
        records = await paginate_records(url, headers, 1, app_ctx.http_client)
        if not records:
            return json.dumps({
                "error": True,
                "message": f"SDK message not found: '{params.message_name}'",
            })
        record = records[0]
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_sdk_message")


@mcp.tool(
    name="dataverse_list_sdk_messages",
    annotations={
        "title": "List SDK Messages",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_sdk_messages(
    params: ListSdkMessagesInput, ctx: Context
) -> str:
    """List/search SDK messages (the message catalog).

    Optional: name_contains, filter (raw OData), top (default 50), select,
    dataverse_url. Use to discover valid message names before registering a
    processing step.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_MESSAGE_SELECT
    top = params.top

    filters: list[str] = []
    if params.name_contains:
        filters.append(f"contains(name,'{odata_quote(params.name_contains)}')")
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
        "$orderby": "name asc",
    }
    combined = _combine_filters(*filters)
    if combined:
        query["$filter"] = combined

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessages?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_sdk_messages")


# ===========================================================================
# E. SDK message filters (read-only)
# ===========================================================================


@mcp.tool(
    name="dataverse_get_sdk_message_filter",
    annotations={
        "title": "Get SDK Message Filter",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_sdk_message_filter(
    params: GetSdkMessageFilterInput, ctx: Context
) -> str:
    """Resolve an SDK message filter by message + primary entity, or by filter ID.

    Mode 1: Provide filter_id alone to retrieve a specific filter record.
    Mode 2: Provide message_id + primary_entity (lowercase logical name,
            e.g. 'contact') to look up the filter that scopes that message
            to one entity.

    Use the returned sdkmessagefilterid to scope a processing step to one
    entity via dataverse_create_plugin_step filter_id parameter.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        if params.filter_id:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/sdkmessagefilters({params.filter_id})"
                f"?$select={','.join(_DEFAULT_FILTER_SELECT)}"
            )
            resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
            if resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": f"SDK message filter not found: '{params.filter_id}'",
                })
            resp.raise_for_status()
            record = resp.json()
            record.pop("@odata.context", None)
            return json.dumps({"record": record})

        # Lookup by message + entity
        escaped_entity = odata_quote(params.primary_entity or "")
        query: dict[str, str] = {
            "$select": ",".join(_DEFAULT_FILTER_SELECT),
            "$filter": (
                f"_sdkmessageid_value eq {params.message_id} and "
                f"primaryobjecttypecode eq '{escaped_entity}'"
            ),
            "$top": "1",
        }
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/sdkmessagefilters?{urlencode(query, safe='$,')}"
        )
        records = await paginate_records(url, headers, 1, app_ctx.http_client)
        if not records:
            return json.dumps({
                "error": True,
                "message": (
                    f"SDK message filter not found for message '{params.message_id}' "
                    f"and entity '{params.primary_entity}'"
                ),
            })
        record = records[0]
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_sdk_message_filter")


@mcp.tool(
    name="dataverse_list_sdk_message_filters",
    annotations={
        "title": "List SDK Message Filters",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_sdk_message_filters(
    params: ListSdkMessageFiltersInput, ctx: Context
) -> str:
    """List SDK message filters (which entities support each message).

    Optional: message_id (filters for one message), primary_entity (filter
    by entity logical name), filter (raw OData), top (default 50), select,
    dataverse_url.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_FILTER_SELECT
    top = params.top

    filters: list[str] = []
    if params.message_id:
        filters.append(f"_sdkmessageid_value eq {params.message_id}")
    if params.primary_entity:
        filters.append(
            f"primaryobjecttypecode eq '{odata_quote(params.primary_entity)}'"
        )
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
    }
    combined = _combine_filters(*filters)
    if combined:
        query["$filter"] = combined

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessagefilters?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_sdk_message_filters")


# ===========================================================================
# F. SDK message processing steps
# ===========================================================================


@mcp.tool(
    name="dataverse_get_plugin_step",
    annotations={
        "title": "Get Plug-in Step",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_plugin_step(
    params: GetPluginStepInput, ctx: Context
) -> str:
    """Retrieve a single SDK message processing step record.

    Required: step_id (GUID of the sdkmessageprocessingstep).
    Optional: select (columns), dataverse_url.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_STEP_SELECT
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessageprocessingsteps({params.step_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in step not found: '{params.step_id}'",
            })
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_plugin_step")


@mcp.tool(
    name="dataverse_list_plugin_steps",
    annotations={
        "title": "List Plug-in Steps",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_plugin_steps(
    params: ListPluginStepsInput, ctx: Context
) -> str:
    """List SDK message processing steps.

    Optional: plugin_type_id (steps handled by one plug-in type),
    message_id (steps for one message), filter (raw OData), top (default 50),
    select, dataverse_url. Results ordered by name ascending.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_STEP_SELECT
    top = params.top

    filters: list[str] = []
    if params.plugin_type_id:
        filters.append(f"_eventhandler_value eq {params.plugin_type_id}")
    if params.message_id:
        filters.append(f"_sdkmessageid_value eq {params.message_id}")
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
        "$orderby": "name asc",
    }
    combined = _combine_filters(*filters)
    if combined:
        query["$filter"] = combined

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessageprocessingsteps?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_plugin_steps")


@write_tool(
    name="dataverse_create_plugin_step",
    annotations={
        "title": "Create Plug-in Step",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_plugin_step(
    params: CreatePluginStepInput, ctx: Context
) -> str:
    """Register a plug-in step against a message (and optional entity filter).

    Required: name, plugin_type_id (GUID of the plugintype), message_id
    (GUID of the sdkmessage; resolve via dataverse_get_sdk_message),
    stage (10/20/40), mode (0/1).
    Optional: filter_id (scope to one entity; omit for all entities), rank
    (default 1), filtering_attributes (comma-separated; Update steps only),
    supported_deployment (default 0), async_auto_delete (default false),
    configuration (unsecure config string), description, dataverse_url.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "name": params.name,
        "stage": params.stage,
        "mode": params.mode,
        "rank": params.rank,
        "supporteddeployment": params.supported_deployment,
        "asyncautodelete": params.async_auto_delete,
        "eventhandler_plugintype@odata.bind": f"/plugintypes({params.plugin_type_id})",
        "sdkmessageid@odata.bind": f"/sdkmessages({params.message_id})",
    }
    if params.filter_id is not None:
        body["sdkmessagefilterid@odata.bind"] = f"/sdkmessagefilters({params.filter_id})"
    if params.filtering_attributes is not None:
        body["filteringattributes"] = params.filtering_attributes
    if params.configuration is not None:
        body["configuration"] = params.configuration
    if params.description is not None:
        body["description"] = params.description

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/sdkmessageprocessingsteps"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "POST", url, json=body, headers=headers
        )
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "name": params.name,
            "stage": params.stage,
            "mode": params.mode,
            "location": location,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_plugin_step")


@write_tool(
    name="dataverse_update_plugin_step",
    annotations={
        "title": "Update Plug-in Step",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_plugin_step(
    params: UpdatePluginStepInput, ctx: Context
) -> str:
    """Update an SDK message processing step (including enable/disable via state).

    Required: step_id. At least one of: name, rank, filtering_attributes,
    state ('enabled'/'disabled'), configuration, description.
    Use state to enable/disable the step without deleting it.
    stage, mode, and message are not mutable post-registration — re-create instead.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.name is not None:
        body["name"] = params.name
    if params.rank is not None:
        body["rank"] = params.rank
    if params.filtering_attributes is not None:
        body["filteringattributes"] = params.filtering_attributes
    if params.state is not None:
        if params.state == "disabled":
            body["statecode"] = 1
            body["statuscode"] = 2
        else:  # 'enabled'
            body["statecode"] = 0
            body["statuscode"] = 1
    if params.configuration is not None:
        body["configuration"] = params.configuration
    if params.description is not None:
        body["description"] = params.description

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessageprocessingsteps({params.step_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", url, json=body, headers=headers
        )
        resp.raise_for_status()
        return json.dumps({"updated": True, "step_id": params.step_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_plugin_step")


@delete_tool(
    name="dataverse_delete_plugin_step",
    annotations={
        "title": "Delete Plug-in Step",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_plugin_step(
    params: DeletePluginStepInput, ctx: Context
) -> str:
    """Delete an SDK message processing step.

    Required: step_id.

    WARNING: delete step images first.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessageprocessingsteps({params.step_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", url, headers=headers
        )
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in step not found: '{params.step_id}'",
            })
        resp.raise_for_status()
        return json.dumps({"deleted": True, "step_id": params.step_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_plugin_step")


# ===========================================================================
# G. SDK message processing step images
# ===========================================================================


@mcp.tool(
    name="dataverse_get_plugin_step_image",
    annotations={
        "title": "Get Plug-in Step Image",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_plugin_step_image(
    params: GetPluginStepImageInput, ctx: Context
) -> str:
    """Retrieve a single step image record.

    Required: image_id (GUID of the sdkmessageprocessingstepimage).
    Optional: select (columns), dataverse_url.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_IMAGE_SELECT
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessageprocessingstepimages({params.image_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in step image not found: '{params.image_id}'",
            })
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_plugin_step_image")


@mcp.tool(
    name="dataverse_list_plugin_step_images",
    annotations={
        "title": "List Plug-in Step Images",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_plugin_step_images(
    params: ListPluginStepImagesInput, ctx: Context
) -> str:
    """List step images, optionally scoped to one step.

    Optional: step_id (images for one step), filter (raw OData), top
    (default 50), select, dataverse_url.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_IMAGE_SELECT
    top = params.top

    filters: list[str] = []
    if params.step_id:
        filters.append(f"_sdkmessageprocessingstepid_value eq {params.step_id}")
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
    }
    combined = _combine_filters(*filters)
    if combined:
        query["$filter"] = combined

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessageprocessingstepimages?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_plugin_step_images")


@write_tool(
    name="dataverse_create_plugin_step_image",
    annotations={
        "title": "Create Plug-in Step Image",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_plugin_step_image(
    params: CreatePluginStepImageInput, ctx: Context
) -> str:
    """Register a pre/post image on a processing step.

    Required: step_id (GUID of the parent sdkmessageprocessingstep),
    image_type (0=PreImage, 1=PostImage, 2=Both), entity_alias (property
    bag key e.g. 'PreImage'), message_property_name (e.g. 'Target').
    Optional: name (defaults to entity_alias), attributes (comma-separated
    logical names; omit for all attributes), description, dataverse_url.

    Note: PostImage is only valid for post-operation steps.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "imagetype": params.image_type,
        "entityalias": params.entity_alias,
        "messagepropertyname": params.message_property_name,
        "sdkmessageprocessingstepid@odata.bind": (
            f"/sdkmessageprocessingsteps({params.step_id})"
        ),
    }
    if params.name is not None:
        body["name"] = params.name
    if params.attributes is not None:
        body["attributes"] = params.attributes
    if params.description is not None:
        body["description"] = params.description

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/sdkmessageprocessingstepimages"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "POST", url, json=body, headers=headers
        )
        resp.raise_for_status()
        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        return json.dumps({
            "created": True,
            "entity_alias": params.entity_alias,
            "image_type": params.image_type,
            "location": location,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_plugin_step_image")


@write_tool(
    name="dataverse_update_plugin_step_image",
    annotations={
        "title": "Update Plug-in Step Image",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_plugin_step_image(
    params: UpdatePluginStepImageInput, ctx: Context
) -> str:
    """Update a step image record.

    Required: image_id. At least one of: entity_alias, message_property_name,
    attributes, name.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.entity_alias is not None:
        body["entityalias"] = params.entity_alias
    if params.message_property_name is not None:
        body["messagepropertyname"] = params.message_property_name
    if params.attributes is not None:
        body["attributes"] = params.attributes
    if params.name is not None:
        body["name"] = params.name

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessageprocessingstepimages({params.image_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", url, json=body, headers=headers
        )
        resp.raise_for_status()
        return json.dumps({"updated": True, "image_id": params.image_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_plugin_step_image")


@delete_tool(
    name="dataverse_delete_plugin_step_image",
    annotations={
        "title": "Delete Plug-in Step Image",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_delete_plugin_step_image(
    params: DeletePluginStepImageInput, ctx: Context
) -> str:
    """Delete a step image record.

    Required: image_id.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sdkmessageprocessingstepimages({params.image_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", url, headers=headers
        )
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": f"Plug-in step image not found: '{params.image_id}'",
            })
        resp.raise_for_status()
        return json.dumps({"deleted": True, "image_id": params.image_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_plugin_step_image")
