"""Environment discovery tools for the Power Platform admin API."""

import asyncio
import json
import logging
from typing import Any

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import mcp
from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    build_headers,
    extract_error_message,
    finalize_response,
    get_app_ctx,
    get_bearer_token,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import GetEntitySetsInput, ListEnvironmentsInput, RetrievePrincipalAccessInput, RetrieveUserPrivilegesInput, WhoAmIInput

logger = logging.getLogger(__name__)

_ENVIRONMENTS_ENDPOINT = (
    "https://api.bap.microsoft.com/providers/"
    "Microsoft.BusinessAppPlatform/scopes/admin/environments"
)
_ENVIRONMENTS_SCOPE = "https://service.powerapps.com/.default"
_ENVIRONMENTS_API_VERSION = "2020-10-01"


def _normalize_environment(raw_environment: dict[str, Any]) -> dict[str, Any]:
    """Normalize the raw Power Platform admin response into an agent-friendly shape."""
    properties = raw_environment.get("properties", {})
    linked = properties.get("linkedEnvironmentMetadata", {})
    states = properties.get("states", {})

    return {
        "environment_id": raw_environment.get("name") or linked.get("resourceId"),
        "display_name": properties.get("displayName") or linked.get("friendlyName"),
        "location": raw_environment.get("location"),
        "environment_sku": properties.get("environmentSku"),
        "is_default": properties.get("isDefault"),
        "instance_url": linked.get("instanceUrl"),
        "instance_api_url": linked.get("instanceApiUrl"),
        "unique_name": linked.get("uniqueName"),
        "domain_name": linked.get("domainName"),
        "management_state": states.get("management", {}).get("id"),
        "runtime_state": states.get("runtime", {}).get("id"),
        "azure_region": properties.get("azureRegion"),
        "created_time": properties.get("createdTime"),
        "description": properties.get("description"),
        "properties": properties,
    }


@mcp.tool(
    name="dataverse_list_environments",
    annotations={
        "title": "List Environments",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_environments(
    params: ListEnvironmentsInput, ctx: Context
) -> str:
    """List Power Platform environments available to the authenticated user.

    This tool uses the Power Platform admin API and does not require a
    dataverse_url. Use it to discover available environments before calling
    environment-specific Dataverse tools.
    """
    app_ctx = get_app_ctx(ctx)

    try:
        bearer_token = await asyncio.to_thread(
            get_bearer_token,
            app_ctx,
            _ENVIRONMENTS_SCOPE,
        )

        expand_values: list[str] = []
        if params.expand_capacity:
            expand_values.append("properties.capacity")
        if params.expand_addons:
            expand_values.append("properties.addons")

        query_params: dict[str, Any] = {"api-version": _ENVIRONMENTS_API_VERSION}
        if expand_values:
            query_params["$expand"] = ",".join(expand_values)

        response = await request_with_retry(app_ctx.http_client, "GET",
            _ENVIRONMENTS_ENDPOINT,
            params=query_params,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        payload = response.json()
        environments = [
            _normalize_environment(raw_environment)
            for raw_environment in payload.get("value", [])
        ]

        return finalize_response({
            "environments": environments,
            "count": len(environments),
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error(
            "Power Platform admin API error: %s (status=%d)",
            msg,
            e.response.status_code,
        )
        return json.dumps({
            "error": True,
            "message": (
                "Power Platform admin API returned HTTP "
                f"{e.response.status_code}: {msg}"
            ),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_environments")


# ---------------------------------------------------------------------------
# Service discovery tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_whoami",
    annotations={
        "title": "Who Am I",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_whoami(params: WhoAmIInput, ctx: Context) -> str:
    """Return the authenticated user's identity from the Dataverse WhoAmI endpoint.

    Returns UserId, BusinessUnitId, and OrganizationId for the caller.
    Call this at the start of a session to confirm authentication is working
    and to obtain the caller's system user GUID (useful for privilege checks
    or filtering records owned by the current user).
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(app_ctx.http_client, "GET",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/WhoAmI",
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        return json.dumps({
            "UserId": payload.get("UserId"),
            "BusinessUnitId": payload.get("BusinessUnitId"),
            "OrganizationId": payload.get("OrganizationId"),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_whoami")


@mcp.tool(
    name="dataverse_get_entity_sets",
    annotations={
        "title": "Get Entity Sets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_entity_sets(params: GetEntitySetsInput, ctx: Context) -> str:
    """List OData EntitySet names available in the Dataverse environment.

    Queries the OData service document and returns each EntitySet's name and url.
    Use this to discover the exact entity_set_name for a table before composing
    record query URLs — faster and smaller than fetching the full $metadata document.

    For example, the 'account' table has EntitySet name 'accounts', and
    'systemuser' has EntitySet name 'systemusers'.

    Use 'contains' to filter by a substring and 'top' to limit results.
    Check 'has_more' in the response to determine if additional entries exist.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(app_ctx.http_client, "GET",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/",
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        all_entries = payload.get("value", [])

        # Apply optional substring filter
        if params.contains:
            needle = params.contains.lower()
            all_entries = [
                e for e in all_entries if needle in (e.get("name") or "").lower()
            ]

        has_more = len(all_entries) > params.top
        entity_sets = [
            {"name": entry.get("name"), "url": entry.get("url")}
            for entry in all_entries[: params.top]
        ]
        return finalize_response({
            "entity_sets": entity_sets,
            "count": len(entity_sets),
            "has_more": has_more,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_get_entity_sets")


# ---------------------------------------------------------------------------
# Security tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_retrieve_user_privileges",
    annotations={
        "title": "Retrieve User Privileges",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_retrieve_user_privileges(
    params: RetrieveUserPrivilegesInput, ctx: Context
) -> str:
    """Retrieve all security privileges assigned to a system user via their roles.

    Returns a list of RolePrivilege objects, each containing PrivilegeName,
    Depth (Basic/Local/Deep/Global/None), and BusinessUnitId.

    Use dataverse_whoami to get the current caller's UserId, then call this
    tool to verify available privileges before attempting operations that may
    fail due to missing permissions.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(app_ctx.http_client, "GET",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/systemusers({params.user_id})"
            f"/Microsoft.Dynamics.CRM.RetrieveUserPrivileges",
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        privileges = payload.get("RolePrivileges", [])
        return finalize_response({
            "privileges": privileges,
            "count": len(privileges),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_retrieve_user_privileges")


@mcp.tool(
    name="dataverse_retrieve_principal_access",
    annotations={
        "title": "Retrieve Principal Access",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_retrieve_principal_access(
    params: RetrievePrincipalAccessInput, ctx: Context
) -> str:
    """Return the access rights a system user has to a specific record.

    Returns the AccessRights bitmask and a list of named rights
    (ReadAccess, WriteAccess, DeleteAccess, AssignAccess, ShareAccess, etc.).

    Use this to confirm whether a user can act on a record before delegating
    an operation that may fail if the user lacks the required access.

    entity_set_name is the OData collection name (e.g., 'accounts', 'contacts').
    Use dataverse_get_entity_sets to discover the correct name.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    target_ref = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{params.entity_set_name}({params.record_id})"

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(app_ctx.http_client, "GET",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/systemusers({params.user_id})"
            f"/Microsoft.Dynamics.CRM.RetrievePrincipalAccess"
            f"(Target=@tid)",
            params={"@tid": f"{{'@odata.id':'{target_ref}'}}"},
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        access_rights = payload.get("AccessRights", "")

        # Parse the bitmask string into named access rights
        named_rights = [
            right.strip()
            for right in access_rights.split(",")
            if right.strip()
        ] if access_rights else []

        return json.dumps({
            "access_rights": access_rights,
            "named_rights": named_rights,
            "user_id": params.user_id,
            "entity_set_name": params.entity_set_name,
            "record_id": params.record_id,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_retrieve_principal_access")