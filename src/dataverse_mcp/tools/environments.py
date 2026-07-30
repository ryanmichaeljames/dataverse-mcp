"""Environment discovery tools for the Power Platform admin API."""

import asyncio
import json
import logging
from typing import Any

import httpx
from mcp.server.mcpserver import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("core")
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
from dataverse_mcp.models import GetEntitySetsInput, GetOrganizationInfoInput, ListEnvironmentsInput, RetrievePrincipalAccessInput, RetrieveUserPrivilegesInput, WhoAmIInput

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


@tool(
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

    Uses the Power Platform admin API — no dataverse_url required.
    Returns instance_url for each environment, which is the dataverse_url
    for all other tools. Use this to discover environments before
    calling environment-specific Dataverse tools.
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


@tool(
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
    """Return the authenticated caller's identity from the Dataverse WhoAmI endpoint.

    Returns UserId, BusinessUnitId, and OrganizationId. Call at session start
    to confirm authentication and get the caller's UserId for privilege checks.
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


# Members of the Microsoft.Dynamics.CRM.EndpointAccessType enum accepted by
# RetrieveCurrentOrganization. The member name is interpolated into the request
# URL as an OData enum literal, so it is re-checked here even though the input
# model already constrains it: the URL must never carry an unvalidated string.
_ENDPOINT_ACCESS_TYPES = frozenset({"Default", "Internet", "Intranet"})


def _strip_odata_envelope(payload: Any) -> dict[str, Any]:
    """Return *payload* without its top-level ``@odata.*`` envelope keys.

    Only the envelope is dropped. Property names, nesting, and values are passed
    through untouched because the exact response shapes of these functions vary by
    org and platform version and must not be guessed at.
    """
    if not isinstance(payload, dict):
        return {"value": payload}
    return {k: v for k, v in payload.items() if not k.startswith("@odata.")}


def _extract_version(payload: dict[str, Any]) -> str | None:
    """Return the server version from a RetrieveVersion payload.

    ``Version`` is the confirmed property name, so it is read directly. The
    lone-string fallback is retained for an org that names it otherwise: the
    payload carries exactly one string property, which is unambiguous. Returns
    None when neither applies so the caller can omit the convenience field
    rather than emit a guess or a null.
    """
    version = payload.get("Version")
    if isinstance(version, str) and version:
        return version
    strings = [v for v in payload.values() if isinstance(v, str) and v]
    if len(strings) == 1:
        return strings[0]
    return None


# Confirmed shape of RetrieveOrganizationInfo: {"organizationInfo": {..., "Solutions": [...]}}.
# The solution list runs to several hundred entries on a real org, so it is
# summarized to a count unless the caller explicitly opts in.
_ORG_INFO_CONTAINER_KEY = "organizationinfo"
_SOLUTIONS_KEY = "Solutions"


def _summarize_solutions(
    payload: dict[str, Any], *, include_solutions: bool
) -> dict[str, Any]:
    """Replace the ``Solutions`` array with ``solutions_count`` unless opted in.

    Every other property is passed through in its original order. The payload is
    returned untouched when the container or the ``Solutions`` array is missing or
    is not the expected type — ``solutions_count`` is omitted rather than guessed.
    """
    container_key = next(
        (
            key
            for key, value in payload.items()
            if key.lower() == _ORG_INFO_CONTAINER_KEY and isinstance(value, dict)
        ),
        None,
    )
    if container_key is None:
        return payload

    container: dict[str, Any] = payload[container_key]
    solutions = container.get(_SOLUTIONS_KEY)
    if not isinstance(solutions, list):
        if solutions is not None:
            logger.warning(
                "RetrieveOrganizationInfo returned %s of unexpected type %s; "
                "passing it through without solutions_count",
                _SOLUTIONS_KEY,
                type(solutions).__name__,
            )
        return payload

    summarized: dict[str, Any] = {}
    for key, value in container.items():
        if key == _SOLUTIONS_KEY:
            summarized["solutions_count"] = len(value)
            if include_solutions:
                summarized[key] = value
        else:
            summarized[key] = value
    return {**payload, container_key: summarized}


async def _call_org_function(
    app_ctx: Any,
    headers: dict[str, str],
    function_name: str,
    url: str,
    partial_errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    """GET an unbound function, recording a partial error instead of propagating.

    Returns the envelope-stripped payload, or None when the call failed — in which
    case an entry is appended to *partial_errors* so one unavailable or
    privilege-gated function cannot fail the whole tool.
    """
    try:
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()
        return _strip_odata_envelope(response.json())
    except httpx.HTTPStatusError as e:
        message = (
            f"Dataverse returned HTTP {e.response.status_code}: "
            f"{extract_error_message(e.response)}"
        )
    except Exception as e:  # network, JSON decode, auth — never fatal for one call
        message = f"{type(e).__name__}: {e}"
    logger.warning(
        "%s failed during dataverse_get_organization_info: %s", function_name, message
    )
    partial_errors.append({"function": function_name, "message": message})
    return None


@tool(
    name="dataverse_get_organization_info",
    annotations={
        "title": "Get Organization Info",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_organization_info(
    params: GetOrganizationInfoInput, ctx: Context
) -> str:
    """Fingerprint a Dataverse environment: server version, organization identity, endpoints.

    Merges three unbound Web API functions — RetrieveVersion,
    RetrieveCurrentOrganization, and RetrieveOrganizationInfo. Call this before any
    risky operation to confirm which environment you are pointed at.

    To tell a non-production environment from production, read
    organization_info.organizationInfo.InstanceType or
    current_organization.Detail.OrganizationType. Both are strings, and their values
    are distinct per tier — a developer-tier org reports "Developer", not "Sandbox" —
    so never test only for "Sandbox" when deciding whether an environment is safe to
    change. Identity lives alongside them: Detail.UniqueName, Detail.FriendlyName,
    Detail.EnvironmentId, Detail.Geo, and Detail.State.

    RetrieveOrganizationInfo also returns every installed solution. That list runs to
    several hundred entries, so it is replaced by
    organization_info.organizationInfo.solutions_count. Set include_solutions=true to
    get the full Solutions array as well, but prefer dataverse_list_solutions for
    browsing solutions.

    Each function is called independently. If one is unavailable or privilege-gated
    its failure is reported in partial_errors and the remaining data is still
    returned; only a failure of all three yields an error response. Apart from the
    solution summarization, payloads are returned as Dataverse produced them, minus
    the @odata envelope keys.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    access_type = params.access_type
    if access_type not in _ENDPOINT_ACCESS_TYPES:
        return json.dumps({
            "error": True,
            "message": (
                f"access_type must be one of "
                f"{', '.join(sorted(_ENDPOINT_ACCESS_TYPES))}; got {access_type!r}."
            ),
        })

    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
    partial_errors: list[dict[str, str]] = []

    try:
        headers = await build_headers(app_ctx, base_url)

        version_payload = await _call_org_function(
            app_ctx,
            headers,
            "RetrieveVersion",
            f"{api_root}/RetrieveVersion()",
            partial_errors,
        )
        current_org_payload = await _call_org_function(
            app_ctx,
            headers,
            "RetrieveCurrentOrganization",
            f"{api_root}/RetrieveCurrentOrganization"
            f"(AccessType=Microsoft.Dynamics.CRM.EndpointAccessType'{access_type}')",
            partial_errors,
        )
        org_info_payload = await _call_org_function(
            app_ctx,
            headers,
            "RetrieveOrganizationInfo",
            f"{api_root}/RetrieveOrganizationInfo()",
            partial_errors,
        )
    except Exception as e:
        # Only reached for failures outside the per-function calls (e.g. token
        # acquisition), which affect all three equally.
        return tool_error_response(e, "dataverse_get_organization_info")

    if len(partial_errors) == 3:
        details = "; ".join(
            f"{entry['function']}: {entry['message']}" for entry in partial_errors
        )
        return json.dumps({
            "error": True,
            "message": (
                "Could not retrieve organization information: all three functions "
                f"failed. {details}"
            ),
        })

    result: dict[str, Any] = {"access_type": access_type}
    if version_payload is not None:
        version = _extract_version(version_payload)
        if version is not None:
            result["version"] = version
        result["retrieve_version"] = version_payload
    if current_org_payload is not None:
        result["current_organization"] = current_org_payload
    if org_info_payload is not None:
        result["organization_info"] = _summarize_solutions(
            org_info_payload, include_solutions=params.include_solutions
        )
    result["partial_errors"] = partial_errors

    return finalize_response(result)


@tool(
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
    """List OData EntitySet names from the Dataverse service document.

    Use this to discover the correct entity_set_name for a table before
    querying records (e.g., 'account' → 'accounts', 'systemuser' → 'systemusers').
    Faster and smaller than fetching $metadata. Filter with contains.
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


@tool(
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

    Returns RolePrivilege objects with PrivilegeName and Depth. Use
    dataverse_whoami to get the caller's UserId for checking your own privileges.
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


@tool(
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
    """Return the access rights a system user has to a specific Dataverse record.

    Returns the AccessRights bitmask and named rights (ReadAccess, WriteAccess,
    DeleteAccess, etc.). Use before delegating an operation to confirm the user
    can act on the record.
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