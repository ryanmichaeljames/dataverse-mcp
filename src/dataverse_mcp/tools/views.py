"""View (savedquery) tools for the Dataverse MCP server.

Provides tools to list, inspect, create, and edit Dataverse saved views
without requiring agents to hand-edit raw FetchXml or LayoutXml.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote as _url_quote, urlencode

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException
import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import mcp, write_tool
from dataverse_mcp.client import (
    AppContext,
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
    AddViewColumnInput,
    CreateViewInput,
    GetViewInput,
    ListViewsInput,
    RemoveViewColumnInput,
    UpdateViewInput,
    ValidateViewInput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAVEDQUERY_SELECT = (
    "savedqueryid,name,returnedtypecode,querytype,isdefault,"
    "isquickfindquery,statecode,description"
)
_DEFAULT_CELL_WIDTH = 100
_QUERYTYPE_QUICKFIND = 4
_QUERYTYPE_LOOKUP = 64
_QUERYTYPE_ASSOCIATED = 2
_SAVEDQUERY_COMPONENT_TYPE = 26

_QUERYTYPE_NAMES: dict[int, str] = {
    0: "Main Grid",
    1: "Advanced Find",
    2: "Associated",
    4: "Quick Find",
    8: "Lookup",
    16: "Sub Grid",
    64: "Lookup",
    128: "Main Application View",
    256: "Quick Launch Bar",
    512: "Outlook Filters",
    1024: "Address Book Filters",
    2048: "Offline Filters",
    4096: "Lookup Filters",
    8192: "Retrieve Related",
    16384: "Saved Query Type Retrieve Related",
    32768: "Offline Template",
    65536: "Custom View",
}

_GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)

# Operator enum from Fetch.xsd <xs:simpleType name="operator">
_FETCH_OPERATORS: frozenset[str] = frozenset({
    "eq", "neq", "ne", "gt", "ge", "le", "lt", "like", "not-like", "in",
    "not-in", "between", "not-between", "null", "not-null", "yesterday",
    "today", "tomorrow", "last-seven-days", "next-seven-days", "last-week",
    "this-week", "next-week", "last-month", "this-month", "next-month", "on",
    "on-or-before", "on-or-after", "last-year", "this-year", "next-year",
    "last-x-hours", "next-x-hours", "last-x-days", "next-x-days",
    "last-x-weeks", "next-x-weeks", "last-x-months", "next-x-months",
    "olderthan-x-months", "olderthan-x-years", "olderthan-x-weeks",
    "olderthan-x-days", "olderthan-x-hours", "olderthan-x-minutes",
    "last-x-years", "next-x-years",
    "eq-userid", "ne-userid", "eq-userteams", "eq-useroruserteams",
    "eq-useroruserhierarchy", "eq-useroruserhierarchyandteams",
    "eq-businessid", "ne-businessid", "eq-userlanguage",
    "this-fiscal-year", "this-fiscal-period", "next-fiscal-year",
    "next-fiscal-period", "last-fiscal-year", "last-fiscal-period",
    "last-x-fiscal-years", "last-x-fiscal-periods",
    "next-x-fiscal-years", "next-x-fiscal-periods",
    "in-fiscal-year", "in-fiscal-period", "in-fiscal-period-and-year",
    "in-or-before-fiscal-period-and-year", "in-or-after-fiscal-period-and-year",
    "begins-with", "not-begin-with", "ends-with", "not-end-with",
    "under", "eq-or-under", "not-under", "above", "eq-or-above",
    "contain-values", "not-contain-values",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_view(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    view_id: str,
) -> dict | None:
    """GET /savedqueries(<id>)?$select=...,fetchxml,layoutxml. 404 → None."""
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/savedqueries({view_id})"
        f"?$select={_SAVEDQUERY_SELECT},fetchxml,layoutxml"
    )
    resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    # Normalize empty layoutxml to None
    if not data.get("layoutxml"):
        data["layoutxml"] = None
    return data


async def _resolve_entity_view_info(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    table: str,
) -> dict:
    """Fetch ObjectTypeCode, PrimaryIdAttribute, PrimaryNameAttribute, EntitySetName."""
    table_enc = _url_quote(table, safe="")
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/EntityDefinitions(LogicalName='{table_enc}')"
        f"?$select=ObjectTypeCode,PrimaryIdAttribute,PrimaryNameAttribute,EntitySetName"
    )
    resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return {
        "otc": data["ObjectTypeCode"],
        "primary_id": data["PrimaryIdAttribute"],
        "primary_name": data["PrimaryNameAttribute"],
        "entity_set": data["EntitySetName"],
    }


async def _resolve_columns(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    table: str,
    names: list[str],
) -> dict[str, str]:
    """Confirm each column exists; return {logical_name: display_name}. Raise on missing."""
    if not names:
        return {}
    table_enc = _url_quote(table, safe="")
    name_filters = " or ".join(
        f"LogicalName eq '{odata_quote(n)}'" for n in names
    )
    query = urlencode(
        {"$filter": name_filters, "$select": "LogicalName,DisplayName"},
        safe="$,",
    )
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/EntityDefinitions(LogicalName='{table_enc}')/Attributes?{query}"
    )
    resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
    resp.raise_for_status()
    items = resp.json().get("value", [])
    found: dict[str, str] = {}
    for item in items:
        logical = item.get("LogicalName", "")
        dn_obj = item.get("DisplayName") or {}
        localized = dn_obj.get("LocalizedLabels") or []
        display = localized[0].get("Label") if localized else logical
        found[logical] = display
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"Column(s) not found on table '{table}': {', '.join(missing)}")
    return found


async def _publish_table(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    table_logical_name: str,
) -> None:
    xml = (
        f"<importexportxml>"
        f"<entities><entity>{table_logical_name}</entity></entities>"
        f"<optionsets /><relationships />"
        f"</importexportxml>"
    )
    resp = await request_with_retry(app_ctx.http_client, "POST",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/PublishXml",
        json={"ParameterXml": xml},
        headers={**headers, "Content-Type": "application/json"},
    )
    resp.raise_for_status()


async def _add_component_to_solution(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    view_id: str,
    solution_unique_name: str,
) -> None:
    """AddSolutionComponent for a view (component type 26 = SavedQuery)."""
    body = {
        "ComponentId": view_id,
        "ComponentType": _SAVEDQUERY_COMPONENT_TYPE,
        "SolutionUniqueName": solution_unique_name,
        "AddRequiredComponents": False,
        "DoNotIncludeSubcomponents": False,
    }
    resp = await request_with_retry(app_ctx.http_client, "POST",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/AddSolutionComponent",
        json=body,
        headers=headers,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# XML builders (view-type aware)
# ---------------------------------------------------------------------------


def _grid_names(query_type: int, entity_set: str, table_logical: str) -> tuple[str, str]:
    """Return (grid_name, row_name) per view type.
    Associated (2) and lookup (64) use entity_set / table_logical; everything else uses resultset / result.
    """
    if query_type in (_QUERYTYPE_ASSOCIATED, _QUERYTYPE_LOOKUP):
        return entity_set, table_logical
    return "resultset", "result"


def _grid_preview(query_type: int) -> str:
    return "0" if query_type == _QUERYTYPE_LOOKUP else "1"


def _build_layoutxml(
    otc: int,
    primary_id: str,
    primary_name: str,
    columns: list[str],
    *,
    query_type: int,
    entity_set: str,
    table_logical: str,
    widths: dict[str, int] | None = None,
) -> str:
    grid_name, row_name = _grid_names(query_type, entity_set, table_logical)
    preview = _grid_preview(query_type)
    grid = ET.Element("grid", {
        "name": grid_name,
        "object": str(otc),
        "jump": primary_name,
        "select": "1",
        "icon": "1",
        "preview": preview,
    })
    row = ET.SubElement(grid, "row", {"name": row_name, "id": primary_id})
    w = widths or {}
    for col in columns:
        ET.SubElement(row, "cell", {"name": col, "width": str(w.get(col, _DEFAULT_CELL_WIDTH))})
    return ET.tostring(grid, encoding="unicode")


def _build_fetchxml(
    table: str,
    primary_id: str,
    columns: list[str],
    *,
    sort: list | None = None,
    filter_elems: list[ET.Element] | None = None,
) -> str:
    fetch = ET.Element("fetch", {"version": "1.0", "mapping": "logical"})
    entity = ET.SubElement(fetch, "entity", {"name": table})
    # Primary ID always first
    ET.SubElement(entity, "attribute", {"name": primary_id})
    for col in columns:
        if col != primary_id:
            ET.SubElement(entity, "attribute", {"name": col})
    for s in (sort or []):
        attr = s.attribute if hasattr(s, "attribute") else s["attribute"]
        desc = s.descending if hasattr(s, "descending") else s.get("descending", False)
        ET.SubElement(entity, "order", {
            "attribute": attr,
            "descending": "true" if desc else "false",
        })
    for fe in (filter_elems or []):
        entity.append(fe)
    return ET.tostring(fetch, encoding="unicode")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_fetch(fetchxml: str) -> dict:
    """Parse FetchXml into structured dict with columns, sort, filters, quick_find_fields."""
    try:
        root = ET.fromstring(fetchxml)
    except ET.ParseError:
        return {"table": None, "columns": [], "sort": [], "filters": [], "quick_find_fields": []}

    entity = root.find("entity")
    if entity is None:
        return {"table": None, "columns": [], "sort": [], "filters": [], "quick_find_fields": []}

    table = entity.get("name", "")
    columns = [a.get("name") for a in entity.findall("attribute") if a.get("name")]
    sort = [
        {
            "attribute": o.get("attribute"),
            "descending": o.get("descending", "false").lower() == "true",
        }
        for o in entity.findall("order")
        if o.get("attribute")
    ]
    filters: list[str] = []
    quick_find_fields: list[str] = []
    for f in entity.findall("filter"):
        if f.get("isquickfindfields"):
            for cond in f.iter("condition"):
                attr = cond.get("attribute")
                if attr:
                    quick_find_fields.append(attr)
        else:
            filters.append(ET.tostring(f, encoding="unicode"))

    return {
        "table": table,
        "columns": columns,
        "sort": sort,
        "filters": filters,
        "quick_find_fields": quick_find_fields,
    }


def _parse_layout(layoutxml: str | None) -> dict:
    """Parse LayoutXml into structured dict. Returns empty columns when layoutxml is None."""
    if not layoutxml:
        return {"grid_name": None, "row_name": None, "object": None, "columns": []}
    try:
        root = ET.fromstring(layoutxml)
    except ET.ParseError:
        return {"grid_name": None, "row_name": None, "object": None, "columns": []}
    row = root.find("row")
    columns: list[dict] = []
    if row is not None:
        columns = [
            {"name": c.get("name"), "width": int(c.get("width", _DEFAULT_CELL_WIDTH))}
            for c in row.findall("cell")
            if c.get("name")
        ]
    return {
        "grid_name": root.get("name"),
        "row_name": row.get("name") if row is not None else None,
        "object": root.get("object"),
        "columns": columns,
    }


# ---------------------------------------------------------------------------
# Quick Find protection
# ---------------------------------------------------------------------------


def _strip_quickfind_filters(entity_elem: ET.Element) -> list[str]:
    """Remove <filter isquickfindfields=...> from the entity element in place.

    Returns the list of search field names that were in those filters. Callers
    must surface a quick_find_warning if the list is non-empty, because Dataverse
    rejects PATCH with isquickfindfields blocks (HTTP 400 / 0x80040216).
    """
    to_remove = [f for f in entity_elem.findall("filter") if f.get("isquickfindfields")]
    fields: list[str] = []
    for f in to_remove:
        for cond in f.iter("condition"):
            attr = cond.get("attribute")
            if attr:
                fields.append(attr)
        entity_elem.remove(f)
    return fields


# ---------------------------------------------------------------------------
# Validation — 16 rules from Fetch.xsd / layout schema
# ---------------------------------------------------------------------------


def _validate_view_xml(fetchxml: str, layoutxml: str | None) -> list[str]:
    """Validate FetchXml (rules 1–8) and LayoutXml (rules 9–15) plus cross-field (rule 16).

    layoutxml=None skips rules 9–16 (valid for querytype 8192 / no-grid views).
    Returns a list of error strings; empty list means valid.
    """
    errors: list[str] = []

    # Rule 1: Well-formed FetchXml
    try:
        fetch_root = ET.fromstring(fetchxml)
    except ET.ParseError as exc:
        return [f"FetchXml is not well-formed: {exc}"]

    # Rule 2: Root must be <fetch>
    if fetch_root.tag != "fetch":
        errors.append(f"FetchXml root must be <fetch>, found <{fetch_root.tag}>.")
        return errors

    # Rule 3: Exactly one <entity> with name
    entities = fetch_root.findall("entity")
    if len(entities) != 1:
        errors.append(f"FetchXml must have exactly one <entity>, found {len(entities)}.")
        return errors
    entity = entities[0]
    if not entity.get("name"):
        errors.append("FetchXml <entity> is missing the required 'name' attribute.")

    # Rule 4: Each <attribute> must have name
    for i, attr in enumerate(entity.findall("attribute")):
        if not attr.get("name"):
            errors.append(f"FetchXml <attribute>[{i}] is missing the required 'name' attribute.")

    # Rule 5: Each <order> must have attribute
    for i, order in enumerate(entity.findall("order")):
        if not order.get("attribute"):
            errors.append(f"FetchXml <order>[{i}] is missing the required 'attribute' attribute.")

    # Rule 6: <filter type> must be "and" or "or" when specified
    for f in entity.iter("filter"):
        ftype = f.get("type")
        if ftype is not None and ftype not in ("and", "or"):
            errors.append(f"FetchXml <filter type='{ftype}'> must be 'and' or 'or'.")

    # Rule 7: <condition operator> must be a valid FetchXml operator
    for cond in entity.iter("condition"):
        op = cond.get("operator")
        if op and op not in _FETCH_OPERATORS:
            errors.append(f"FetchXml <condition operator='{op}'> is not a valid FetchXml operator.")

    # Rule 8: At least one <attribute>
    attrs = entity.findall("attribute")
    if not attrs:
        errors.append("FetchXml <entity> must have at least one <attribute>.")

    fetch_attr_names = {a.get("name") for a in attrs if a.get("name")}

    # Skip layout rules for null-layout views (e.g. querytype 8192)
    if layoutxml is None:
        return errors

    # Rule 9: Well-formed LayoutXml
    try:
        grid_root = ET.fromstring(layoutxml)
    except ET.ParseError as exc:
        errors.append(f"LayoutXml is not well-formed: {exc}")
        return errors

    # Rule 10: Root must be <grid>
    if grid_root.tag != "grid":
        errors.append(f"LayoutXml root must be <grid>, found <{grid_root.tag}>.")
        return errors

    # Rule 11: <grid> required attributes: name, object, select
    for req_attr in ("name", "object", "select"):
        if not grid_root.get(req_attr):
            errors.append(f"LayoutXml <grid> is missing the required '{req_attr}' attribute.")

    # Rule 12: Exactly one <row>
    rows = grid_root.findall("row")
    if len(rows) != 1:
        errors.append(f"LayoutXml <grid> must have exactly one <row>, found {len(rows)}.")
        if not rows:
            return errors
    row = rows[0]

    # Rule 13: <row> must have name and id attributes
    if not row.get("name"):
        errors.append("LayoutXml <row> is missing the required 'name' attribute.")
    if not row.get("id"):
        errors.append("LayoutXml <row> is missing the required 'id' attribute.")

    # Rule 14: At least one <cell> with name
    named_cells = [c for c in row.findall("cell") if c.get("name")]
    if not named_cells:
        errors.append("LayoutXml <row> must have at least one <cell> with a 'name' attribute.")

    # Rule 15: No duplicate cell names
    seen_cells: set[str] = set()
    for cell in named_cells:
        cname = cell.get("name")
        if cname in seen_cells:
            errors.append(
                f"LayoutXml duplicate <cell name='{cname}'> — each column may appear only once."
            )
        else:
            seen_cells.add(cname)

    # Rule 16: Every layout cell must have a matching fetch attribute
    for cname in seen_cells:
        if cname not in fetch_attr_names:
            errors.append(
                f"LayoutXml <cell name='{cname}'> has no matching "
                f"<attribute name='{cname}'> in FetchXml."
            )

    return errors


async def _patch_view(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    view_id: str,
    body: dict,
) -> None:
    """Validate fetchxml/layoutxml (when present) then PATCH /savedqueries(<id>).

    Raises ValueError on validation failure so callers never send malformed XML.
    """
    if "fetchxml" in body:
        errors = _validate_view_xml(body["fetchxml"], body.get("layoutxml"))
        if errors:
            raise ValueError(
                f"View XML validation failed — PATCH aborted. "
                f"{len(errors)} error(s): " + "; ".join(errors)
            )
    patch_headers = {**headers, "Content-Type": "application/json"}
    resp = await request_with_retry(app_ctx.http_client, "PATCH",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/savedqueries({view_id})",
        json=body,
        headers=patch_headers,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Tool 1: dataverse_list_views
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_list_views",
    annotations={
        "title": "List Views",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_views(params: ListViewsInput, ctx: Context) -> str:
    """List saved views (savedqueries) registered in the Dataverse environment.

    Returns metadata: id, name, querytype, isdefault, statecode. Filter by
    table_logical_name and/or query_type (0=Main Grid, 1=Advanced Find,
    2=Associated, 4=Quick Find, 64=Lookup). Use dataverse_get_view for layout.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    filters: list[str] = []
    if params.table_logical_name:
        filters.append(f"returnedtypecode eq '{odata_quote(params.table_logical_name)}'")
    if params.query_type is not None:
        filters.append(f"querytype eq {params.query_type}")

    query: dict[str, str] = {
        "$select": _SAVEDQUERY_SELECT,
        "$top": str(params.top),
    }
    if filters:
        query["$filter"] = " and ".join(filters)

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/savedqueries?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        views = [
            {
                "view_id": r.get("savedqueryid"),
                "name": r.get("name"),
                "table": r.get("returnedtypecode"),
                "query_type": r.get("querytype"),
                "query_type_name": _QUERYTYPE_NAMES.get(r.get("querytype", -1), "Unknown"),
                "is_default": r.get("isdefault"),
                "is_quick_find": r.get("isquickfindquery"),
                "statecode": r.get("statecode"),
                "description": r.get("description"),
            }
            for r in records
        ]
        return finalize_response({
            "views": views,
            "count": len(views),
            "has_more": len(records) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_views")


# ---------------------------------------------------------------------------
# Tool 2: dataverse_get_view
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_get_view",
    annotations={
        "title": "Get View",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_view(params: GetViewInput, ctx: Context) -> str:
    """Get a single Dataverse view's layout as structured JSON.

    Parses FetchXml and LayoutXml into readable columns, sort, and filter
    lists. Returns fetchxml_backup and layoutxml_backup for reference.
    quick_find_fields is populated for Quick Find views (querytype=4).
    Use dataverse_list_views to discover view IDs.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        record = await _fetch_view(app_ctx, base_url, headers, params.view_id)
        if record is None:
            return json.dumps({"error": True, "message": f"View '{params.view_id}' not found."})

        fetchxml = record.get("fetchxml") or ""
        layoutxml = record.get("layoutxml")

        parsed_fetch = _parse_fetch(fetchxml)
        parsed_layout = _parse_layout(layoutxml)
        query_type = record.get("querytype", 0)

        return finalize_response({
            "view_id": record.get("savedqueryid"),
            "name": record.get("name"),
            "table": record.get("returnedtypecode"),
            "query_type": query_type,
            "query_type_name": _QUERYTYPE_NAMES.get(query_type, "Unknown"),
            "is_default": record.get("isdefault"),
            "is_quick_find": record.get("isquickfindquery"),
            "statecode": record.get("statecode"),
            "description": record.get("description"),
            "fetch": {
                "columns": parsed_fetch["columns"],
                "sort": parsed_fetch["sort"],
                "filters": parsed_fetch["filters"],
                "quick_find_fields": parsed_fetch["quick_find_fields"],
            },
            "layout": parsed_layout,
            "fetchxml_backup": fetchxml,
            "layoutxml_backup": layoutxml,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_get_view")


# ---------------------------------------------------------------------------
# Tool 3: dataverse_validate_view
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_validate_view",
    annotations={
        "title": "Validate View",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_validate_view(params: ValidateViewInput, ctx: Context) -> str:
    """Validate a Dataverse view's FetchXml and LayoutXml against 16 structural rules.

    Fetches the live XML and checks FetchXml structure (rules 1-8), LayoutXml
    structure (rules 9-15), and column cross-reference (rule 16). Layout rules
    are skipped when layoutxml is null. Write tools run this automatically
    before every PATCH — use this for a standalone pre-check.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        record = await _fetch_view(app_ctx, base_url, headers, params.view_id)
        if record is None:
            return json.dumps({"error": True, "message": f"View '{params.view_id}' not found."})

        fetchxml = record.get("fetchxml") or ""
        layoutxml = record.get("layoutxml")
        errors = _validate_view_xml(fetchxml, layoutxml)

        if errors:
            return finalize_response({
                "valid": False,
                "view_id": params.view_id,
                "view_name": record.get("name"),
                "table": record.get("returnedtypecode"),
                "error_count": len(errors),
                "errors": errors,
            })

        parsed_fetch = _parse_fetch(fetchxml)
        parsed_layout = _parse_layout(layoutxml)
        return finalize_response({
            "valid": True,
            "view_id": params.view_id,
            "view_name": record.get("name"),
            "table": record.get("returnedtypecode"),
            "column_count": len(parsed_layout["columns"]),
            "columns": [c["name"] for c in parsed_layout["columns"]],
            "sort": parsed_fetch["sort"],
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_validate_view")


# ---------------------------------------------------------------------------
# Tool 4: dataverse_create_view
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_view",
    annotations={
        "title": "Create View",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_view(params: CreateViewInput, ctx: Context) -> str:
    """Create a new saved view (savedquery) for a Dataverse table.

    Builds FetchXml and LayoutXml from the supplied column list automatically.
    Validates the generated XML before posting (16-rule check).
    Publishes automatically — no separate publish needed.
    Returns the new view's fetchxml and layoutxml for reference.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # 1. Resolve entity metadata
        try:
            info = await _resolve_entity_view_info(
                app_ctx, base_url, headers, params.table_logical_name
            )
        except httpx.HTTPStatusError:
            return json.dumps({
                "error": True,
                "message": f"Table '{params.table_logical_name}' not found or inaccessible.",
            })

        # 2. Confirm columns exist
        try:
            await _resolve_columns(
                app_ctx, base_url, headers, params.table_logical_name, params.columns
            )
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)})

        # 3. Build XML
        filter_elems: list[ET.Element] = []
        if params.filter_fetchxml:
            try:
                fe = DET.fromstring(params.filter_fetchxml)
                filter_elems = [fe]
            except DefusedXmlException:
                return json.dumps({
                    "error": True,
                    "message": "filter_fetchxml contains forbidden XML constructs (entities/DTD) and was rejected.",
                })
            except ET.ParseError as exc:
                return json.dumps({
                    "error": True,
                    "message": f"filter_fetchxml is not well-formed: {exc}",
                })

        fetchxml = _build_fetchxml(
            params.table_logical_name,
            info["primary_id"],
            params.columns,
            sort=params.sort,
            filter_elems=filter_elems,
        )
        layoutxml = _build_layoutxml(
            info["otc"],
            info["primary_id"],
            info["primary_name"],
            params.columns,
            query_type=params.query_type,
            entity_set=info["entity_set"],
            table_logical=params.table_logical_name,
            widths=params.widths,
        )

        # 4. Validate before posting
        errors = _validate_view_xml(fetchxml, layoutxml)
        if errors:
            return json.dumps({
                "error": True,
                "message": f"XML validation failed: " + "; ".join(errors),
            })

        # 5. POST
        post_body: dict = {
            "name": params.name,
            "returnedtypecode": params.table_logical_name,
            "fetchxml": fetchxml,
            "layoutxml": layoutxml,
            "querytype": params.query_type,
            "isdefault": params.is_default,
        }
        if params.description is not None:
            post_body["description"] = params.description

        post_headers = {**headers, "Content-Type": "application/json"}
        resp = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/savedqueries",
            json=post_body,
            headers=post_headers,
        )
        resp.raise_for_status()

        # Extract new view ID from OData-EntityId header
        entity_id_header = resp.headers.get("OData-EntityId", "")
        guid_match = _GUID_RE.search(entity_id_header)
        new_id = guid_match.group(0).lower() if guid_match else None
        logger.info("Created view '%s' id=%s for table %s", params.name, new_id, params.table_logical_name)

        # 6. Add to solution if requested
        if params.solution_unique_name and new_id:
            try:
                await _add_component_to_solution(
                    app_ctx, base_url, headers, new_id, params.solution_unique_name
                )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Could not add view to solution %s: %d %s",
                    params.solution_unique_name, e.response.status_code, e.response.text,
                )

        # 7. Publish
        published = False
        try:
            await _publish_table(app_ctx, base_url, headers, params.table_logical_name)
            published = True
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        return finalize_response({
            "created": True,
            "view_id": new_id,
            "name": params.name,
            "table": params.table_logical_name,
            "columns": params.columns,
            "query_type": params.query_type,
            "is_default": params.is_default,
            "published": published,
            "solution_unique_name": params.solution_unique_name,
            "fetchxml": fetchxml,
            "layoutxml": layoutxml,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_create_view")


# ---------------------------------------------------------------------------
# Tool 5: dataverse_update_view
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_view",
    annotations={
        "title": "Update View",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_update_view(params: UpdateViewInput, ctx: Context) -> str:
    """Update an existing Dataverse view's columns, sort, filters, or name.

    Existing non-quickfind filters are preserved unless filter_fetchxml is given.
    Quick Find filter blocks are stripped before PATCH (Dataverse rejects them) —
    quick_find_warning names any dropped fields for restoration via the maker portal.
    Publishes automatically — no separate publish needed.
    Returns fetchxml_backup and layoutxml_backup for rollback.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # 1. Fetch existing view
        record = await _fetch_view(app_ctx, base_url, headers, params.view_id)
        if record is None:
            return json.dumps({"error": True, "message": f"View '{params.view_id}' not found."})

        backup_fetchxml = record.get("fetchxml") or ""
        backup_layoutxml = record.get("layoutxml")
        table = record.get("returnedtypecode", "")
        query_type = record.get("querytype", 0)

        # 2. Resolve entity metadata
        info = await _resolve_entity_view_info(app_ctx, base_url, headers, table)

        # 3. Parse existing fetch
        parsed = _parse_fetch(backup_fetchxml)

        # 4. Strip Quick Find filters from the entity element (can't send these in a PATCH)
        fetch_root = ET.fromstring(backup_fetchxml)
        entity = fetch_root.find("entity")
        qf_dropped = _strip_quickfind_filters(entity)

        # 5. Determine preserved or replaced filters
        if params.filter_fetchxml is not None:
            try:
                new_fe = DET.fromstring(params.filter_fetchxml)
                preserved_filters = [new_fe]
            except DefusedXmlException:
                return json.dumps({
                    "error": True,
                    "message": "filter_fetchxml contains forbidden XML constructs (entities/DTD) and was rejected.",
                })
            except ET.ParseError as exc:
                return json.dumps({
                    "error": True,
                    "message": f"filter_fetchxml is not well-formed: {exc}",
                })
        else:
            # Re-use existing non-quickfind filters from the entity we already stripped QF from
            preserved_filters = entity.findall("filter")

        # 6. Determine new columns and sort
        new_cols = params.columns if params.columns is not None else parsed["columns"]
        new_sort = params.sort if params.sort is not None else parsed["sort"]

        # 7. Validate new columns exist if replacing
        if params.columns is not None:
            try:
                await _resolve_columns(app_ctx, base_url, headers, table, params.columns)
            except ValueError as e:
                return json.dumps({"error": True, "message": str(e)})

        # 8. Build new fetchxml
        new_fetchxml = _build_fetchxml(
            table,
            info["primary_id"],
            new_cols,
            sort=new_sort,
            filter_elems=preserved_filters,
        )

        # 9. Build new layoutxml only if columns or widths changed
        patch_body: dict = {"fetchxml": new_fetchxml}
        if params.columns is not None or params.widths is not None:
            new_layoutxml = _build_layoutxml(
                info["otc"],
                info["primary_id"],
                info["primary_name"],
                new_cols,
                query_type=query_type,
                entity_set=info["entity_set"],
                table_logical=table,
                widths=params.widths,
            )
            patch_body["layoutxml"] = new_layoutxml

        if params.name is not None:
            patch_body["name"] = params.name

        # 10. Validate and PATCH
        await _patch_view(app_ctx, base_url, headers, params.view_id, patch_body)
        logger.info("Updated view %s", params.view_id)

        # 11. Add to solution if requested
        if params.solution_unique_name:
            try:
                await _add_component_to_solution(
                    app_ctx, base_url, headers, params.view_id, params.solution_unique_name
                )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Could not add view to solution %s: %d %s",
                    params.solution_unique_name, e.response.status_code, e.response.text,
                )

        # 12. Publish
        try:
            await _publish_table(app_ctx, base_url, headers, table)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        result: dict = {
            "updated": True,
            "view_id": params.view_id,
            "table": table,
            "columns": new_cols,
            "published": True,
            "solution_unique_name": params.solution_unique_name,
            "fetchxml_backup": backup_fetchxml,
            "layoutxml_backup": backup_layoutxml,
        }
        if qf_dropped:
            result["quick_find_warning"] = (
                f"Quick Find search fields were stripped before PATCH because Dataverse rejects "
                f"isquickfindfields blocks (HTTP 400 / 0x80040216). Dropped fields: "
                f"{', '.join(qf_dropped)}. Restore them via the maker portal or solution import."
            )
        return finalize_response(result)

    except Exception as e:
        return tool_error_response(e, "dataverse_update_view")


# ---------------------------------------------------------------------------
# Tool 6: dataverse_add_view_column
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_add_view_column",
    annotations={
        "title": "Add View Column",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_add_view_column(params: AddViewColumnInput, ctx: Context) -> str:
    """Add a single column to a Dataverse view's FetchXml and LayoutXml.

    Preserves all other columns, sort, and filters by construction. Returns a
    no-op result if the column is already present. Quick Find filter blocks are
    stripped before PATCH — quick_find_warning names any dropped fields.
    Publishes automatically — no separate publish needed.
    Returns fetchxml_backup and layoutxml_backup for rollback.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # 1. Fetch existing view
        record = await _fetch_view(app_ctx, base_url, headers, params.view_id)
        if record is None:
            return json.dumps({"error": True, "message": f"View '{params.view_id}' not found."})

        backup_fetchxml = record.get("fetchxml") or ""
        backup_layoutxml = record.get("layoutxml")
        table = record.get("returnedtypecode", "")

        if backup_layoutxml is None:
            return json.dumps({
                "error": True,
                "message": (
                    f"View '{params.view_id}' has no grid layout (layoutxml is null). "
                    "This view type does not support column editing."
                ),
            })

        # 2. Verify the column exists on the table
        try:
            await _resolve_columns(app_ctx, base_url, headers, table, [params.column])
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)})

        # 3. Parse XML trees
        fetch_root = ET.fromstring(backup_fetchxml)
        entity = fetch_root.find("entity")
        grid_root = ET.fromstring(backup_layoutxml)
        row = grid_root.find("row")

        if entity is None:
            return json.dumps({"error": True, "message": "FetchXml has no <entity> element."})
        if row is None:
            return json.dumps({"error": True, "message": "LayoutXml has no <row> element."})

        # 4. Strip Quick Find filters
        qf_dropped = _strip_quickfind_filters(entity)

        # 5. Check if column already present in fetch (no-op)
        existing_attrs = {a.get("name") for a in entity.findall("attribute")}
        existing_cells = {c.get("name") for c in row.findall("cell")}

        if params.column in existing_attrs and params.column in existing_cells:
            return json.dumps({
                "added": False,
                "no_op": True,
                "column": params.column,
                "view_id": params.view_id,
                "message": f"Column '{params.column}' is already present in the view.",
            })

        # 6. Insert <attribute> before any <order> or <filter> elements
        children = list(entity)
        insert_attr_pos = len(children)
        for i, child in enumerate(children):
            if child.tag in ("order", "filter"):
                insert_attr_pos = i
                break

        if params.column not in existing_attrs:
            new_attr = ET.Element("attribute", {"name": params.column})
            entity.insert(insert_attr_pos, new_attr)

        # 7. Insert <cell> into the row at the specified position
        if params.column not in existing_cells:
            width = str(params.width if params.width is not None else _DEFAULT_CELL_WIDTH)
            new_cell = ET.Element("cell", {"name": params.column, "width": width})
            existing_cell_list = row.findall("cell")
            if params.position is None or params.position >= len(existing_cell_list):
                row.append(new_cell)
            else:
                row.insert(params.position, new_cell)

        # 8. Serialize and validate
        new_fetchxml = ET.tostring(fetch_root, encoding="unicode")
        new_layoutxml = ET.tostring(grid_root, encoding="unicode")

        # 9. PATCH (validates internally)
        await _patch_view(
            app_ctx, base_url, headers, params.view_id,
            {"fetchxml": new_fetchxml, "layoutxml": new_layoutxml},
        )
        logger.info("Added column '%s' to view %s", params.column, params.view_id)

        # 10. Add to solution
        if params.solution_unique_name:
            try:
                await _add_component_to_solution(
                    app_ctx, base_url, headers, params.view_id, params.solution_unique_name
                )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Could not add view to solution %s: %d %s",
                    params.solution_unique_name, e.response.status_code, e.response.text,
                )

        # 11. Publish
        try:
            await _publish_table(app_ctx, base_url, headers, table)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        result: dict = {
            "added": True,
            "column": params.column,
            "view_id": params.view_id,
            "published": True,
            "solution_unique_name": params.solution_unique_name,
            "fetchxml_backup": backup_fetchxml,
            "layoutxml_backup": backup_layoutxml,
        }
        if qf_dropped:
            result["quick_find_warning"] = (
                f"Quick Find search fields were stripped before PATCH because Dataverse rejects "
                f"isquickfindfields blocks (HTTP 400 / 0x80040216). Dropped fields: "
                f"{', '.join(qf_dropped)}. Restore them via the maker portal or solution import."
            )
        return finalize_response(result)

    except Exception as e:
        return tool_error_response(e, "dataverse_add_view_column")


# ---------------------------------------------------------------------------
# Tool 7: dataverse_remove_view_column
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_remove_view_column",
    annotations={
        "title": "Remove View Column",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_remove_view_column(params: RemoveViewColumnInput, ctx: Context) -> str:
    """Remove a single column from a Dataverse view's FetchXml and LayoutXml.

    Preserves all other columns, sort, and filters by construction. Returns an
    error if the column is not present — no change is made. Quick Find filter
    blocks are stripped before PATCH — quick_find_warning names any dropped fields.
    Publishes automatically — no separate publish needed.
    Returns fetchxml_backup and layoutxml_backup for rollback.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # 1. Fetch existing view
        record = await _fetch_view(app_ctx, base_url, headers, params.view_id)
        if record is None:
            return json.dumps({"error": True, "message": f"View '{params.view_id}' not found."})

        backup_fetchxml = record.get("fetchxml") or ""
        backup_layoutxml = record.get("layoutxml")
        table = record.get("returnedtypecode", "")

        if backup_layoutxml is None:
            return json.dumps({
                "error": True,
                "message": (
                    f"View '{params.view_id}' has no grid layout (layoutxml is null). "
                    "This view type does not support column editing."
                ),
            })

        # 2. Parse XML trees
        fetch_root = ET.fromstring(backup_fetchxml)
        entity = fetch_root.find("entity")
        grid_root = ET.fromstring(backup_layoutxml)
        row = grid_root.find("row")

        if entity is None:
            return json.dumps({"error": True, "message": "FetchXml has no <entity> element."})
        if row is None:
            return json.dumps({"error": True, "message": "LayoutXml has no <row> element."})

        # 3. Strip Quick Find filters
        qf_dropped = _strip_quickfind_filters(entity)

        # 4. Verify the column is present
        existing_attrs = {a.get("name"): a for a in entity.findall("attribute")}
        existing_cells = {c.get("name"): c for c in row.findall("cell")}

        if params.column not in existing_attrs and params.column not in existing_cells:
            return json.dumps({
                "error": True,
                "message": (
                    f"Column '{params.column}' is not present in view '{params.view_id}'. "
                    "Nothing was changed."
                ),
            })

        # 5. Remove <attribute> from entity
        if params.column in existing_attrs:
            entity.remove(existing_attrs[params.column])

        # 6. Remove <cell> from row
        if params.column in existing_cells:
            row.remove(existing_cells[params.column])

        # 7. Serialize and validate
        new_fetchxml = ET.tostring(fetch_root, encoding="unicode")
        new_layoutxml = ET.tostring(grid_root, encoding="unicode")

        # 8. PATCH (validates internally)
        await _patch_view(
            app_ctx, base_url, headers, params.view_id,
            {"fetchxml": new_fetchxml, "layoutxml": new_layoutxml},
        )
        logger.info("Removed column '%s' from view %s", params.column, params.view_id)

        # 9. Add to solution
        if params.solution_unique_name:
            try:
                await _add_component_to_solution(
                    app_ctx, base_url, headers, params.view_id, params.solution_unique_name
                )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Could not add view to solution %s: %d %s",
                    params.solution_unique_name, e.response.status_code, e.response.text,
                )

        # 10. Publish
        try:
            await _publish_table(app_ctx, base_url, headers, table)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        result: dict = {
            "removed": True,
            "column": params.column,
            "view_id": params.view_id,
            "published": True,
            "solution_unique_name": params.solution_unique_name,
            "fetchxml_backup": backup_fetchxml,
            "layoutxml_backup": backup_layoutxml,
        }
        if qf_dropped:
            result["quick_find_warning"] = (
                f"Quick Find search fields were stripped before PATCH because Dataverse rejects "
                f"isquickfindfields blocks (HTTP 400 / 0x80040216). Dropped fields: "
                f"{', '.join(qf_dropped)}. Restore them via the maker portal or solution import."
            )
        return finalize_response(result)

    except Exception as e:
        return tool_error_response(e, "dataverse_remove_view_column")
