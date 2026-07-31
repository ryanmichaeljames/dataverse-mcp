"""Security administration tools for the Dataverse MCP server.

Covers security roles, teams, users, and business units.
"""

import json
import logging
from collections import Counter
from typing import Any
from urllib.parse import urlencode

import httpx
from mcp.server.mcpserver import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("security")
from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    build_headers,
    encode_odata_literal,
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
    GetAuditDetailsInput,
    GetRolePrivilegesInput,
    GetSecurityRoleInput,
    GetTeamInput,
    GetTeamPrivilegesInput,
    GetUserInput,
    ListAuditInput,
    ListBusinessUnitsInput,
    ListSecurityRolesInput,
    ListSharedPrincipalsInput,
    ListTeamsInput,
    ListUsersInput,
    RemoveSecurityRoleInput,
    RemoveTeamMembersInput,
    RetrieveAccessOriginInput,
    RetrieveRecordChangeHistoryInput,
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
# Role privileges (RetrieveRolePrivilegesRole)
# ---------------------------------------------------------------------------

# Property name of RetrieveRolePrivilegesRoleResponse's collection. VERIFIED LIVE:
# the response body carries exactly this one top-level key holding a JSON list,
# with no wrapper around it. It stays the FIRST thing tried rather than the only
# thing — the function's inner shape is undocumented on Microsoft Learn, so
# _extract_privileges keeps its by-shape fallbacks as cheap insurance (see below).
_ROLE_PRIVILEGES_KEY = "RolePrivileges"

# Property names a privilege entry may carry its depth under. Checked in order;
# the first one present with a usable value wins. Live payloads use "Depth"; the
# lowercase form is kept because Dataverse mixes PascalCase (function/complex-type
# properties) with lowercase (entity attributes).
_DEPTH_KEYS = ("Depth", "depth")


def _first_present(entry: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-None value among *keys*, else None."""
    for key in keys:
        value = entry.get(key)
        if value is not None:
            return value
    return None


def _is_object_list(value: Any) -> bool:
    """True when *value* looks like a collection of entries.

    An empty list qualifies: a role with no privileges (or a record shared with
    nobody) is a real answer, not a shape mismatch. A non-empty list must be all
    objects, otherwise it is some other array that happens to sit in the payload.
    """
    if not isinstance(value, list):
        return False
    return all(isinstance(item, dict) for item in value)


def _extract_entry_list(
    payload: Any,
    preferred_keys: tuple[str, ...],
    function_name: str,
) -> tuple[str, list[dict[str, Any]]] | None:
    """Locate an entry collection as ``(source, entries)``, or None.

    Several of the Web API functions these tools call return types whose inner
    properties Microsoft Learn does NOT document, so the list is found by shape as
    well as by name, in three tiers:

    1. Each name in *preferred_keys*, in order, at the top level.
    2. Exactly one object-list among the top-level properties.
    3. Exactly one object-list one level down, inside a named wrapper — the shape
       ``dataverse_retrieve_record_change_history`` actually met
       (``AuditDetailCollection.AuditDetails``) and the one
       ``dataverse_get_total_record_counts`` met
       (``EntityRecordCountCollection.*``).

    A bare top-level array is accepted too. Ambiguity — two or more candidate
    lists at the same tier — returns None rather than picking one, so the caller
    degrades to raw pass-through instead of reporting entries that may not be the
    entries asked for. *function_name* only labels the warnings.
    """
    if _is_object_list(payload):
        return ("<response body>", payload)
    if not isinstance(payload, dict):
        return None

    for key in preferred_keys:
        named = payload.get(key)
        if _is_object_list(named):
            return (key, named)

    top_level = [(k, v) for k, v in payload.items() if _is_object_list(v)]
    if len(top_level) == 1:
        return top_level[0]
    if len(top_level) > 1:
        logger.warning(
            "%s returned %d candidate lists at the top level (%s); refusing to "
            "pick one",
            function_name,
            len(top_level),
            ", ".join(k for k, _ in top_level),
        )
        return None

    nested = [
        (f"{outer}.{inner}", value)
        for outer, container in payload.items()
        if isinstance(container, dict)
        for inner, value in container.items()
        if _is_object_list(value)
    ]
    if len(nested) == 1:
        return nested[0]
    if len(nested) > 1:
        logger.warning(
            "%s returned %d candidate nested lists (%s); refusing to pick one",
            function_name,
            len(nested),
            ", ".join(k for k, _ in nested),
        )
    return None


def _extract_privileges(payload: Any) -> tuple[str, list[dict[str, Any]]] | None:
    """Locate ``RetrieveRolePrivilegesRole``'s privilege collection."""
    return _extract_entry_list(
        payload, (_ROLE_PRIVILEGES_KEY,), "RetrieveRolePrivilegesRole"
    )


def _summarize_depths(entries: list[dict[str, Any]]) -> dict[str, int] | None:
    """Count entries by their raw ``Depth`` value, or None when none carry one.

    The value is stringified and otherwise passed through UNCHANGED. VERIFIED
    LIVE: Dataverse serializes this OData enum as its member NAME, so this reads
    ``{"Global": 4090, "Basic": 42}`` and is already human-readable — no numeric
    PrivilegeDepth code was observed in any sampled payload. Should one ever
    arrive, the code is reported as-is: no numeric-to-label mapping is applied,
    because that mapping is not confirmed for this function and a wrong
    access-level label is worse than a raw one.
    """
    counter: Counter[str] = Counter()
    for entry in entries:
        depth = _first_present(entry, _DEPTH_KEYS)
        if depth is not None:
            counter[str(depth)] += 1
    return dict(counter) if counter else None


@tool(
    name="dataverse_get_role_privileges",
    annotations={
        "title": "Get Role Privileges",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_role_privileges(
    params: GetRolePrivilegesInput, ctx: Context
) -> str:
    """Answer "what can this security role actually DO?" — list a role's privileges.

    Calls the unbound RetrieveRolePrivilegesRole function. This is the companion to
    dataverse_get_security_role, which returns the role RECORD (name, business
    unit, managed flag) and says nothing about what the role permits. Use
    dataverse_list_security_roles to find a role id by name.

    Scope: this is the role's OWN privilege set. For a specific person's effective
    privileges across all their roles and teams use
    dataverse_retrieve_user_privileges, or dataverse_audit_user_access for the full
    access report.

    RESPONSE SHAPE (verified live). Dataverse returns one top-level property,
    RolePrivileges, holding the whole list with no wrapper; privileges_source
    reports where the collection was found. Every entry carries all six of:
      - PrivilegeName   — the familiar 'prvReadAccount' form. Already present on
                          every entry, so NO extra lookup against the privileges
                          table is made or needed.
      - PrivilegeId     — GUID of the privilege.
      - Depth           — the access level (see below).
      - BusinessUnitId  — GUID of the business unit the depth is scoped to.
      - RecordFilterId, RecordFilterUniqueName — record-filter binding; empty on
                          ordinary privileges.
    Entries are passed through exactly as Dataverse sent them: nothing is added,
    renamed or dropped.

    Depth arrives HUMAN-READABLE and is never relabelled. OData serializes the
    PrivilegeDepth enum as its member NAME, and only member names were observed
    live ("Basic", "Local", "Deep", "Global" — increasing scope, Global being
    org-wide; "Basic" is the user's own records). Should a numeric PrivilegeDepth
    code ever arrive instead, it is reported raw: that mapping is not confirmed for
    this function, and a wrong access-level label is more dangerous than an
    unlabelled one. depth_summary counts every entry by its Depth value.

    THE LIST IS BIG AND IS TRIMMED BY DEFAULT. The function has no server-side
    paging — it returns every privilege in one response. Measured live: a System
    Administrator role carries 4,132 privileges in a ~1 MB raw response. That is
    why top defaults to 50 (~14 KB) and why the raw payload is never echoed back on
    the normalized path. The magnitude is never hidden: total_count is always the
    full number Dataverse returned regardless of trimming, has_more says whether
    anything was trimmed, and depth_summary is computed over ALL entries rather
    than just the returned page. Raise top (max 1000) to see more.

    A well-formed but nonexistent role id returns an ERROR, not an empty list:
    Dataverse answers HTTP 404 [0x80040217] "Entity 'role' With Id = ... Does Not
    Exist", surfaced through the standard {"error": true, "message": ...} envelope.
    An empty privileges list therefore means a real role that grants nothing.

    The function's inner properties are undocumented on Microsoft Learn, so the
    collection is still located by shape as well as by name (RolePrivileges first,
    then a lone object-list at the top level, then one level down inside a named
    wrapper) as insurance against a future platform change. If it cannot be
    identified unambiguously, nothing is guessed: normalized is false, no counts
    are reported, and the payload comes back unchanged under raw_response (minus
    the @odata.* envelope) for you to read yourself.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    # Parameter alias, mirroring dataverse_is_component_customizable in
    # tools/metadata.py. RoleId is Edm.Guid: an OData Guid literal is written BARE
    # — no surrounding quotes and no guid'...' prefix — so there is no string
    # literal to break out of and encode_odata_literal deliberately does NOT apply
    # here. The entire defence is the input model: role_id must match
    # _GUID_PATTERN, so it cannot carry a character that is significant in a URL.
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveRolePrivilegesRole(RoleId=@rid)?@rid={params.role_id}"
    )

    # The whole body is guarded, not just the HTTP call: CLAUDE.md's "do not raise
    # uncaught exceptions from tools" is unconditional, and the shape-location and
    # summary steps below read an undocumented payload.
    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()

        # Parse defensively: a non-JSON or empty body is surfaced as-is rather than
        # raising.
        try:
            payload: Any = response.json()
        except ValueError:
            logger.warning("RetrieveRolePrivilegesRole returned a non-JSON body")
            payload = response.text or None

        body: Any = payload
        if isinstance(payload, dict):
            body = {k: v for k, v in payload.items() if not k.startswith("@odata.")}

        found = _extract_privileges(body)
        if found is None:
            logger.warning(
                "RetrieveRolePrivilegesRole response carried no unambiguous "
                "privilege collection; returning it raw"
            )
            return finalize_response({
                "role_id": params.role_id,
                "normalized": False,
                "message": (
                    "Dataverse answered successfully, but no unambiguous privilege "
                    "collection could be located in the payload, so no count is "
                    "reported and none is guessed. The response is returned "
                    "unchanged (minus the @odata.* envelope) under raw_response — "
                    "read the privileges from there."
                ),
                "raw_response": body,
            })

        source, all_entries = found
        total_count = len(all_entries)
        page = all_entries[: params.top]

        result: dict[str, Any] = {
            "role_id": params.role_id,
            "normalized": True,
            "privileges_source": source,
            "count": len(page),
            "total_count": total_count,
            "has_more": total_count > len(page),
            "privileges": page,
        }

        depth_summary = _summarize_depths(all_entries)
        if depth_summary is not None:
            result["depth_summary"] = depth_summary

        if result["has_more"]:
            result["message"] = (
                f"This role carries {total_count} privileges; the first "
                f"{len(page)} are returned. RetrieveRolePrivilegesRole has no "
                "server-side paging, so the list is trimmed here — raise top "
                "(max 1000) to see more, and read depth_summary for the "
                f"access-level breakdown across ALL {total_count} entries."
            )

        return finalize_response(result)
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_get_role_privileges")
    except Exception as e:
        return tool_error_response(e, "dataverse_get_role_privileges")


# ---------------------------------------------------------------------------
# Team privileges (RetrieveTeamPrivileges)
# ---------------------------------------------------------------------------

# Property names RetrieveTeamPrivilegesResponse may carry its collection under.
# Microsoft Learn documents the return TYPE but none of its inner properties. Two
# names are tried before the by-shape tiers:
#   * "TeamPrivileges" — the name the response type implies. NEVER OBSERVED live;
#     kept first because trying it costs nothing and it is the name a future
#     platform change would most plausibly move to.
#   * "RolePrivileges" — VERIFIED LIVE: this is the key the response actually
#     carries, matching the sibling RetrieveUserPrivileges (see
#     dataverse_audit_user_access below, which reads exactly that key). A team's
#     privileges come from its roles, so the name fits.
# _extract_entry_list still locates the list by shape when neither name is present
# (the fallback never fired on any sampled call), and returns None rather than
# guessing.
_TEAM_PRIVILEGES_KEYS = ("TeamPrivileges", "RolePrivileges")


@tool(
    name="dataverse_get_team_privileges",
    annotations={
        "title": "Get Team Privileges",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_team_privileges(
    params: GetTeamPrivilegesInput, ctx: Context
) -> str:
    """Answer "what can this TEAM actually DO?" — list a team's privileges.

    Calls the entity-bound RetrieveTeamPrivileges function on the team record. It
    takes no parameters of its own: the team id is the key predicate.

    This completes the three-way security picture. dataverse_get_role_privileges
    answers it for a ROLE, dataverse_retrieve_user_privileges for a USER, and this
    for a TEAM — the missing third. It is the companion to dataverse_get_team,
    which returns the team RECORD (name, type, business unit) and says nothing
    about what the team permits. Use dataverse_list_teams to find a team id by
    name, and dataverse_audit_user_access for one person's full access report
    across their direct roles and team memberships.

    RESPONSE SHAPE. Microsoft Learn documents the call and the return type
    RetrieveTeamPrivilegesResponse but NOT its inner properties. VERIFIED LIVE: the
    collection arrives under RolePrivileges — NOT TeamPrivileges, despite the
    response type name — exactly as the sibling RetrieveUserPrivileges does. The
    collection is still located by name first (TeamPrivileges, which has never been
    observed, then RolePrivileges, which is what really comes back) and then by
    shape: a lone object-list at the top level, then one level down inside a named
    wrapper. privileges_source reports where it was found, so check it. If no
    collection can be identified unambiguously, nothing is guessed: normalized is
    false, no counts are reported, and the payload comes back unchanged under
    raw_response (minus the @odata.* envelope) for you to read yourself.

    AN EMPTY LIST IS A REAL ANSWER, NOT A FAILURE. count: 0 with normalized: true
    means the team has NO DIRECTLY-ASSIGNED SECURITY ROLES — a common and entirely
    normal state; on the org this was verified against, EVERY team returned an
    empty RolePrivileges. Do not read it as an error, and do not read it as "this
    team's members have no access": members still hold their own roles, and
    dataverse_audit_user_access is the tool for a person's effective access.

    Entries are expected to mirror RetrieveRolePrivilegesRole's, which was verified
    live: PrivilegeName ('prvReadAccount'), PrivilegeId, Depth, BusinessUnitId,
    RecordFilterId, RecordFilterUniqueName. That expectation is INHERITED FROM THE
    ROLE FUNCTION AND NOT YET VERIFIED FOR TEAMS — every team sampled returned an
    empty list, so no team privilege ENTRY has ever been observed. Entries are
    passed through EXACTLY as Dataverse sent them — nothing is added, renamed or
    dropped — so trust the returned keys over this list.

    Depth is never relabelled. OData serializes the PrivilegeDepth enum as its
    member NAME, and only member names were observed live ON THE ROLE FUNCTION
    ("Basic", "Local", "Deep", "Global" — increasing scope, Global being org-wide);
    whether this function returns the member name or a numeric PrivilegeDepth code
    is likewise unverified, for the same reason. Should a numeric code arrive it is
    reported raw: that mapping is not confirmed for this function, and a wrong
    access-level label is more dangerous than an unlabelled one. depth_summary
    counts every entry by its Depth value.

    THE LIST CAN BE BIG AND IS TRIMMED BY DEFAULT. The function has no server-side
    paging — it returns every privilege in one response — and a team carrying a
    broad role inherits thousands of privileges (the role function was measured
    live at 4,132 privileges in a ~1 MB response). top therefore defaults to 50.
    The magnitude is never hidden: total_count is always the full number Dataverse
    returned, has_more says whether anything was trimmed, and depth_summary is
    computed over ALL entries rather than the returned page. Raise top (max 1000)
    to see more.

    A well-formed but nonexistent team id returns an ERROR, not an empty list —
    VERIFIED LIVE: Dataverse answers HTTP 404 [0x80040217] "Does Not Exist", as the
    role function does, and it is surfaced through the standard
    {"error": true, "message": ...} envelope. The two cases are therefore
    distinguishable: an empty privileges list is always a REAL team with no
    directly-assigned roles, never a bad team id.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    # RetrieveTeamPrivileges is ENTITY-BOUND and takes no function parameters, so
    # there is no parameter alias here at all: team_id lands in the OData KEY
    # PREDICATE in the URL PATH — teams(<guid>). A Guid key predicate is written
    # BARE (no surrounding quotes, no guid'...' prefix), so there is no string
    # literal to break out of and encode_odata_literal deliberately does NOT apply.
    # This repo has a confirmed live key-predicate injection through exactly this
    # position, so the whole defence is _GUID_PATTERN on GetTeamPrivilegesInput: a
    # value matching it cannot contain ' ) / ? & # or CRLF.
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/teams({params.team_id})/Microsoft.Dynamics.CRM.RetrieveTeamPrivileges()"
    )

    # The whole body is guarded, not just the HTTP call: CLAUDE.md's "do not raise
    # uncaught exceptions from tools" is unconditional, and the shape-location and
    # summary steps below read an undocumented payload.
    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()

        # Parse defensively: a non-JSON or empty body is surfaced as-is rather than
        # raising.
        try:
            payload: Any = response.json()
        except ValueError:
            logger.warning("RetrieveTeamPrivileges returned a non-JSON body")
            payload = response.text or None

        body: Any = payload
        if isinstance(payload, dict):
            body = {k: v for k, v in payload.items() if not k.startswith("@odata.")}

        found = _extract_entry_list(
            body, _TEAM_PRIVILEGES_KEYS, "RetrieveTeamPrivileges"
        )
        if found is None:
            logger.warning(
                "RetrieveTeamPrivileges response carried no unambiguous privilege "
                "collection; returning it raw"
            )
            return finalize_response({
                "team_id": params.team_id,
                "normalized": False,
                "message": (
                    "Dataverse answered successfully, but no unambiguous privilege "
                    "collection could be located in the payload, so no count is "
                    "reported and none is guessed. This function's inner response "
                    "properties are undocumented on Microsoft Learn; live runs saw "
                    "the collection under 'RolePrivileges', so a payload that does "
                    "not carry it may signal a platform change. The response is "
                    "returned unchanged (minus the @odata.* envelope) under "
                    "raw_response — read the privileges from there."
                ),
                "raw_response": body,
            })

        source, all_entries = found
        total_count = len(all_entries)
        page = all_entries[: params.top]

        result: dict[str, Any] = {
            "team_id": params.team_id,
            "normalized": True,
            "privileges_source": source,
            "count": len(page),
            "total_count": total_count,
            "has_more": total_count > len(page),
            "privileges": page,
        }

        depth_summary = _summarize_depths(all_entries)
        if depth_summary is not None:
            result["depth_summary"] = depth_summary

        if result["has_more"]:
            result["message"] = (
                f"This team carries {total_count} privileges; the first "
                f"{len(page)} are returned. RetrieveTeamPrivileges has no "
                "server-side paging, so the list is trimmed here — raise top "
                "(max 1000) to see more, and read depth_summary for the "
                f"access-level breakdown across ALL {total_count} entries."
            )

        return finalize_response(result)
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_get_team_privileges")
    except Exception as e:
        return tool_error_response(e, "dataverse_get_team_privileges")


# ---------------------------------------------------------------------------
# Access origin (RetrieveAccessOrigin)
# ---------------------------------------------------------------------------

# Property name RetrieveAccessOriginResponse carries its answer under. VERIFIED
# LIVE: once the @odata.* envelope is stripped the body is exactly
# {"Response": "<string>"} — ONE scalar string property, never a collection, in a
# raw body of roughly 286 bytes. Every sampled call took this path.
_ACCESS_ORIGIN_KEY = "Response"

# Scalar JSON types. ``bool`` is listed explicitly for readers even though
# isinstance(True, int) is already True — harmless here, since every scalar is
# accepted and none is compared against another type.
_SCALAR_TYPES = (str, int, float, bool)


def _is_scalar(value: Any) -> bool:
    """True for a JSON scalar. ``None`` is deliberately NOT one.

    An explicit null is "nothing to report", not an access origin, and a missing
    property reads as None too — so treating null as an answer would let an
    absent ``Response`` normalize into ``access_origin: null``.
    """
    return isinstance(value, _SCALAR_TYPES)


def _extract_access_origin(payload: Any) -> tuple[str, Any] | None:
    """Return the access-origin answer as ``(source, value)``, or None.

    Two tiers, mirroring ``_extract_version`` (tools\\environments.py) and
    ``_extract_verdict`` (tools\\metadata.py):

    1. ``Response`` is the confirmed property name — verified live against a real
       org, where the whole body was ``{"Response": "<string>"}`` on every call —
       so it is read directly.
    2. A defensive fallback for a differently-named property: a body carrying
       exactly one scalar value is unambiguous, so it is accepted with its source
       recorded.

    Membership is tested with ``_is_scalar``, never truthiness: an origin
    reported as an empty string, ``0`` or ``False`` is still what the platform
    said and must not fall through to the raw path. Anything else — no scalar at
    all, two or more scalars without a ``Response`` among them, or a non-object
    body — returns None so the caller degrades to raw pass-through rather than
    being told an origin that may not be the origin.

    There is no ``count``: this answer is never list-shaped.
    """
    if not isinstance(payload, dict):
        return None
    answer = payload.get(_ACCESS_ORIGIN_KEY)
    if _is_scalar(answer):
        return (_ACCESS_ORIGIN_KEY, answer)
    scalars = [(k, v) for k, v in payload.items() if _is_scalar(v)]
    if len(scalars) != 1:
        return None
    return scalars[0]


@tool(
    name="dataverse_retrieve_access_origin",
    annotations={
        "title": "Retrieve Access Origin",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_retrieve_access_origin(
    params: RetrieveAccessOriginInput, ctx: Context
) -> str:
    """Answer "WHY does this principal have access to this record?".

    Calls the unbound RetrieveAccessOrigin function, which explains where a
    principal's rights over one specific row come from — object ownership, or the
    Principal Object Access (POA) table that backs explicit shares and team or
    hierarchy grants.

    This is the companion to dataverse_retrieve_principal_access, which returns
    only the access MASK (which rights: Read, Write, Delete, …) and cannot say
    where those rights came from. When you are debugging "why can this user see
    this record?" or "why can't they?", the mask is the symptom and this is the
    cause. Use dataverse_audit_user_access for the wider picture (roles, teams,
    effective privileges) and dataverse_get_role_privileges for what one role
    permits in general rather than on one row.

    Inputs:
      - object_id    — the record's own GUID.
      - logical_name — the SINGULAR lowercase logical name of that record's table
                       ('account', not 'accounts'). This is deliberately not the
                       entity set name the record-access tools take.
      - principal_id — a systemuser id or a team id. No other principal type is
                       accepted; use dataverse_list_users / dataverse_list_teams.

    RESPONSE SHAPE (verified live). Dataverse answers with ONE scalar string
    property, Response — never a collection, in a raw body of roughly 286 bytes.
    It is surfaced as access_origin, with access_origin_source naming the property
    it was read from, and the payload (minus the @odata.* envelope) rides along
    under raw_response so you can check that for yourself. There is no count:
    the answer is never list-shaped. Should a future platform change move the
    answer somewhere unrecognizable, normalized is false, nothing is fabricated,
    and raw_response is the whole answer.

    HTTP 200 DOES NOT MEAN "HAS ACCESS" — READ THE STRING. Three materially
    different outcomes all come back as a successful call with normalized true,
    and they are distinguishable ONLY by the English prose inside the string. The
    text is passed through verbatim and deliberately NOT classified into a
    boolean: pattern-matching platform prose is fragile and locale-dependent, and
    a wrong security verdict is worse than none. Observed live in ONE org —
    these wordings are observations, not a documented platform contract, so
    treat the list as incomplete and never match on it:
      1. Access exists, with the reason. Two forms seen, both meaning "owner" —
         "PrincipalId is object owner (<guid>)" on a user- or team-owned row,
         and "PrincipalId is member of organization (<guid>) who is object owner
         (<guid>)" on an ORGANIZATION-owned row (see the org-owned note below
         for why that answer is the same for every principal).
      2. NO access at all —
         "Access origin could not be found. Access does not come from POA table
          or object ownership."
      3. The record DOES NOT EXIST — still HTTP 200, carrying the platform's
         "Does Not Exist" exception text inside the Response string. A bad
         object_id is NOT a 404 from this function, so an unread string looks
         exactly like a successful answer.
    Do not report that the principal has access unless the string says so.

    Other live-confirmed behaviour:
      - An unknown but grammar-valid logical_name is a clean HTTP 400
        [0x80041102] "... was not found in the MetadataCache", surfaced through
        the standard {"error": true, "message": ...} envelope. Confirm the name
        with dataverse_list_tables.
      - On an ORGANIZATION-owned table (solution, role, …) the answer is the same
        for every principal, because ownership resolves at organization level.
        That is correct platform behaviour, not a defect — discrimination between
        principals shows up on user- and team-owned rows.
      - A nonexistent principal_id is not validated against an org-owned row: it
        returned the same generic ownership text as a real one. Confirm the
        principal exists with dataverse_get_user / dataverse_get_team first.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    # The three parameters are NOT escaped alike, because their OData types differ.
    #
    #   ObjectId, PrincipalId (Edm.Guid)  — a Guid literal is written BARE: no
    #       surrounding quotes and no guid'...' prefix. There is therefore no
    #       string literal to break out of and encode_odata_literal deliberately
    #       does NOT apply; the whole defence is _GUID_PATTERN on the input model,
    #       which leaves no character that is significant in a URL. Live-confirmed
    #       by IsComponentCustomizable and RetrieveRolePrivilegesRole.
    #
    #   LogicalName (Edm.String) — a single-quoted OData string literal, so it DOES
    #       need encode_odata_literal: single quotes doubled, then percent-encoded.
    #       Percent-encoding alone is not enough, because Dataverse percent-decodes
    #       the URL before parsing the OData expression, so a lone %27 would decode
    #       back to a quote and terminate the literal early. Live-confirmed by
    #       ValidateFetchXmlExpression. This is defence in depth, not the only
    #       defence: RetrieveAccessOriginInput already restricts logical_name to the
    #       Dataverse identifier grammar, which cannot express a quote at all.
    logical_name_enc = encode_odata_literal(params.logical_name)
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveAccessOrigin(ObjectId=@oid,LogicalName=@ln,PrincipalId=@pid)"
        f"?@oid={params.object_id}&@ln='{logical_name_enc}'&@pid={params.principal_id}"
    )

    # The whole body is guarded, not just the HTTP call: CLAUDE.md's "do not raise
    # uncaught exceptions from tools" is unconditional, and the extraction step
    # below reads a payload whose inner properties Microsoft Learn does not
    # document (they are live-verified here, not contractual).
    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()

        # Parse defensively: a non-JSON or empty body is surfaced as-is rather than
        # raising.
        try:
            payload: Any = response.json()
        except ValueError:
            logger.warning("RetrieveAccessOrigin returned a non-JSON body")
            payload = response.text or None

        body: Any = payload
        if isinstance(payload, dict):
            body = {k: v for k, v in payload.items() if not k.startswith("@odata.")}

        result: dict[str, Any] = {
            "object_id": params.object_id,
            "logical_name": params.logical_name,
            "principal_id": params.principal_id,
        }

        found = _extract_access_origin(body)
        if found is None:
            logger.warning(
                "RetrieveAccessOrigin response carried neither a %s property nor "
                "exactly one scalar value; returning it raw",
                _ACCESS_ORIGIN_KEY,
            )
            result["normalized"] = False
            result["message"] = (
                "Dataverse answered successfully, but the payload carried neither "
                f"a {_ACCESS_ORIGIN_KEY} property nor exactly one scalar value, so "
                "the access origin could not be identified. access_origin is "
                "OMITTED rather than guessed — do not read its absence as 'no "
                "access'. The response is returned unchanged (minus the @odata.* "
                "envelope) under raw_response; read the origin from there."
            )
        else:
            source, value = found
            result["normalized"] = True
            result["access_origin_source"] = source
            result["access_origin"] = value
            result["message"] = (
                "A successful call does NOT mean the principal has access — read "
                "access_origin. The same shape carries 'no access' ("
                '"Access origin could not be found...") and "that record id does '
                "not exist\" as ordinary HTTP 200 prose. The text is passed "
                "through unclassified on purpose."
            )
        # raw_response rides along on both paths: the payload is one record's
        # access explanation, so it is small (~286 bytes), and it is the only way
        # a caller can check the extraction for themselves.
        result["raw_response"] = body
        return finalize_response(result)
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_retrieve_access_origin")
    except Exception as e:
        return tool_error_response(e, "dataverse_retrieve_access_origin")


# ---------------------------------------------------------------------------
# Record shares (RetrieveSharedPrincipalsAndAccess + RetrieveSharedLinks)
# ---------------------------------------------------------------------------

# Property name RetrieveSharedPrincipalsAndAccessResponse carries its collection
# under. Microsoft Learn documents the return type but not its inner properties;
# "PrincipalAccesses" was taken from the organization-service response class and is
# now VERIFIED LIVE — every sampled call normalized under exactly this name, and
# _extract_entry_list's by-shape fallback never fired. The fallback is kept as
# cheap insurance against a platform change.
_SHARED_PRINCIPALS_KEYS = ("PrincipalAccesses",)

# RetrieveSharedLinks returns Collection(team), i.e. an ordinary OData collection,
# so the entries sit under the standard "value" property rather than a named one.
# VERIFIED LIVE, including that the function itself is available on a real org (it
# never landed in partial_errors on any sampled call).
_SHARED_LINKS_KEYS = ("value",)


async def _call_share_function(
    app_ctx: Any,
    headers: dict[str, str],
    function_name: str,
    url: str,
    partial_errors: list[dict[str, str]],
) -> Any | None:
    """GET an unbound function, recording a partial error instead of propagating.

    Mirrors ``_call_org_function`` in tools\\environments.py. Returns the
    envelope-stripped payload, or None when the call failed — in which case an
    entry is appended to *partial_errors* so one unavailable or privilege-gated
    function cannot fail the whole tool.
    """
    try:
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError:
            logger.warning("%s returned a non-JSON body", function_name)
            return response.text or None
        if isinstance(payload, dict):
            return {k: v for k, v in payload.items() if not k.startswith("@odata.")}
        return payload
    except httpx.HTTPStatusError as e:
        message = (
            f"Dataverse returned HTTP {e.response.status_code}: "
            f"{extract_error_message(e.response)}"
        )
    except Exception as e:  # network, auth — never fatal for the other call
        message = f"{type(e).__name__}: {e}"
    logger.warning(
        "%s failed during dataverse_list_shared_principals: %s", function_name, message
    )
    partial_errors.append({"function": function_name, "message": message})
    return None


def _shared_block(
    payload: Any,
    preferred_keys: tuple[str, ...],
    function_name: str,
    entries_key: str,
    top: int,
) -> dict[str, Any]:
    """Normalize one share function's payload into a trimmed, counted block."""
    found = _extract_entry_list(payload, preferred_keys, function_name)
    if found is None:
        logger.warning(
            "%s response carried no unambiguous collection; returning it raw",
            function_name,
        )
        return {
            "normalized": False,
            "message": (
                f"{function_name} answered successfully, but no unambiguous "
                "collection could be located in the payload, so no count is "
                "reported and none is guessed. This function's inner response "
                "properties are undocumented. The response is returned unchanged "
                "(minus the @odata.* envelope) under raw_response — read it there."
            ),
            "raw_response": payload,
        }

    source, all_entries = found
    total_count = len(all_entries)
    page = all_entries[:top]
    block: dict[str, Any] = {
        "normalized": True,
        "source": source,
        "count": len(page),
        "total_count": total_count,
        "has_more": total_count > len(page),
        entries_key: page,
    }
    if block["has_more"]:
        block["message"] = (
            f"{function_name} returned {total_count} entries; the first "
            f"{len(page)} are returned. It has no server-side paging, so the list "
            "is trimmed here — raise top (max 1000) to see more."
        )
    return block


@tool(
    name="dataverse_list_shared_principals",
    annotations={
        "title": "List Shared Principals",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_shared_principals(
    params: ListSharedPrincipalsInput, ctx: Context
) -> str:
    """Answer "WHO has this record because it was SHARED with them?".

    Merges two unbound Web API functions that answer one question:
      - RetrieveSharedPrincipalsAndAccess — the principals the record was shared
        with, and the access rights each was given.
      - RetrieveSharedLinks — the existing shared links over the record that the
        caller is allowed to see.

    This is the list neither neighbouring tool can produce.
    dataverse_retrieve_principal_access answers "which rights does ONE named
    principal have" (the mask), dataverse_retrieve_access_origin answers "WHY does
    ONE named principal have them" — both need you to already know who to ask
    about. This one enumerates them.

    MIND THE INPUT ASYMMETRY. This tool takes the PLURAL entity_set_name
    ('accounts'), because the target is expressed as an OData EntityReference and
    an @odata.id names a collection. dataverse_retrieve_access_origin takes the
    SINGULAR logical_name ('account'). Confusing the two is the easy caller error
    here — use dataverse_get_entity_sets to confirm the plural, which is irregular
    often enough ('webresourceset') that guessing costs a 404.

    A WRONG ENTITY SET NAME LOOKS EXACTLY LIKE A MISSING RECORD. VERIFIED LIVE:
    a nonexistent record id and a VALID id paired with the WRONG entity set both
    return HTTP 404 [0x80040217] "Entity '<Type>' With Id = <guid> Does Not Exist"
    from BOTH functions — the same status, the same error code, indistinguishable
    text. Both calls therefore fail and you get the standard
    {"error": true, "message": ...} envelope. So when this tool errors with "Does
    Not Exist", CHECK THE ENTITY SET NAME FIRST (plural — 'accounts', not
    'account'; see the asymmetry note above) before concluding the record is gone.
    That singular-for-plural slip is the likeliest cause and it misdiagnoses as a
    missing record.

    THE TWO CALLS FAIL INDEPENDENTLY. Each is made on its own: if one is
    unavailable or privilege-gated, its failure is reported in partial_errors and
    the other's data is still returned. Only a failure of BOTH yields the standard
    {"error": true, "message": ...} envelope. RetrieveSharedLinks is in principle
    the more likely of the two to be missing (it was available on the org tested,
    never landing in partial_errors), so a partial_errors entry naming it is an
    expected outcome rather than an error — check partial_errors before concluding
    a record is unshared.

    RESPONSE SHAPES (verified live). Microsoft Learn documents
    RetrieveSharedPrincipalsAndAccessResponse but not its inner properties; live
    runs confirm the collection arrives under PrincipalAccesses, and that is the
    name tried first before the by-shape fallback. RetrieveSharedLinks returns
    Collection(team), an ordinary OData collection, and its entries duly arrive
    under the standard 'value' property. Each block reports the source it was found
    under — check it. If a payload cannot be identified unambiguously that block
    carries normalized: false, no counts, and the raw payload (minus the @odata.*
    envelope) under raw_response; nothing is fabricated.

    Both lists are trimmed to top (default 50, max 1000) because neither function
    pages server-side. count, total_count and has_more are reported per block and
    total_count is always the full number Dataverse returned.

    An empty result is NOT proof the record is private: these functions report
    explicit shares (the POA table) that the CALLER can see, not access granted by
    ownership, security roles, team membership or the business-unit hierarchy. Use
    dataverse_retrieve_access_origin for a specific principal, and read
    partial_errors before drawing any conclusion.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"

    # Target is typed crmbaseentity — an EntityReference, NOT a scalar — and the
    # Web API passes one as a parameter alias whose value is a JSON OBJECT holding
    # an @odata.id:  ?@t={"@odata.id":"accounts(<guid>)"}
    #
    # That is a FOURTH escaping regime, distinct from the three already live in
    # this codebase, so do not "make it consistent" with any of them:
    #   * Edm.Guid              -> bare literal, nothing encoded
    #                              (dataverse_get_role_privileges above)
    #   * Edm.String            -> single-quoted literal via encode_odata_literal
    #                              (dataverse_validate_fetchxml, tools\tables.py)
    #   * Collection(Edm.String)-> JSON ARRAY alias, percent-encode ONLY
    #                              (dataverse_get_total_record_counts, tools\tables.py)
    #   * URL path segment      -> closed Literal allowlist
    #                              (dataverse_retrieve_unpublished, tools\customizations.py)
    # An @odata.id object is escaped like the JSON array and for the same reason:
    # the alias value is a JSON document, so it is json.dumps'd and then
    # percent-encoded, and encode_odata_literal must NOT be applied — doubling
    # single quotes would corrupt the JSON. safe="@" keeps the alias name from
    # becoming %40t, exactly as in dataverse_get_total_record_counts.
    #
    # Both halves of the @odata.id are caller-supplied and land in a URL, and this
    # repo has a confirmed live OData key-predicate injection through exactly this
    # class of input. The defence is at the model: ListSharedPrincipalsInput pins
    # entity_set_name to _DATAVERSE_NAME_PATTERN and record_id to _GUID_PATTERN, so
    # neither can express the '"', '}', ')' or '/' a breakout needs.
    #
    # The relative form ("accounts(<guid>)") is what Microsoft Learn's samples use.
    # dataverse_retrieve_principal_access builds the same reference with an
    # ABSOLUTE url. VERIFIED LIVE: both forms are accepted — the same record queried
    # each way returned byte-identical bodies from both functions — so the choice is
    # free, and the relative form is kept here because it cannot leak the org host
    # into a query string.
    target = f"{params.entity_set_name}({params.record_id})"
    alias_value = json.dumps({"@odata.id": target}, separators=(",", ":"))
    query = urlencode({"@t": alias_value}, safe="@")
    principals_url = f"{api_root}/RetrieveSharedPrincipalsAndAccess(Target=@t)?{query}"
    links_url = f"{api_root}/RetrieveSharedLinks(Target=@t)?{query}"

    partial_errors: list[dict[str, str]] = []

    # The whole body is guarded, not just the HTTP calls: CLAUDE.md's "do not raise
    # uncaught exceptions from tools" is unconditional, and the normalization steps
    # below read payloads whose inner properties are undocumented.
    try:
        headers = await build_headers(app_ctx, base_url)

        principals_payload = await _call_share_function(
            app_ctx,
            headers,
            "RetrieveSharedPrincipalsAndAccess",
            principals_url,
            partial_errors,
        )
        links_payload = await _call_share_function(
            app_ctx, headers, "RetrieveSharedLinks", links_url, partial_errors
        )

        if len(partial_errors) == 2:
            details = "; ".join(
                f"{entry['function']}: {entry['message']}" for entry in partial_errors
            )
            return json.dumps({
                "error": True,
                "message": (
                    f"Could not list the principals {target} is shared with: both "
                    f"functions failed. {details}"
                ),
            })

        result: dict[str, Any] = {
            "entity_set_name": params.entity_set_name,
            "record_id": params.record_id,
            "target": target,
        }
        if principals_payload is not None:
            result["shared_principals"] = _shared_block(
                principals_payload,
                _SHARED_PRINCIPALS_KEYS,
                "RetrieveSharedPrincipalsAndAccess",
                "principals",
                params.top,
            )
        if links_payload is not None:
            result["shared_links"] = _shared_block(
                links_payload,
                _SHARED_LINKS_KEYS,
                "RetrieveSharedLinks",
                "links",
                params.top,
            )
        result["partial_errors"] = partial_errors

        # Say what an all-empty answer does and does not mean, but only when every
        # block really was normalized and really was empty — never as a verdict on
        # a payload that could not be read.
        blocks = [
            b for b in (result.get("shared_principals"), result.get("shared_links"))
            if isinstance(b, dict)
        ]
        if blocks and all(
            b.get("normalized") and b.get("total_count") == 0 for b in blocks
        ):
            result["message"] = (
                f"No explicit share was reported for {target}. That is not proof "
                "the record is private: these functions report shares recorded in "
                "the POA table and visible to THIS caller, not access granted by "
                "ownership, security roles, team membership or the business-unit "
                "hierarchy. Check partial_errors, and use "
                "dataverse_retrieve_access_origin for a specific principal."
            )

        return finalize_response(result)
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_list_shared_principals")
    except Exception as e:
        return tool_error_response(e, "dataverse_list_shared_principals")


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


@delete_tool(
    name="dataverse_remove_security_role",
    annotations={
        "title": "Remove Security Role",
        "readOnlyHint": False,
        "destructiveHint": True,
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
    Requires DATAVERSE_ALLOW_DELETE=true.
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


@delete_tool(
    name="dataverse_remove_team_members",
    annotations={
        "title": "Remove Team Members",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_remove_team_members(
    params: RemoveTeamMembersInput, ctx: Context
) -> str:
    """Remove one or more system users from a Dataverse team.

    Issues one $ref DELETE per user against the teams(<teamId>)/teammembership_association
    navigation property. Returns per-user results. Requires DATAVERSE_ALLOW_DELETE=true.
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


# ---------------------------------------------------------------------------
# Read-only audit history tools
# ---------------------------------------------------------------------------

_DEFAULT_AUDIT_SELECT = [
    "auditid",
    "createdon",
    "operation",
    "action",
    "objecttypecode",
    "_userid_value",
    "_objectid_value",
    "transactionid",
]


@tool(
    name="dataverse_retrieve_record_change_history",
    annotations={
        "title": "Retrieve Record Change History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_retrieve_record_change_history(
    params: RetrieveRecordChangeHistoryInput, ctx: Context
) -> str:
    """Retrieve the full audit change history for a specific record.

    Calls the unbound RetrieveRecordChangeHistory function which returns an
    AuditDetailCollection. Each AuditDetail is polymorphic — the most common
    subtype is AttributeAuditDetail (OldValue, NewValue per changed field).
    All subtypes include an AuditRecord navigation property with the audit
    metadata (who, when, what operation).

    Note: requires auditing enabled on the org and the target table. If auditing
    is disabled, Dataverse returns an HTTP error — check the error message for
    audit configuration guidance.

    URL form: GET /api/data/v9.2/RetrieveRecordChangeHistory(Target=@p1)
              ?@p1={'@odata.id':'<entity_set_name>(<record_id>)'}
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    api_base = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
    # Build the alias value: relative @odata.id reference
    target_ref = f"{params.entity_set_name}({params.record_id})"
    alias_value = f"{{'@odata.id':'{target_ref}'}}"
    url = (
        f"{api_base}/RetrieveRecordChangeHistory(Target=@p1)"
        f"?@p1={alias_value}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        body = resp.json()

        collection = body.get("AuditDetailCollection", body)
        details = collection.get("AuditDetails", [])
        more_records = collection.get("MoreRecords", False)
        paging_cookie = collection.get("PagingCookie")
        total = collection.get("TotalRecordCount")

        # Cap to requested top
        details = details[: params.top]

        response: dict = {
            "entity_set_name": params.entity_set_name,
            "record_id": params.record_id,
            "audit_details": details,
            "count": len(details),
            "has_more": more_records,
        }
        if total is not None:
            response["total_record_count"] = total
        if paging_cookie:
            response["paging_cookie"] = paging_cookie

        return finalize_response(response)
    except Exception as e:
        return tool_error_response(e, "dataverse_retrieve_record_change_history")


@tool(
    name="dataverse_get_audit_details",
    annotations={
        "title": "Get Audit Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_audit_details(
    params: GetAuditDetailsInput, ctx: Context
) -> str:
    """Retrieve full details from a single audit record.

    Calls the bound RetrieveAuditDetails function on the audit entity, returning
    a polymorphic AuditDetail. The most common subtype is AttributeAuditDetail
    which includes OldValue and NewValue (each containing the changed attribute
    values keyed by logical name) plus InvalidNewValueAttributes.

    Common AuditDetail subtypes (identified by @odata.type):
      - AttributeAuditDetail — field changes with OldValue/NewValue
      - RelationshipAuditDetail — relationship association/disassociation
      - ShareAuditDetail — record sharing
      - RolePrivilegeAuditDetail — role privilege changes
      - UserAccessAuditDetail — user login/access events

    Note: requires auditing enabled on the org. If auditing is disabled,
    Dataverse returns an HTTP error — check the error message for guidance.

    URL form: GET /api/data/v9.2/audits(<audit_id>)/Microsoft.Dynamics.CRM.RetrieveAuditDetails
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    api_base = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
    url = f"{api_base}/audits({params.audit_id})/Microsoft.Dynamics.CRM.RetrieveAuditDetails"

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        body.pop("@odata.context", None)

        audit_detail = body.get("AuditDetail", body)
        return json.dumps({"audit_id": params.audit_id, "audit_detail": audit_detail})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_audit_details")


@tool(
    name="dataverse_list_audit",
    annotations={
        "title": "List Audit Records",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_audit(params: ListAuditInput, ctx: Context) -> str:
    """Query the audit table with optional OData filters.

    Returns audit records from the 'audits' entity set. Common columns:
    - auditid, createdon — record identity and timestamp
    - operation — 1=Create, 2=Update, 3=Delete, 4=Access, 5=Upsert
    - action — specific event code (e.g., 1=Create, 2=Update, 3=Delete,
      64=User Access via Web, 65=User Access via Web Services)
    - objecttypecode — logical name of the audited entity (e.g., 'account')
    - _userid_value — GUID of the user who made the change
    - _objectid_value — GUID of the audited record
    - transactionid — groups related changes in one operation

    Use dataverse_get_audit_details to fetch full before/after values for a
    specific audit record.

    Note: requires auditing enabled on the org. If auditing is disabled,
    Dataverse may return an empty result set or an HTTP error.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    select = params.select or _DEFAULT_AUDIT_SELECT
    top = params.top
    query_params: dict[str, str] = {
        "$select": ",".join(select),
        "$top": str(top),
    }
    if params.filter:
        query_params["$filter"] = params.filter
    if params.orderby:
        query_params["$orderby"] = ",".join(params.orderby)

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/audits"
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
        return tool_error_response(e, "dataverse_list_audit")
