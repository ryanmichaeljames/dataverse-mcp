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
    encode_odata_literal,
    extract_error_message,
    finalize_response,
    get_app_ctx,
    get_bearer_token,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import GetEntitySetsInput, GetOrganizationInfoInput, GetSettingInput, ListEnvironmentsInput, RetrievePrincipalAccessInput, RetrieveUserPrivilegesInput, WhoAmIInput

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


# ---------------------------------------------------------------------------
# Effective configuration (RetrieveSetting)
# ---------------------------------------------------------------------------

# CONFIRMED LIVE (v9.2): RetrieveSetting answers with a single named container,
#   {"SettingDetail": {"Name": "...", "Value": "false", "DataType": 2}}
# — the same "one wrapper object" shape RetrieveCurrentOrganization (Detail) and
# RetrieveOrganizationInfo (organizationInfo) use. The value is therefore NESTED,
# never a top-level property, which is why a top-level-only lookup found nothing
# on every real call.
_SETTING_DETAIL_KEY = "SettingDetail"

# Property names the value may sit under INSIDE the container. "Value" is the
# confirmed one; the other two are retained as a cheap defensive fallback for an
# org or platform version that names it otherwise.
_SETTING_VALUE_KEYS = ("Value", "SettingValue", "Setting")

# Siblings of Value inside the container, surfaced verbatim. DataType is an
# INTEGER CODE and is passed through UNMAPPED: the code-to-type-name table has not
# been verified, so naming it would be a guess — the same reason `Depth` is left
# unrelabelled on the role/team tools.
_SETTING_NAME_PROPERTY = "Name"
_SETTING_DATA_TYPE_PROPERTY = "DataType"

# Scalar JSON types. ``bool`` is listed for the reader even though
# isinstance(True, int) already holds — harmless here because every scalar is
# accepted and none is compared against another type. A setting's value really can
# be a bool, so it must not be filtered out the way a bool must be excluded when
# something is being counted.
_SCALAR_TYPES = (str, int, float, bool)


def _is_setting_scalar(value: Any) -> bool:
    """True for a JSON scalar. ``None`` is deliberately NOT one.

    An explicit null is "nothing to report", not a setting value, and a missing
    property reads as None too — so treating null as an answer would let an absent
    property normalize into ``setting_value: null``.
    """
    return isinstance(value, _SCALAR_TYPES)


def _setting_is_absent(payload: Any) -> bool:
    """True when Dataverse said, successfully, that no such setting exists.

    CONFIRMED LIVE: an unrecognized setting name is NOT an error. Dataverse answers
    HTTP 200 with ``{"SettingDetail": null}`` — an explicit null container. That is
    a different answer from "the setting exists and its value is false/0/empty",
    and the two must never collapse into the same response.
    """
    return (
        isinstance(payload, dict)
        and _SETTING_DETAIL_KEY in payload
        and payload[_SETTING_DETAIL_KEY] is None
    )


def _extract_setting_value(payload: Any) -> tuple[str, Any] | None:
    """Return the setting's value as ``(source, value)``, or None.

    Two tiers — confirmed path first, defensive fallback second — exactly as
    ``_extract_version`` above and ``_extract_verdict`` (tools\\metadata.py):

    1. CONFIRMED: ``SettingDetail.Value``, the container shape every live call
       returns. The other ``_SETTING_VALUE_KEYS`` names are tried inside the same
       container in case a platform version renames the property. Once the
       container is present this tier is authoritative: no value inside it means a
       shape that is not understood, NOT a value hiding elsewhere in the envelope.
    2. DEFENSIVE: a differently-shaped payload with no container at all — a
       top-level candidate name, then a body carrying exactly ONE scalar property,
       which is unambiguous. This is the tier that would carry the tool through a
       response shape change.

    Membership is tested with ``_is_setting_scalar``, never truthiness: a setting
    whose value is ``""``, ``0`` or ``False`` is still what the platform computed
    and must not fall through to the raw path — those are the very values a
    configuration diff cares about. Note the live value arrives as the STRING
    ``"false"``, not a JSON boolean, and is passed through as sent. Anything else —
    an unrecognized container, two or more scalars with none of the candidate names
    among them, or a non-object body — returns None so the caller degrades to raw
    pass-through rather than being told a value that may not be the value.
    """
    if not isinstance(payload, dict):
        return None

    detail = payload.get(_SETTING_DETAIL_KEY)
    if isinstance(detail, dict):
        for key in _SETTING_VALUE_KEYS:
            candidate = detail.get(key)
            if _is_setting_scalar(candidate):
                return (f"{_SETTING_DETAIL_KEY}.{key}", candidate)
        return None
    if _SETTING_DETAIL_KEY in payload and detail is None:
        # Handled by _setting_is_absent — an absent setting is not a value.
        return None

    for key in _SETTING_VALUE_KEYS:
        candidate = payload.get(key)
        if _is_setting_scalar(candidate):
            return (key, candidate)
    scalars = [(k, v) for k, v in payload.items() if _is_setting_scalar(v)]
    if len(scalars) != 1:
        return None
    return scalars[0]


def _extract_setting_detail_metadata(payload: Any) -> dict[str, Any]:
    """Return the ``SettingDetail`` siblings worth surfacing, verbatim.

    ``Name`` is the setting's own name as the platform reports it (which lets a
    caller confirm the name it asked for is the name it got) and ``DataType`` is
    an integer code, returned UNMAPPED — live, ``DataType: 2`` accompanied the
    string ``"false"``, and no verified code-to-type-name table exists, so
    relabelling it would be a guess. A property that is missing or of an
    unexpected type is omitted rather than reported as null.
    """
    if not isinstance(payload, dict):
        return {}
    detail = payload.get(_SETTING_DETAIL_KEY)
    if not isinstance(detail, dict):
        return {}

    metadata: dict[str, Any] = {}
    name = detail.get(_SETTING_NAME_PROPERTY)
    if isinstance(name, str) and name:
        metadata["setting_detail_name"] = name
    data_type = detail.get(_SETTING_DATA_TYPE_PROPERTY)
    if isinstance(data_type, int) and not isinstance(data_type, bool):
        metadata["setting_data_type"] = data_type
    return metadata


@tool(
    name="dataverse_get_setting",
    annotations={
        "title": "Get Setting",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_setting(params: GetSettingInput, ctx: Context) -> str:
    """Read one setting's FINAL COMPUTED value for this environment.

    Calls the unbound RetrieveSetting function, which returns the value actually
    in effect after the platform has applied its precedence rules, rather than a
    raw configuration row that only tells you what someone stored at one level.
    That makes it the tool for diffing configuration between environments: compare
    computed values, not rows.

    It reads a NAMED setting from the settings framework, addressed by its unique
    name. It is not a general reader for the organization row: a column such as
    plugintracelogsetting is not a setting name, and
    dataverse_get_plugin_trace_log_setting (or dataverse_query_table over
    organizations) is what reads those.

    Omit app_unique_name to read the ORGANIZATION-level value. Supply the unique
    name of a model-driven app to read the value as that app sees it, which can
    differ where an app-level override exists. The two are different requests: when
    app_unique_name is omitted the parameter is left out of the call entirely
    rather than sent empty.

    THE VALUE IS NESTED. Microsoft Learn documents RetrieveSettingResponse but not
    its inner properties; live, v9.2 answers
    {"SettingDetail": {"Name": ..., "Value": "false", "DataType": 2}}. setting_value
    is lifted out of that container and setting_value_source says where it came
    from (normally SettingDetail.Value). setting_detail_name and setting_data_type
    carry its siblings; DataType is an INTEGER CODE passed through unmapped, since
    no verified code-to-type-name table exists. Note Value is a STRING — "false",
    not a JSON boolean — so parse it yourself rather than testing truthiness.

    AN UNKNOWN SETTING NAME IS NOT AN ERROR. Dataverse answers HTTP 200 with
    SettingDetail: null. That is reported as setting_found: false with no
    setting_value, and it is a DIFFERENT answer from a setting that exists and
    holds "", "false" or 0 — those come back as setting_found: true with the value.
    Never read a missing setting_value as "the setting is off".

    If the payload matches neither shape, setting_value is OMITTED rather than
    guessed and normalized is false. raw_response (minus the @odata.* envelope)
    always rides along on every path, so the extraction can be checked.

    Setting names come from the settingdefinitions table (112 rows on a stock org),
    which dataverse_query_table can list. Both URL forms are live-verified to
    return HTTP 200: SettingName alone, and SettingName with AppUniqueName.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"

    # Both parameters are Edm.String, so each alias value is a SINGLE-QUOTED OData
    # string literal and encode_odata_literal applies to both: it doubles embedded
    # single quotes before percent-encoding, so a quote in the value cannot
    # terminate the literal early once Dataverse percent-decodes the URL. This is
    # the regime dataverse_validate_fetchxml uses, NOT the JSON-array regime of
    # dataverse_get_total_record_counts, where escaping the literal would corrupt
    # the JSON it sends as a parameter alias. The delimiting quotes and
    # the alias '@' stay bare, matching the key-predicate call sites.
    #
    # AppUniqueName is optional, and when it is not supplied BOTH its slot in the
    # parameter list and its alias are omitted — RetrieveSetting(SettingName=@s) —
    # rather than being sent empty, which would be a different request. The two
    # forms are therefore built separately instead of by templating one.
    setting_literal = encode_odata_literal(params.setting_name)
    if params.app_unique_name is not None:
        app_literal = encode_odata_literal(params.app_unique_name)
        url = (
            f"{api_root}/RetrieveSetting(SettingName=@s,AppUniqueName=@a)"
            f"?@s='{setting_literal}'&@a='{app_literal}'"
        )
    else:
        url = f"{api_root}/RetrieveSetting(SettingName=@s)?@s='{setting_literal}'"

    # The whole body is guarded, not just the HTTP call: "do not raise uncaught
    # exceptions from tools" is unconditional, and the normalization below reads a
    # payload whose inner properties are undocumented.
    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()

        try:
            payload: Any = response.json()
        except ValueError:
            logger.warning("RetrieveSetting returned a non-JSON body")
            payload = response.text or None

        body: Any = payload
        if isinstance(payload, dict):
            body = {k: v for k, v in payload.items() if not k.startswith("@odata.")}

        result: dict[str, Any] = {
            "setting_name": params.setting_name,
            "app_unique_name": params.app_unique_name,
            "scope": "app" if params.app_unique_name is not None else "organization",
        }

        # Absence is checked FIRST: SettingDetail: null is a successful answer with
        # its own meaning, and asking the extractor about it would only ever get
        # None back — indistinguishable from a shape it did not understand.
        if _setting_is_absent(body):
            # HTTP 200 with an explicit null container: the call succeeded and the
            # platform's answer is "there is no such setting". Reported as its own
            # outcome so a typo cannot masquerade as a falsy value.
            logger.info(
                "RetrieveSetting returned %s: null for '%s' — no such setting",
                _SETTING_DETAIL_KEY,
                params.setting_name,
            )
            result["normalized"] = True
            result["setting_found"] = False
            result["message"] = (
                f"Dataverse answered successfully with {_SETTING_DETAIL_KEY}: null: "
                f"no setting named '{params.setting_name}' exists"
                + (
                    f" for app '{params.app_unique_name}'"
                    if params.app_unique_name is not None
                    else ""
                )
                + ". This is NOT 'the setting is off' or 'the setting is empty' — "
                "those return setting_found: true with the value. Check the name "
                "against the settingdefinitions table (uniquename column)."
            )
        elif (found := _extract_setting_value(body)) is None:
            logger.warning(
                "RetrieveSetting response carried no %s container and none of %s at "
                "the top level; returning it raw",
                _SETTING_DETAIL_KEY,
                ", ".join(_SETTING_VALUE_KEYS),
            )
            result["normalized"] = False
            result["message"] = (
                "Dataverse answered successfully, but the payload carried no "
                f"{_SETTING_DETAIL_KEY}.Value and no single unambiguous scalar, so "
                "the computed value could not be identified. setting_value is "
                "OMITTED rather than guessed — do not read its absence as 'the "
                "setting is unset'. The response is returned unchanged (minus the "
                "@odata.* envelope) under raw_response."
            )
        else:
            source, value = found
            result["normalized"] = True
            result["setting_found"] = True
            result["setting_value_source"] = source
            result["setting_value"] = value
            result.update(_extract_setting_detail_metadata(body))
        # raw_response rides along on ALL THREE paths: one setting's value is a
        # small payload, and it is the only way a caller can check the extraction.
        result["raw_response"] = body
        return finalize_response(result)
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_get_setting")
    except Exception as e:
        return tool_error_response(e, "dataverse_get_setting")