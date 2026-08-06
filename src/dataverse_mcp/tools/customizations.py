"""Unpublished (draft) customization reads for the Dataverse MCP server.

Customization records live in two layers. A write (PATCH on a systemform's
formxml, a savedquery's layoutxml, a webresource's
content) lands in the UNPUBLISHED draft; a normal ``Retrieve``/GET keeps
returning the PUBLISHED row until ``PublishXml`` runs. That split is why this
module exists: ``dataverse_set_formxml``, ``dataverse_add_form_control``,
``dataverse_update_view`` and ``dataverse_add_view_column`` all write drafts,
while ``dataverse_get_form`` / ``dataverse_get_view`` read the published row —
so an agent that edits and then reads back sees stale data and can silently
clobber its own work.

``RetrieveUnpublished`` is the platform message that reads the other layer. It
is bound to an entity INSTANCE::

    GET {api_root}/{entityset}({record_id})/Microsoft.Dynamics.CRM.RetrieveUnpublished()

The collection-bound sibling ``RetrieveUnpublishedMultiple()`` is already used by
``dataverse_list_apps``; this module deliberately exposes only the single-record
form (see the tool docstring for why).

``RetrieveUnpublished`` is NOT accepted for every table that has a draft layer:
Dataverse refuses the message per entity type. ``sitemaps`` is the live-confirmed
example — ``HTTP 400 [0x80040800] The 'RetrieveUnpublished' method does not
support entities of type 'sitemap'``, with or without ``$select`` — so it is
absent from both the allowlist and ``_DEFAULT_SELECT`` below: a sitemap draft
cannot be read through this message at all.

Category: ``core``. The tool spans views, forms, apps and web
resources, so no single domain category owns it — and ``core`` is the one
category that is always registered, which means a caller scoped to
``DATAVERSE_TOOLS=views`` does not lose the tool that makes its view reads
correct.
"""

import json
import logging
from urllib.parse import urlencode

import httpx
from mcp.server.mcpserver import Context

from dataverse_mcp._app import category_tools

tool, _write_tool, _delete_tool = category_tools("core")
from dataverse_mcp.client import (  # noqa: E402
    _DATAVERSE_API_VERSION,
    build_headers,
    finalize_response,
    get_app_ctx,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import RetrieveUnpublishedInput  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default column projections
# ---------------------------------------------------------------------------
#
# Payload hygiene is the point. A single systemform row carries formxml, a
# savedquery carries fetchxml + layoutxml and a webresource carries base64
# content — any one of them can be hundreds of KB, and this server already caps
# a tool response at 5 MB. So every entity set gets a small default projection
# with the XML/binary columns EXCLUDED, and the caller opts in to the big
# columns through ``select``.
#
# Live-measured savings, same record both ways: webresourceset 980 bytes by
# default vs 26,388 with content (~96% saved); systemforms 629 vs 3,819 with
# formxml; savedqueries 677 vs 1,049 with fetchxml + layoutxml.
#
# Requiring ``select`` outright was the alternative and was rejected: the most
# common use of this tool is "did my edit land?", which needs a working default
# rather than prior knowledge of the column names. Returning everything was
# rejected for the size reason above.
#
# The projections are copied verbatim from the live-verified constants in
# views.py, forms.py, apps.py and web_resources.py, so the column names are
# known-good. The key set is also the allowlist of entity types Dataverse
# actually accepts RetrieveUnpublished for — ``sitemaps`` is excluded because the
# platform rejects the message for that type outright (see the module docstring).
_DEFAULT_SELECT: dict[str, tuple[str, ...]] = {
    # views.py::_SAVEDQUERY_SELECT — fetchxml and layoutxml excluded
    "savedqueries": (
        "savedqueryid", "name", "returnedtypecode", "querytype",
        "isdefault", "isquickfindquery", "statecode", "description",
    ),
    # forms.py::_SYSTEM_FORM_SELECT — formxml excluded
    "systemforms": (
        "formid", "name", "objecttypecode", "type",
        "formactivationstate", "isdefault", "description",
    ),
    # apps.py::_APP_SELECT
    "appmodules": (
        "appmoduleid", "appmoduleidunique", "name", "uniquename",
        "description", "publishedon", "statecode", "clienttype",
    ),
    # web_resources.py::_DEFAULT_WEB_RESOURCE_SELECT — content excluded
    "webresourceset": (
        "webresourceid", "name", "displayname", "webresourcetype",
        "description", "languagecode", "ismanaged", "iscustomizable",
        "createdon", "modifiedon",
    ),
}

# The large columns held back by each default projection, named in the response
# so the caller knows what to ask for rather than having to guess.
_LARGE_COLUMNS: dict[str, tuple[str, ...]] = {
    "savedqueries": ("fetchxml", "layoutxml"),
    "systemforms": ("formxml",),
    "appmodules": (),
    "webresourceset": ("content",),
}


# ---------------------------------------------------------------------------
# Tool: dataverse_retrieve_unpublished
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_retrieve_unpublished",
    annotations={
        "title": "Retrieve Unpublished Customization",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_retrieve_unpublished(
    params: RetrieveUnpublishedInput, ctx: Context
) -> str:
    """Read the UNPUBLISHED (draft) definition of one customization record.

    A normal GET — and therefore dataverse_get_form, dataverse_get_view and
    dataverse_get_web_resource — returns the PUBLISHED row. Writes such as
    dataverse_set_formxml, dataverse_add_form_control, dataverse_update_view and
    dataverse_add_view_column save to the draft, so after any of them the
    published read is stale until dataverse_publish_customizations runs. Call
    this tool to read back what you just wrote; reading the published row and
    then editing it can silently clobber your own unpublished changes.

    Supported entity_set_name values: 'savedqueries' (views), 'systemforms'
    (forms), 'appmodules', 'webresourceset'. Dataverse accepts the
    RetrieveUnpublished message for only certain customization entity types, and
    sitemap is NOT one of them — a sitemap draft cannot be read this way, so do
    not go looking for it. Ordinary data tables such as 'accounts' have no
    unpublished layer at all.

    Returns one record, not a list. By default a small projection is returned
    with the large XML/binary columns held back (formxml, fetchxml, layoutxml,
    content) — pass select to ask for them explicitly, e.g.
    select=['formid','name','formxml']. select is honoured and validated: an
    unknown column name comes back as an HTTP 400 naming the property.

    IMPORTANT — the returned column set is NOT the set you asked for, and it
    differs in BOTH directions.
      - FEWER: unlike a plain GET, RetrieveUnpublished omits a requested column
        whose value is NULL instead of returning it as null. A missing key means
        "this column is null", NOT "this column does not exist".
      - MORE: the platform also returns columns you never requested. It is an
        open-ended set, not one known extra — _organizationid_value comes back
        on every entity set, and a narrow select on 'systemforms' also returned
        objecttypecode and type. Treat any unrequested column as possible.
    So never assume returned set == requested set, and do NOT diff requested
    against returned to detect a mistyped select column: nulls vanish from that
    diff and platform extras pollute it. The reliable signal for a bad column
    name is Dataverse's own HTTP 400 "Could not find a property named '<name>'",
    surfaced through the error envelope.

    If the record has no unpublished changes, the draft and the published row
    are identical, which is the expected result rather than an error.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    entity_set = params.entity_set_name
    # Belt and braces, and DELIBERATELY UNREACHABLE today: the closed Literal on
    # the model already refuses anything outside this key set, so no call can get
    # here with a bad entity_set (which is why no test exercises this branch).
    # It is kept because the value lands in a URL PATH SEGMENT — the input class
    # of this repo's confirmed live injection — so the URL must not depend on the
    # model alone if the Literal is ever loosened or the model bypassed.
    if entity_set not in _DEFAULT_SELECT:
        return json.dumps({
            "error": True,
            "message": (
                f"entity_set_name '{entity_set}' does not support "
                "RetrieveUnpublished. Supported: "
                f"{', '.join(sorted(_DEFAULT_SELECT))}."
            ),
        })

    select = list(params.select) if params.select else list(_DEFAULT_SELECT[entity_set])

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{entity_set}({params.record_id})"
        f"/Microsoft.Dynamics.CRM.RetrieveUnpublished()"
        f"?{urlencode({'$select': ','.join(select)}, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": (
                    f"No {entity_set} record with id '{params.record_id}' was found "
                    "(HTTP 404). Confirm the id and that the record has not been "
                    "deleted."
                ),
            })
        resp.raise_for_status()

        try:
            body = resp.json()
        except ValueError:
            logger.error(
                "RetrieveUnpublished returned a non-JSON body for %s(%s)",
                entity_set, params.record_id,
            )
            return json.dumps({
                "error": True,
                "message": (
                    "RetrieveUnpublished returned a body that is not JSON "
                    f"(HTTP {resp.status_code}, {len(resp.content)} bytes). "
                    "Retry, and report this if it persists."
                ),
            })

        if not isinstance(body, dict):
            return json.dumps({
                "error": True,
                "message": (
                    "RetrieveUnpublished returned a "
                    f"{type(body).__name__}, not a single record. This tool reads "
                    "one instance-bound record; use dataverse_list_apps with "
                    "include_unpublished=true for the collection form."
                ),
            })

        record = {k: v for k, v in body.items() if not k.startswith("@odata.")}
        if isinstance(record.get("value"), list) and len(record) == 1:
            # A collection envelope, not an instance. Returning it as "record"
            # would hand the caller a fake single record whose only column is
            # named "value"; say so instead.
            return json.dumps({
                "error": True,
                "message": (
                    "RetrieveUnpublished returned a collection envelope rather "
                    "than a single record. This tool reads one instance-bound "
                    "record; use dataverse_list_apps with include_unpublished=true "
                    "for the collection form."
                ),
            })
        if not record:
            return json.dumps({
                "error": True,
                "message": (
                    "RetrieveUnpublished returned an empty record (only the "
                    "@odata envelope). Check that the requested columns exist on "
                    f"{entity_set}: {', '.join(select)}."
                ),
            })

        payload: dict = {
            "entity_set_name": entity_set,
            "record_id": params.record_id,
            "unpublished": True,
            "select": select,
            "record": record,
        }
        held_back = [c for c in _LARGE_COLUMNS[entity_set] if c not in select]
        if held_back:
            payload["message"] = (
                "This is the unpublished (draft) definition. Large column(s) "
                f"{', '.join(held_back)} were not requested — pass them in select "
                "to retrieve them."
            )
        else:
            payload["message"] = "This is the unpublished (draft) definition."
        return finalize_response(payload)
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_retrieve_unpublished")
    except Exception as e:
        return tool_error_response(e, "dataverse_retrieve_unpublished")
