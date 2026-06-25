"""Custom API CRUD tools for the Dataverse MCP server.

Covers three related Dataverse entities (standard OData, not metadata API):
  - customapi (entity set: customapis, PK: customapiid)
  - customapirequestparameter (entity set: customapirequestparameters, PK: customapirequestparameterid)
  - customapiresponseproperty (entity set: customapiresponseproperties, PK: customapiresponsepropertyid)

Key constraints from Dataverse:
  - uniquename, bindingtype, boundentitylogicalname, allowedcustomprocessingsteptype,
    isfunction, workflowsdkstepenabled are immutable after creation on customapi.
  - uniquename, customapiid, type, logicalentityname are immutable on parameters/properties.
  - Use CustomAPIId@odata.bind (nav prop name) when linking parameters/properties to a custom api.

Type enum (for both request parameters and response properties):
  0=Boolean, 1=DateTime, 2=Decimal, 3=Entity, 4=EntityCollection,
  5=EntityReference, 6=Float, 7=Integer, 8=Money, 9=Picklist,
  10=String, 11=StringArray, 12=Guid
"""

import json
import logging
import re
from urllib.parse import urlencode

import httpx  # noqa: F401  (used in tool_error_response)
from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("customapis")

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
    CreateCustomApiInput,
    CreateCustomApiRequestParameterInput,
    CreateCustomApiResponsePropertyInput,
    DeleteCustomApiInput,
    DeleteCustomApiRequestParameterInput,
    DeleteCustomApiResponsePropertyInput,
    GetCustomApiInput,
    ListCustomApiRequestParametersInput,
    ListCustomApiResponsePropertiesInput,
    ListCustomApisInput,
    UpdateCustomApiInput,
    UpdateCustomApiRequestParameterInput,
    UpdateCustomApiResponsePropertyInput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GUID regex for extracting new record id from OData-EntityId header
# ---------------------------------------------------------------------------

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# ---------------------------------------------------------------------------
# Default column selections
# ---------------------------------------------------------------------------

_DEFAULT_CUSTOM_API_SELECT = [
    "customapiid",
    "uniquename",
    "name",
    "displayname",
    "bindingtype",
    "isfunction",
    "isprivate",
    "allowedcustomprocessingsteptype",
]

_DEFAULT_REQUEST_PARAMETER_SELECT = [
    "customapirequestparameterid",
    "uniquename",
    "name",
    "displayname",
    "type",
    "isoptional",
]

_DEFAULT_RESPONSE_PROPERTY_SELECT = [
    "customapiresponsepropertyid",
    "uniquename",
    "name",
    "displayname",
    "type",
]

# ---------------------------------------------------------------------------
# Custom API — Read: list
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_custom_apis",
    annotations={
        "title": "List Custom APIs",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_custom_apis(
    params: ListCustomApisInput, ctx: Context
) -> str:
    """List Custom API records in the Dataverse environment.

    Returns customapiid, uniquename, name, displayname, bindingtype,
    isfunction, isprivate, and allowedcustomprocessingsteptype for each record.
    Use the optional filter parameter to narrow results with an OData $filter
    expression (e.g., "isprivate eq false", "isfunction eq true").
    bindingtype: 0=Global, 1=Entity, 2=EntityCollection.
    allowedcustomprocessingsteptype: 0=None, 1=AsyncOnly, 2=SyncAndAsync.
    Use dataverse_get_custom_api to fetch expanded request parameters and
    response properties for a specific Custom API.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    query: dict[str, str] = {
        "$select": ",".join(_DEFAULT_CUSTOM_API_SELECT),
        "$top": str(params.top),
    }
    if params.filter:
        query["$filter"] = params.filter

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/customapis"
        f"?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        return finalize_response({
            "custom_apis": records,
            "count": len(records),
            "has_more": len(records) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_custom_apis")


# ---------------------------------------------------------------------------
# Custom API — Read: get single
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_get_custom_api",
    annotations={
        "title": "Get Custom API",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_custom_api(
    params: GetCustomApiInput, ctx: Context
) -> str:
    """Retrieve a single Custom API record by its GUID, including expanded
    request parameters and response properties.

    Returns all default columns plus:
    - CustomAPIRequestParameters (customapirequestparameterid, uniquename,
      name, type, isoptional)
    - CustomAPIResponseProperties (customapiresponsepropertyid, uniquename,
      name, type)
    The @odata.context key is stripped from the response.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = ",".join(_DEFAULT_CUSTOM_API_SELECT)
    expand = (
        "CustomAPIRequestParameters($select=customapirequestparameterid,uniquename,name,type,isoptional),"
        "CustomAPIResponseProperties($select=customapiresponsepropertyid,uniquename,name,type)"
    )
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/customapis({params.custom_api_id})"
        f"?$select={select}&$expand={expand}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return finalize_response({"custom_api": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_custom_api")


# ---------------------------------------------------------------------------
# Custom API — Write: create
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_custom_api",
    annotations={
        "title": "Create Custom API",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_custom_api(
    params: CreateCustomApiInput, ctx: Context
) -> str:
    """Create a new Custom API record and return its GUID.

    Required: uniquename (include publisher prefix, immutable after creation),
    name (display name).
    Optional: displayname, description, binding_type (0=Global, 1=Entity,
    2=EntityCollection; default 0), is_function (bool, default false),
    is_private (bool, default false), allowed_custom_processing_step_type
    (0=None, 1=AsyncOnly, 2=SyncAndAsync; default 0),
    bound_entity_logical_name (required when binding_type is 1 or 2).

    Note: uniquename, binding_type, is_function, allowed_custom_processing_step_type,
    and bound_entity_logical_name are immutable after creation — choose carefully.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "uniquename": params.uniquename,
        "name": params.name,
        "displayname": params.displayname if params.displayname is not None else params.name,
        "bindingtype": params.binding_type,
        "isfunction": params.is_function,
        "isprivate": params.is_private,
        "allowedcustomprocessingsteptype": params.allowed_custom_processing_step_type,
    }
    if params.description is not None:
        body["description"] = params.description
    if params.bound_entity_logical_name is not None:
        body["boundentitylogicalname"] = params.bound_entity_logical_name

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/customapis"

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
                    "Custom API created but the new id could not be read from the "
                    "OData-EntityId response header."
                ),
            })
        new_id = m.group(0)
        logger.info("Created custom API '%s': id=%s", params.uniquename, new_id)
        return finalize_response({"created": True, "id": new_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_create_custom_api")


# ---------------------------------------------------------------------------
# Custom API — Write: update (mutable fields only)
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_custom_api",
    annotations={
        "title": "Update Custom API",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_custom_api(
    params: UpdateCustomApiInput, ctx: Context
) -> str:
    """Partially update a Custom API record (PATCH) — only mutable fields.

    Mutable fields: name, displayname, description, is_private,
    execute_privilege_name. Provide custom_api_id and at least one of
    the mutable fields. Immutable fields (uniquename, bindingtype, isfunction,
    allowedcustomprocessingsteptype, boundentitylogicalname) cannot be changed
    after creation. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.name is not None:
        body["name"] = params.name
    if params.displayname is not None:
        body["displayname"] = params.displayname
    if params.description is not None:
        body["description"] = params.description
    if params.is_private is not None:
        body["isprivate"] = params.is_private
    if params.execute_privilege_name is not None:
        body["executeprivilegename"] = params.execute_privilege_name

    patch_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/customapis({params.custom_api_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", patch_url, headers=headers, json=body
        )
        resp.raise_for_status()
        logger.info("Updated custom API %s", params.custom_api_id)
        return finalize_response({"updated": True, "id": params.custom_api_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_custom_api")


# ---------------------------------------------------------------------------
# Custom API — Delete
# ---------------------------------------------------------------------------


@delete_tool(
    name="dataverse_delete_custom_api",
    annotations={
        "title": "Delete Custom API",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_delete_custom_api(
    params: DeleteCustomApiInput, ctx: Context
) -> str:
    """Permanently delete a Custom API record by its GUID.

    This is irreversible and also cascades to delete all associated
    request parameters and response properties. Managed Custom APIs
    cannot be deleted directly — uninstall via the parent solution.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    delete_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/customapis({params.custom_api_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", delete_url, headers=headers
        )
        resp.raise_for_status()
        logger.info("Deleted custom API %s", params.custom_api_id)
        return finalize_response({"deleted": True, "id": params.custom_api_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_custom_api")


# ---------------------------------------------------------------------------
# Custom API Request Parameters — Read: list
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_custom_api_request_parameters",
    annotations={
        "title": "List Custom API Request Parameters",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_custom_api_request_parameters(
    params: ListCustomApiRequestParametersInput, ctx: Context
) -> str:
    """List request parameter records for a Custom API.

    Filters by custom_api_id (GUID) to return only parameters belonging
    to that Custom API. Returns customapirequestparameterid, uniquename,
    name, displayname, type, isoptional for each record.
    Type enum: 0=Boolean, 1=DateTime, 2=Decimal, 3=Entity,
    4=EntityCollection, 5=EntityReference, 6=Float, 7=Integer, 8=Money,
    9=Picklist, 10=String, 11=StringArray, 12=Guid.
    An optional OData filter expression can further narrow results.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    filters: list[str] = [
        f"_customapiid_value eq {params.custom_api_id}"
    ]
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(_DEFAULT_REQUEST_PARAMETER_SELECT),
        "$top": str(params.top),
        "$filter": " and ".join(filters),
    }

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/customapirequestparameters"
        f"?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        return finalize_response({
            "request_parameters": records,
            "count": len(records),
            "has_more": len(records) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_custom_api_request_parameters")


# ---------------------------------------------------------------------------
# Custom API Request Parameters — Write: create
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_custom_api_request_parameter",
    annotations={
        "title": "Create Custom API Request Parameter",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_custom_api_request_parameter(
    params: CreateCustomApiRequestParameterInput, ctx: Context
) -> str:
    """Create a new request parameter for a Custom API and return its GUID.

    Required: custom_api_id (GUID of the parent Custom API, immutable),
    uniquename (immutable after creation), type (int, immutable; 0=Boolean,
    1=DateTime, 2=Decimal, 3=Entity, 4=EntityCollection, 5=EntityReference,
    6=Float, 7=Integer, 8=Money, 9=Picklist, 10=String, 11=StringArray,
    12=Guid), name (display name).
    Optional: displayname, description, is_optional (bool, default false).
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "CustomAPIId@odata.bind": f"/customapis({params.custom_api_id})",
        "uniquename": params.uniquename,
        "type": params.type,
        "name": params.name,
        "displayname": params.displayname if params.displayname is not None else params.name,
        "isoptional": params.is_optional,
    }
    if params.description is not None:
        body["description"] = params.description

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/customapirequestparameters"

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
                    "Request parameter created but the new id could not be read "
                    "from the OData-EntityId response header."
                ),
            })
        new_id = m.group(0)
        logger.info(
            "Created custom API request parameter '%s': id=%s",
            params.uniquename, new_id,
        )
        return finalize_response({"created": True, "id": new_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_create_custom_api_request_parameter")


# ---------------------------------------------------------------------------
# Custom API Request Parameters — Write: update
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_custom_api_request_parameter",
    annotations={
        "title": "Update Custom API Request Parameter",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_custom_api_request_parameter(
    params: UpdateCustomApiRequestParameterInput, ctx: Context
) -> str:
    """Partially update a Custom API request parameter (PATCH) — mutable fields only.

    Mutable fields: name, displayname, description, is_optional. Provide
    request_parameter_id and at least one mutable field. Immutable fields
    (uniquename, customapiid, type, logicalentityname) cannot be changed.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.name is not None:
        body["name"] = params.name
    if params.displayname is not None:
        body["displayname"] = params.displayname
    if params.description is not None:
        body["description"] = params.description
    if params.is_optional is not None:
        body["isoptional"] = params.is_optional

    patch_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/customapirequestparameters({params.request_parameter_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", patch_url, headers=headers, json=body
        )
        resp.raise_for_status()
        logger.info(
            "Updated custom API request parameter %s", params.request_parameter_id
        )
        return finalize_response({"updated": True, "id": params.request_parameter_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_custom_api_request_parameter")


# ---------------------------------------------------------------------------
# Custom API Request Parameters — Delete
# ---------------------------------------------------------------------------


@delete_tool(
    name="dataverse_delete_custom_api_request_parameter",
    annotations={
        "title": "Delete Custom API Request Parameter",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_delete_custom_api_request_parameter(
    params: DeleteCustomApiRequestParameterInput, ctx: Context
) -> str:
    """Permanently delete a Custom API request parameter by its GUID.

    This is irreversible. The parent Custom API is not affected.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    delete_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/customapirequestparameters({params.request_parameter_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", delete_url, headers=headers
        )
        resp.raise_for_status()
        logger.info(
            "Deleted custom API request parameter %s", params.request_parameter_id
        )
        return finalize_response({"deleted": True, "id": params.request_parameter_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_custom_api_request_parameter")


# ---------------------------------------------------------------------------
# Custom API Response Properties — Read: list
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_custom_api_response_properties",
    annotations={
        "title": "List Custom API Response Properties",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_custom_api_response_properties(
    params: ListCustomApiResponsePropertiesInput, ctx: Context
) -> str:
    """List response property records for a Custom API.

    Filters by custom_api_id (GUID) to return only properties belonging
    to that Custom API. Returns customapiresponsepropertyid, uniquename,
    name, displayname, type for each record.
    Type enum: 0=Boolean, 1=DateTime, 2=Decimal, 3=Entity,
    4=EntityCollection, 5=EntityReference, 6=Float, 7=Integer, 8=Money,
    9=Picklist, 10=String, 11=StringArray, 12=Guid.
    An optional OData filter expression can further narrow results.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    filters: list[str] = [
        f"_customapiid_value eq {params.custom_api_id}"
    ]
    if params.filter:
        filters.append(params.filter)

    query: dict[str, str] = {
        "$select": ",".join(_DEFAULT_RESPONSE_PROPERTY_SELECT),
        "$top": str(params.top),
        "$filter": " and ".join(filters),
    }

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/customapiresponseproperties"
        f"?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        return finalize_response({
            "response_properties": records,
            "count": len(records),
            "has_more": len(records) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_custom_api_response_properties")


# ---------------------------------------------------------------------------
# Custom API Response Properties — Write: create
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_custom_api_response_property",
    annotations={
        "title": "Create Custom API Response Property",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_custom_api_response_property(
    params: CreateCustomApiResponsePropertyInput, ctx: Context
) -> str:
    """Create a new response property for a Custom API and return its GUID.

    Required: custom_api_id (GUID of the parent Custom API, immutable),
    uniquename (immutable after creation), type (int, immutable; 0=Boolean,
    1=DateTime, 2=Decimal, 3=Entity, 4=EntityCollection, 5=EntityReference,
    6=Float, 7=Integer, 8=Money, 9=Picklist, 10=String, 11=StringArray,
    12=Guid), name (display name).
    Optional: displayname, description.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "CustomAPIId@odata.bind": f"/customapis({params.custom_api_id})",
        "uniquename": params.uniquename,
        "type": params.type,
        "name": params.name,
        "displayname": params.displayname if params.displayname is not None else params.name,
    }
    if params.description is not None:
        body["description"] = params.description

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/customapiresponseproperties"

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
                    "Response property created but the new id could not be read "
                    "from the OData-EntityId response header."
                ),
            })
        new_id = m.group(0)
        logger.info(
            "Created custom API response property '%s': id=%s",
            params.uniquename, new_id,
        )
        return finalize_response({"created": True, "id": new_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_create_custom_api_response_property")


# ---------------------------------------------------------------------------
# Custom API Response Properties — Write: update
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_custom_api_response_property",
    annotations={
        "title": "Update Custom API Response Property",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_custom_api_response_property(
    params: UpdateCustomApiResponsePropertyInput, ctx: Context
) -> str:
    """Partially update a Custom API response property (PATCH) — mutable fields only.

    Mutable fields: name, displayname, description. Provide
    response_property_id and at least one mutable field. Immutable fields
    (uniquename, customapiid, type, logicalentityname) cannot be changed.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {}
    if params.name is not None:
        body["name"] = params.name
    if params.displayname is not None:
        body["displayname"] = params.displayname
    if params.description is not None:
        body["description"] = params.description

    patch_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/customapiresponseproperties({params.response_property_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", patch_url, headers=headers, json=body
        )
        resp.raise_for_status()
        logger.info(
            "Updated custom API response property %s", params.response_property_id
        )
        return finalize_response({"updated": True, "id": params.response_property_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_custom_api_response_property")


# ---------------------------------------------------------------------------
# Custom API Response Properties — Delete
# ---------------------------------------------------------------------------


@delete_tool(
    name="dataverse_delete_custom_api_response_property",
    annotations={
        "title": "Delete Custom API Response Property",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_delete_custom_api_response_property(
    params: DeleteCustomApiResponsePropertyInput, ctx: Context
) -> str:
    """Permanently delete a Custom API response property by its GUID.

    This is irreversible. The parent Custom API is not affected.
    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    delete_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/customapiresponseproperties({params.response_property_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", delete_url, headers=headers
        )
        resp.raise_for_status()
        logger.info(
            "Deleted custom API response property %s", params.response_property_id
        )
        return finalize_response({"deleted": True, "id": params.response_property_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_custom_api_response_property")
