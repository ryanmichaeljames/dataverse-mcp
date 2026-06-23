"""Web resource CRUD tools for the Dataverse MCP server.

Covers webresource records (entity set: webresourceset, PK: webresourceid).
Verified against a live Dataverse Web API v9.2 org (EntityDefinitions):
  - Entity set: webresourceset  (NOTE: irregular plural — NOT "webresources")
  - Primary key: webresourceid
  - Required on create: name, webresourcetype, content (base64)
  - webresourcetype Picklist:
      1=HTML, 2=CSS, 3=JScript, 4=XML, 5=PNG, 6=JPG, 7=GIF,
      8=Silverlight(XAP), 9=StyleSheet(XSL), 10=ICO,
      11=Vector(SVG), 12=String(RESX)

Publishing note: creating or updating a web resource does NOT automatically
publish it. Call dataverse_publish_customizations (already available in the
schema category) after write operations to make changes visible to users.
"""

import json
import logging
import re
from urllib.parse import urlencode

import httpx  # noqa: F401  (used in tool_error_response)
from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("webresources")
from dataverse_mcp.client import (  # noqa: E402
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
from dataverse_mcp.models import (  # noqa: E402
    CreateWebResourceInput,
    DeleteWebResourceInput,
    GetWebResourceInput,
    ListWebResourcesInput,
    UpdateWebResourceInput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GUID regex for extracting new record id from OData-EntityId header
# ---------------------------------------------------------------------------

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# ---------------------------------------------------------------------------
# Default column selection (exclude content — it can be very large)
# ---------------------------------------------------------------------------

_DEFAULT_WEB_RESOURCE_SELECT = [
    "webresourceid",
    "name",
    "displayname",
    "webresourcetype",
    "description",
    "languagecode",
    "ismanaged",
    "iscustomizable",
    "createdon",
    "modifiedon",
]

# ---------------------------------------------------------------------------
# Human-readable labels for webresourcetype values
# ---------------------------------------------------------------------------

_WEBRESOURCETYPE_LABELS: dict[int, str] = {
    1: "HTML",
    2: "CSS",
    3: "JScript",
    4: "XML",
    5: "PNG",
    6: "JPG",
    7: "GIF",
    8: "Silverlight (XAP)",
    9: "StyleSheet (XSL)",
    10: "ICO",
    11: "Vector (SVG)",
    12: "String (RESX)",
}


def _enrich_record(record: dict) -> dict:
    """Add webresourcetype_label to *record* in-place."""
    wrt = record.get("webresourcetype")
    if wrt is not None:
        record["webresourcetype_label"] = _WEBRESOURCETYPE_LABELS.get(wrt, str(wrt))
    return record


# ---------------------------------------------------------------------------
# Read: list web resources
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_web_resources",
    annotations={
        "title": "List Web Resources",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_web_resources(
    params: ListWebResourcesInput, ctx: Context
) -> str:
    """List webresource records in the Dataverse environment.

    Returns webresourceid, name, displayname, webresourcetype (+ label),
    description, languagecode, ismanaged, iscustomizable, createdon, modifiedon.
    Content is excluded from list results (it can be very large); use
    dataverse_get_web_resource with include_content=true to retrieve it.
    Filter by web_resource_type (1=HTML, 2=CSS, 3=JScript, 4=XML, 5=PNG,
    6=JPG, 7=GIF, 8=XAP, 9=XSL, 10=ICO, 11=SVG, 12=RESX) and/or
    name_contains for a case-sensitive substring match on the name field.
    After creating or updating a web resource, call
    dataverse_publish_customizations to make changes live.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_WEB_RESOURCE_SELECT
    top = params.top

    filter_parts: list[str] = []
    if params.web_resource_type is not None:
        filter_parts.append(f"webresourcetype eq {params.web_resource_type}")
    if params.name_contains is not None:
        filter_parts.append(f"contains(name,'{odata_quote(params.name_contains)}')")

    query_params: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
        "$orderby": "name",
    }
    if filter_parts:
        query_params["$filter"] = " and ".join(filter_parts)

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/webresourceset"
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
        return tool_error_response(e, "dataverse_list_web_resources")


# ---------------------------------------------------------------------------
# Read: get a single web resource
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_get_web_resource",
    annotations={
        "title": "Get Web Resource",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_web_resource(
    params: GetWebResourceInput, ctx: Context
) -> str:
    """Retrieve a single webresource record by its GUID.

    Returns webresourceid, name, displayname, webresourcetype (+ label),
    description, languagecode, ismanaged, iscustomizable, createdon, modifiedon.
    Set include_content=true to also retrieve the base64-encoded content field
    (may be large for images and script bundles — a 5 MB cap applies).

    IMPORTANT: this returns the PUBLISHED version of the web resource. Edits made
    via dataverse_update_web_resource are saved to the unpublished draft and will
    NOT appear here until you call dataverse_publish_customizations for this web
    resource. (The Dataverse RetrieveUnpublished message — not exposed by this
    server — is what tooling uses to read the draft before publishing.)
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = list(_DEFAULT_WEB_RESOURCE_SELECT)
    if params.include_content and "content" not in select:
        select.append("content")

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/webresourceset({params.web_resource_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        _enrich_record(record)
        return finalize_response({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_web_resource")


# ---------------------------------------------------------------------------
# Write: create a web resource
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_web_resource",
    annotations={
        "title": "Create Web Resource",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_web_resource(
    params: CreateWebResourceInput, ctx: Context
) -> str:
    """Create a webresource record and return the new record's GUID.

    Required fields: name (with publisher prefix, e.g. 'new_/scripts/app.js'),
    web_resource_type (1=HTML, 2=CSS, 3=JScript, 4=XML, 5=PNG, 6=JPG, 7=GIF,
    8=XAP, 9=XSL, 10=ICO, 11=SVG, 12=RESX), and content (base64-encoded bytes).
    Optional: display_name, description.

    IMPORTANT: Creating a web resource does NOT publish it automatically.
    Call dataverse_publish_customizations after this tool to make the resource
    visible to users. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "name": params.name,
        "webresourcetype": params.web_resource_type,
        "content": params.content,
    }
    if params.display_name is not None:
        body["displayname"] = params.display_name
    if params.description is not None:
        body["description"] = params.description

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/webresourceset"

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client, "POST", url, headers=headers, json=body
        )
        resp.raise_for_status()

        entity_id_header = resp.headers.get("OData-EntityId", "")
        m = _GUID_RE.search(entity_id_header)
        if not m:
            return json.dumps({
                "error": True,
                "message": (
                    "Web resource created but the new id could not be read from the "
                    "OData-EntityId response header."
                ),
            })
        new_id = m.group(0)
        logger.info("Created web resource '%s': id=%s", params.name, new_id)
        return finalize_response({"created": True, "id": new_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_create_web_resource")


# ---------------------------------------------------------------------------
# Write: update a web resource
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_web_resource",
    annotations={
        "title": "Update Web Resource",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_web_resource(
    params: UpdateWebResourceInput, ctx: Context
) -> str:
    """Partially update a webresource record (PATCH) — only supplied fields change.

    Provide web_resource_id and at least one of: content (base64),
    display_name, description.

    IMPORTANT: Updating a web resource does NOT publish it automatically. The
    PATCH succeeds (HTTP 204) and saves to the UNPUBLISHED draft, but a normal
    dataverse_get_web_resource still returns the previously published content
    until you call dataverse_publish_customizations for this web resource.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.content is not None:
        body["content"] = params.content
    if params.display_name is not None:
        body["displayname"] = params.display_name
    if params.description is not None:
        body["description"] = params.description

    patch_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/webresourceset({params.web_resource_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", patch_url, headers=headers, json=body
        )
        resp.raise_for_status()
        logger.info("Updated web resource %s", params.web_resource_id)
        return finalize_response({"updated": True, "id": params.web_resource_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_web_resource")


# ---------------------------------------------------------------------------
# Delete: delete a web resource
# ---------------------------------------------------------------------------


@delete_tool(
    name="dataverse_delete_web_resource",
    annotations={
        "title": "Delete Web Resource",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_delete_web_resource(
    params: DeleteWebResourceInput, ctx: Context
) -> str:
    """Permanently delete a webresource record by its GUID.

    This is irreversible. Managed web resources (ismanaged=true) cannot be
    deleted directly — they must be uninstalled via their parent solution.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    delete_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/webresourceset({params.web_resource_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", delete_url, headers=headers
        )
        resp.raise_for_status()
        logger.info("Deleted web resource %s", params.web_resource_id)
        return finalize_response({"deleted": True, "id": params.web_resource_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_web_resource")
