"""Security administration tools for the Dataverse MCP server.

Covers security roles, teams, users, and business units.
"""

import json
import logging
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("security")
from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    build_headers,
    extract_error_message,
    finalize_response,
    get_app_ctx,
    paginate_records,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import (
    AddTeamMembersInput,
    AssignSecurityRoleInput,
    AuditUserAccessInput,
    GetSecurityRoleInput,
    GetTeamInput,
    GetUserInput,
    ListBusinessUnitsInput,
    ListSecurityRolesInput,
    ListTeamsInput,
    ListUsersInput,
    RemoveSecurityRoleInput,
    RemoveTeamMembersInput,
    SetUserStateInput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default column selections (keep payloads small)
# ---------------------------------------------------------------------------

_DEFAULT_ROLE_SELECT = [
    "roleid",
    "name",
    "_businessunitid_value",
    "ismanaged",
    "modifiedon",
]

_DEFAULT_TEAM_SELECT = [
    "teamid",
    "name",
    "teamtype",
    "_businessunitid_value",
    "isdefault",
    "modifiedon",
]

_DEFAULT_USER_SELECT = [
    "systemuserid",
    "fullname",
    "domainname",
    "internalemailaddress",
    "isdisabled",
    "_businessunitid_value",
]

_DEFAULT_BU_SELECT = [
    "businessunitid",
    "name",
    "_parentbusinessunitid_value",
    "isdisabled",
    "modifiedon",
]


# ---------------------------------------------------------------------------
# Read-only security role tools
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_security_roles",
    annotations={
        "title": "List Security Roles",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_security_roles(
    params: ListSecurityRolesInput, ctx: Context
) -> str:
    """List security roles in the Dataverse environment.

    Returns roleid, name, businessunitid, managed status, and modifiedon.
    Use filter to narrow results (e.g., "ismanaged eq false").
    Use dataverse_get_security_role for full details on a specific role.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_ROLE_SELECT
    top = params.top
    query_params: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
    }
    if params.filter:
        query_params["$filter"] = params.filter

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/roles"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_security_roles")


@tool(
    name="dataverse_get_security_role",
    annotations={
        "title": "Get Security Role",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_security_role(
    params: GetSecurityRoleInput, ctx: Context
) -> str:
    """Retrieve a single Dataverse security role by its GUID.

    Returns full role details including name, business unit, and managed status.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_ROLE_SELECT
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/roles({params.role_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_security_role")


# ---------------------------------------------------------------------------
# Read-only team tools
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_teams",
    annotations={
        "title": "List Teams",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_teams(params: ListTeamsInput, ctx: Context) -> str:
    """List teams in the Dataverse environment.

    Returns teamid, name, teamtype, businessunitid, and modifiedon.
    Use filter to narrow results (e.g., "teamtype eq 0" for owner teams).
    Use dataverse_get_team for full details on a specific team.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_TEAM_SELECT
    top = params.top
    query_params: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
    }
    if params.filter:
        query_params["$filter"] = params.filter

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/teams"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_teams")


@tool(
    name="dataverse_get_team",
    annotations={
        "title": "Get Team",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_team(params: GetTeamInput, ctx: Context) -> str:
    """Retrieve a single Dataverse team by its GUID.

    Returns full team details including name, type, and business unit.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_TEAM_SELECT
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/teams({params.team_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_team")


# ---------------------------------------------------------------------------
# Read-only user tools
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_users",
    annotations={
        "title": "List Users",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_users(params: ListUsersInput, ctx: Context) -> str:
    """List system users (systemusers) in the Dataverse environment.

    Returns systemuserid, fullname, domainname, email, disabled flag, and
    businessunitid. Use filter to narrow results
    (e.g., "isdisabled eq false", "domainname eq 'user@contoso.com'").
    Use dataverse_get_user for full details on a specific user.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_USER_SELECT
    top = params.top
    query_params: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
    }
    if params.filter:
        query_params["$filter"] = params.filter

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/systemusers"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_users")


@tool(
    name="dataverse_get_user",
    annotations={
        "title": "Get User",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_user(params: GetUserInput, ctx: Context) -> str:
    """Retrieve a single Dataverse system user by their GUID.

    Returns full user details including fullname, domainname, email, and
    disabled status. Use dataverse_whoami to get the current caller's UserId.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_USER_SELECT
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/systemusers({params.user_id})"
        f"?$select={','.join(select)}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_user")


# ---------------------------------------------------------------------------
# Read-only business unit tool
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_list_business_units",
    annotations={
        "title": "List Business Units",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_business_units(
    params: ListBusinessUnitsInput, ctx: Context
) -> str:
    """List business units in the Dataverse environment.

    Returns businessunitid, name, parent business unit, disabled flag, and
    modifiedon. Use filter to narrow results (e.g., "isdisabled eq false").
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_BU_SELECT
    top = params.top
    query_params: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
    }
    if params.filter:
        query_params["$filter"] = params.filter

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/businessunits"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
        return finalize_response({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_business_units")


# ---------------------------------------------------------------------------
# Write: role assignment / removal
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_assign_security_role",
    annotations={
        "title": "Assign Security Role",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_assign_security_role(
    params: AssignSecurityRoleInput, ctx: Context
) -> str:
    """Assign a security role to a user or team via the Web API $ref association.

    Provide role_id and exactly one of user_id or team_id.
    For users: associates via systemuserroles_association on the systemusers entity.
    For teams: associates via teamroles_association on the teams entity.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    role_uri = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/roles({params.role_id})"
    )
    body = {"@odata.id": role_uri}

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)

        if params.user_id:
            ref_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/systemusers({params.user_id})/systemuserroles_association/$ref"
            )
            target_type = "user"
            target_id = params.user_id
        else:
            ref_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/teams({params.team_id})/teamroles_association/$ref"
            )
            target_type = "team"
            target_id = params.team_id

        resp = await request_with_retry(
            app_ctx.http_client, "POST", ref_url, headers=headers, json=body
        )
        resp.raise_for_status()
        logger.info(
            "Assigned role %s to %s %s", params.role_id, target_type, target_id
        )
        return json.dumps({
            "assigned": True,
            "role_id": params.role_id,
            target_type + "_id": target_id,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_assign_security_role")


@write_tool(
    name="dataverse_remove_security_role",
    annotations={
        "title": "Remove Security Role",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_remove_security_role(
    params: RemoveSecurityRoleInput, ctx: Context
) -> str:
    """Remove a security role from a user or team via the Web API $ref disassociation.

    Provide role_id and exactly one of user_id or team_id.
    For users: disassociates via systemuserroles_association on the systemusers entity.
    For teams: disassociates via teamroles_association on the teams entity.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        if params.user_id:
            ref_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/systemusers({params.user_id})/systemuserroles_association"
                f"({params.role_id})/$ref"
            )
            target_type = "user"
            target_id = params.user_id
        else:
            ref_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/teams({params.team_id})/teamroles_association"
                f"({params.role_id})/$ref"
            )
            target_type = "team"
            target_id = params.team_id

        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", ref_url, headers=headers
        )
        resp.raise_for_status()
        logger.info(
            "Removed role %s from %s %s", params.role_id, target_type, target_id
        )
        return json.dumps({
            "removed": True,
            "role_id": params.role_id,
            target_type + "_id": target_id,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_remove_security_role")


# ---------------------------------------------------------------------------
# Write: team membership
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_add_team_members",
    annotations={
        "title": "Add Team Members",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_add_team_members(
    params: AddTeamMembersInput, ctx: Context
) -> str:
    """Add one or more system users to a Dataverse team.

    Issues one $ref POST per user against the teams(<teamId>)/teammembership_association
    navigation property. Returns per-user results. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        results = []
        for user_id in params.user_ids:
            ref_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/teams({params.team_id})/teammembership_association/$ref"
            )
            user_uri = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/systemusers({user_id})"
            )
            body = {"@odata.id": user_uri}
            try:
                resp = await request_with_retry(
                    app_ctx.http_client, "POST", ref_url, headers=headers, json=body
                )
                resp.raise_for_status()
                results.append({"user_id": user_id, "added": True})
                logger.info("Added user %s to team %s", user_id, params.team_id)
            except httpx.HTTPStatusError as exc:
                msg = extract_error_message(exc.response)
                results.append({"user_id": user_id, "added": False, "error": msg})
                logger.warning(
                    "Failed to add user %s to team %s: %s",
                    user_id, params.team_id, msg,
                )

        succeeded = sum(1 for r in results if r.get("added"))
        failed = len(results) - succeeded
        return json.dumps({
            "team_id": params.team_id,
            "results": results,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_add_team_members")


@write_tool(
    name="dataverse_remove_team_members",
    annotations={
        "title": "Remove Team Members",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_remove_team_members(
    params: RemoveTeamMembersInput, ctx: Context
) -> str:
    """Remove one or more system users from a Dataverse team.

    Issues one $ref DELETE per user against the teams(<teamId>)/teammembership_association
    navigation property. Returns per-user results. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        results = []
        for user_id in params.user_ids:
            ref_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/teams({params.team_id})/teammembership_association"
                f"({user_id})/$ref"
            )
            try:
                resp = await request_with_retry(
                    app_ctx.http_client, "DELETE", ref_url, headers=headers
                )
                resp.raise_for_status()
                results.append({"user_id": user_id, "removed": True})
                logger.info(
                    "Removed user %s from team %s", user_id, params.team_id
                )
            except httpx.HTTPStatusError as exc:
                msg = extract_error_message(exc.response)
                results.append({"user_id": user_id, "removed": False, "error": msg})
                logger.warning(
                    "Failed to remove user %s from team %s: %s",
                    user_id, params.team_id, msg,
                )

        succeeded = sum(1 for r in results if r.get("removed"))
        failed = len(results) - succeeded
        return json.dumps({
            "team_id": params.team_id,
            "results": results,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_remove_team_members")


# ---------------------------------------------------------------------------
# Write: user state (enable / disable)
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_set_user_state",
    annotations={
        "title": "Set User State",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_set_user_state(params: SetUserStateInput, ctx: Context) -> str:
    """Enable or disable a Dataverse system user.

    PATCHes the writable boolean `isdisabled` field on the systemuser record:
    isdisabled=true disables the user, isdisabled=false enables them. The
    systemuser entity has no statecode/statuscode, and the unbound SetState
    action is not exposed in current Web API environments — `isdisabled` is the
    supported field per the systemuser Web API entity reference.

    Note: the caller must hold the System Administrator role, and a user cannot
    disable their own account. In online environments user lifecycle is also
    governed by Microsoft Entra ID. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    patch_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/systemusers({params.user_id})"
    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client,
            "PATCH",
            patch_url,
            headers=headers,
            json={"isdisabled": params.disabled},
        )
        resp.raise_for_status()
        logger.info(
            "Set user %s state via PATCH isdisabled=%s",
            params.user_id, params.disabled,
        )
        return json.dumps({
            "updated": True,
            "user_id": params.user_id,
            "disabled": params.disabled,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_set_user_state")


# ---------------------------------------------------------------------------
# Composite: user access audit
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_audit_user_access",
    annotations={
        "title": "Audit User Access",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_audit_user_access(
    params: AuditUserAccessInput, ctx: Context
) -> str:
    """Return a composite access report for a Dataverse system user.

    Gathers in one call: user identity, direct security roles, team memberships
    (with each team's roles), and optionally effective privileges and record-level
    access rights. Resolves all type codes to human-readable names.

    Provide either user_id (GUID) or user_domain_name (e.g. 'user@contoso.com').
    Optionally provide target_entity_set_name + target_record_id to include a
    record-level access check.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    api_base = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"

    try:
        headers = await build_headers(app_ctx, base_url)

        # Resolve user_id from domain name if needed
        user_id = params.user_id
        if not user_id:
            resp = await request_with_retry(
                app_ctx.http_client,
                "GET",
                f"{api_base}/systemusers"
                f"?$filter=domainname eq '{params.user_domain_name}'"
                f"&$select=systemuserid,fullname,domainname,isdisabled,_businessunitid_value"
                f"&$top=1",
                headers=headers,
            )
            resp.raise_for_status()
            users = resp.json().get("value", [])
            if not users:
                return json.dumps({
                    "error": True,
                    "message": f"No user found with domainname '{params.user_domain_name}'.",
                })
            user_id = users[0]["systemuserid"]
            user_record = users[0]
            user_record.pop("@odata.context", None)
            user_record.pop("@odata.etag", None)
        else:
            resp = await request_with_retry(
                app_ctx.http_client,
                "GET",
                f"{api_base}/systemusers({user_id})"
                f"?$select=systemuserid,fullname,domainname,isdisabled,_businessunitid_value",
                headers=headers,
            )
            resp.raise_for_status()
            user_record = resp.json()
            user_record.pop("@odata.context", None)
            user_record.pop("@odata.etag", None)

        # Direct security roles
        roles_resp = await request_with_retry(
            app_ctx.http_client,
            "GET",
            f"{api_base}/systemusers({user_id})/systemuserroles_association"
            f"?$select=roleid,name",
            headers=headers,
        )
        roles_resp.raise_for_status()
        direct_roles = [
            {"id": r.get("roleid"), "name": r.get("name")}
            for r in roles_resp.json().get("value", [])
        ]

        # Team memberships
        teams_resp = await request_with_retry(
            app_ctx.http_client,
            "GET",
            f"{api_base}/systemusers({user_id})/teammembership_association"
            f"?$select=teamid,name,teamtype",
            headers=headers,
        )
        teams_resp.raise_for_status()
        raw_teams = teams_resp.json().get("value", [])

        # Roles for each team (up to 10 teams to cap HTTP calls)
        teams = []
        for team in raw_teams[:10]:
            team_id = team.get("teamid")
            tr_resp = await request_with_retry(
                app_ctx.http_client,
                "GET",
                f"{api_base}/teams({team_id})/teamroles_association?$select=roleid,name",
                headers=headers,
            )
            tr_resp.raise_for_status()
            team_roles = [
                {"id": r.get("roleid"), "name": r.get("name")}
                for r in tr_resp.json().get("value", [])
            ]
            teams.append({
                "id": team_id,
                "name": team.get("name"),
                "team_type": team.get("teamtype"),
                "roles": team_roles,
            })
        if len(raw_teams) > 10:
            teams.append({"note": f"{len(raw_teams) - 10} additional teams omitted (cap 10)"})

        report: dict = {
            "user": user_record,
            "direct_roles": direct_roles,
            "teams": teams,
        }

        # Effective privileges (optional)
        if params.include_privileges:
            priv_resp = await request_with_retry(
                app_ctx.http_client,
                "GET",
                f"{api_base}/systemusers({user_id})"
                f"/Microsoft.Dynamics.CRM.RetrieveUserPrivileges",
                headers=headers,
            )
            priv_resp.raise_for_status()
            privileges = priv_resp.json().get("RolePrivileges", [])
            report["effective_privilege_count"] = len(privileges)
            report["effective_privileges"] = privileges

        # Record-level access check (optional)
        if params.target_entity_set_name and params.target_record_id:
            target_ref = (
                f"{api_base}/{params.target_entity_set_name}({params.target_record_id})"
            )
            access_resp = await request_with_retry(
                app_ctx.http_client,
                "GET",
                f"{api_base}/systemusers({user_id})"
                f"/Microsoft.Dynamics.CRM.RetrievePrincipalAccess(Target=@tid)",
                params={"@tid": f"{{'@odata.id':'{target_ref}'}}"},
                headers=headers,
            )
            access_resp.raise_for_status()
            access_payload = access_resp.json()
            access_rights_str = access_payload.get("AccessRights", "")
            named_rights = [r.strip() for r in access_rights_str.split(",") if r.strip()]
            report["record_access"] = {
                "entity_set_name": params.target_entity_set_name,
                "record_id": params.target_record_id,
                "access_rights": access_rights_str,
                "named_rights": named_rights,
            }

        return json.dumps(report)

    except Exception as e:
        return tool_error_response(e, "dataverse_audit_user_access")
