"""Form tools for the Dataverse MCP server.

Provides tools to list, inspect, and edit Dataverse model-driven app forms
(systemform records) without requiring agents to hand-edit raw FormXml.
"""

import json
import logging
import re
import uuid
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
    AddFormControlInput,
    GetFormInput,
    ListFormsInput,
    RemoveFormControlInput,
    SetFormXmlInput,
    ValidateFormInput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Control classid constants
# ---------------------------------------------------------------------------

_SINGLE_LINE_CLASSID = "{4273EDBD-AC1D-40d3-9FB2-095C621B552D}"
_MULTILINE_CLASSID = "{E0DECE4B-6FC8-4A8F-A065-082708572369}"

# String FormatName.Value → classid overrides
_STRING_FORMAT_CLASSIDS: dict[str, str] = {
    "TextArea": _MULTILINE_CLASSID,
    "Email": "{ADA2203E-B4CD-49BE-9DDF-234642B43B52}",
    "Phone": "{ADA2203E-B4CD-49BE-9DDF-234642B43B52}",
    "Url": "{71716B6C-711D-476B-B3CB-F4FE822F5C3A}",
    "Ticker": "{1E1C1485-1E17-4351-B4C1-C5659CBFA2C0}",
}

# AttributeType → classid
_TYPE_CLASSIDS: dict[str, str] = {
    "Integer": "{C6D124CA-7EDA-4A60-AAE9-7E024D73E230}",
    "BigInt": "{C6D124CA-7EDA-4A60-AAE9-7E024D73E230}",
    "Decimal": "{C3EFE0C3-0EC6-42BE-8349-CBD9079C548D}",
    "Double": "{C3EFE0C3-0EC6-42BE-8349-CBD9079C548D}",
    "Money": "{533B9E00-756B-4312-95A0-DC888BA018BE}",
    "Boolean": "{67FAC785-CD58-4F9F-ABB3-4B7DDC6ED5ED}",
    "DateTime": "{5B773807-9FB2-42DB-97C3-7A91EFF8ADFF}",
    "Lookup": "{270BD3DB-D9AF-4782-9025-509E298DEC0A}",
    "Owner": "{270BD3DB-D9AF-4782-9025-509E298DEC0A}",
    "Customer": "{270BD3DB-D9AF-4782-9025-509E298DEC0A}",
    "Picklist": "{3EF39988-22BB-4F0B-BBBE-64B5A3748AEE}",
    "MultiSelectPicklist": "{E7A81278-8635-4D9E-8D4D-59480B391C5B}",
    "State": "{3EF39988-22BB-4F0B-BBBE-64B5A3748AEE}",
    "Status": "{3EF39988-22BB-4F0B-BBBE-64B5A3748AEE}",
    "Memo": _MULTILINE_CLASSID,
    "Uniqueidentifier": _SINGLE_LINE_CLASSID,
    "EntityName": _SINGLE_LINE_CLASSID,
}

# Dataverse component type code for System Form
_SYSTEM_FORM_COMPONENT_TYPE = 60

_FORM_TYPE_NAMES: dict[int, str] = {
    1: "Dashboard",
    2: "Main",
    3: "Mobile Express",
    4: "Quick View",
    5: "Quick Create",
    6: "Dialog",
    7: "Task Flow Form",
    9: "Card",
    10: "Main Interactive experience",
    100: "Other",
    101: "Main Backup",
}

_SYSTEM_FORM_SELECT = (
    "formid,name,objecttypecode,type,formactivationstate,isdefault,description"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _new_guid() -> str:
    """Return a new uppercase GUID wrapped in braces."""
    return "{" + str(uuid.uuid4()).upper() + "}"


def _resolve_classid(attribute_type: str, format_name_value: str | None) -> str:
    """Pick the correct form control classid for a column type."""
    if attribute_type == "String":
        return _STRING_FORMAT_CLASSIDS.get(format_name_value or "", _SINGLE_LINE_CLASSID)
    return _TYPE_CLASSIDS.get(attribute_type, _SINGLE_LINE_CLASSID)


def _is_multiline(attribute_type: str, format_name_value: str | None) -> bool:
    """Return True for column types that benefit from a multi-row cell (rowspan)."""
    if attribute_type == "Memo":
        return True
    if attribute_type == "String" and format_name_value == "TextArea":
        return True
    return False


# FormXml XSD FormGuidType: braces required — \{[a-fA-F0-9]{8}-...\}
_BRACED_GUID_RE = re.compile(
    r"^\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}$"
)


def _validate_formxml(formxml: str) -> list[str]:
    """Validate a FormXml string against structural rules derived from the FormXml XSD.

    Returns a list of error strings. An empty list means the XML is valid.

    Checks performed (all derived from FormXml.xsd):
    - Well-formed XML (xs:element name="form")
    - Root element is <form>
    - <tabs> is present with at least one <tab> (minOccurs=1 on both)
    - Each <tab> has <columns> with at least one <column> (minOccurs=1)
    - Each <column> has the required 'width' attribute (use="required", FormPercentageType)
    - Every <cell> has an 'id' attribute matching FormGuidType: {GUID} with braces
    - Every <control> within a <cell> has 'classid' matching FormGuidType
    - Every <control> within a <cell> has 'datafieldname' (xs:string attribute)
    - No duplicate 'datafieldname' values within the form
    """
    errors: list[str] = []

    # Well-formed XML
    try:
        root = DET.fromstring(formxml)
    except DefusedXmlException:
        return ["XML contains forbidden constructs (entities/DTD) and was rejected."]
    except ET.ParseError as exc:
        return [f"XML is not well-formed: {exc}"]

    # Root element must be <form>
    if root.tag != "form":
        errors.append(f"Root element must be <form>, found <{root.tag}>.")
        return errors  # no point continuing without a <form>

    # <tabs> is required (minOccurs=1 in FormType)
    tabs = root.find("tabs")
    if tabs is None:
        errors.append("<form> is missing the required <tabs> element.")
    else:
        tab_list = tabs.findall("tab")
        if not tab_list:
            errors.append("<tabs> must contain at least one <tab> (minOccurs=1).")

        for tab_idx, tab in enumerate(tab_list):
            columns = tab.find("columns")
            if columns is None:
                errors.append(f"<tab>[{tab_idx}] is missing the required <columns> element.")
                continue
            col_list = columns.findall("column")
            if not col_list:
                errors.append(
                    f"<tab>[{tab_idx}]/<columns> must contain at least one <column> (minOccurs=1)."
                )
            for col_idx, col in enumerate(col_list):
                # width is use="required" with type FormPercentageType (e.g. "100%")
                if not col.get("width"):
                    errors.append(
                        f"<tab>[{tab_idx}]/<column>[{col_idx}] is missing the required 'width' attribute."
                    )

    # Validate all <cell> and <control> elements throughout the form
    seen_datafields: set[str] = set()
    for cell in root.iter("cell"):
        cell_id = cell.get("id")
        if not cell_id:
            errors.append("<cell> is missing the required 'id' attribute.")
        elif not _BRACED_GUID_RE.match(cell_id):
            errors.append(
                f"<cell id='{cell_id}'> must be a brace-wrapped GUID "
                f"(FormGuidType pattern: {{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}})."
            )

        ctrl = cell.find("control")
        if ctrl is None:
            continue

        # classid: FormGuidType (required for any meaningful control)
        classid = ctrl.get("classid") or ""
        if not classid:
            errors.append(
                f"<control> in <cell id='{cell_id}'> is missing the 'classid' attribute."
            )
        elif not _BRACED_GUID_RE.match(classid):
            errors.append(
                f"<control classid='{classid}'> must be a brace-wrapped GUID "
                f"(FormGuidType pattern: {{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}})."
            )

        # datafieldname: xs:string attribute — must be present and unique
        dfn = ctrl.get("datafieldname") or ""
        if not dfn:
            errors.append(
                f"<control> in <cell id='{cell_id}'> is missing the 'datafieldname' attribute."
            )
        elif dfn in seen_datafields:
            errors.append(
                f"Duplicate datafieldname '{dfn}' — each column may appear on a form only once."
            )
        else:
            seen_datafields.add(dfn)

    return errors


def _extract_label(labels_elem: ET.Element | None, languagecode: str = "1033") -> str | None:
    if labels_elem is None:
        return None
    for lbl in labels_elem.findall("label"):
        if lbl.get("languagecode") == languagecode:
            return lbl.get("description")
    first = labels_elem.find("label")
    return first.get("description") if first is not None else None


def _form_to_structured(root: ET.Element) -> dict:
    """Parse FormXml into a tabs → sections → controls dict.

    Each control entry includes schema-confirmed cell attributes (rowspan,
    colspan, visible, disabled, isrequired) sourced from the FormXml XSD.
    """
    tabs_out = []
    for tab in root.findall("./tabs/tab"):
        tab_label = _extract_label(tab.find("labels"))
        sections_out = []
        for section in tab.findall("./columns/column/sections/section"):
            section_label = _extract_label(section.find("labels"))
            controls_out = []
            for row in section.findall("./rows/row"):
                for cell in row.findall("cell"):
                    ctrl = cell.find("control")
                    if ctrl is not None:
                        entry: dict = {
                            "datafieldname": ctrl.get("datafieldname"),
                            "label": _extract_label(cell.find("labels")),
                            "classid": ctrl.get("classid"),
                            "control_id": ctrl.get("id"),
                            "cell_id": cell.get("id"),
                        }
                        # Include optional cell/control attributes from the XSD
                        # when present so callers can see the full layout state.
                        for attr, key in (
                            ("rowspan", "rowspan"),
                            ("colspan", "colspan"),
                            ("visible", "visible"),
                        ):
                            val = cell.get(attr)
                            if val is not None:
                                entry[key] = val
                        for attr, key in (
                            ("disabled", "disabled"),
                            ("isrequired", "isrequired"),
                        ):
                            val = ctrl.get(attr)
                            if val is not None:
                                entry[key] = val
                        controls_out.append(entry)
            sections_out.append({
                "id": section.get("id"),
                "label": section_label,
                "controls": controls_out,
            })
        tabs_out.append({
            "id": tab.get("id"),
            "label": tab_label,
            "sections": sections_out,
        })
    return {"tabs": tabs_out}


def _find_sections(root: ET.Element) -> list[ET.Element]:
    """Return all section elements in document order."""
    return root.findall("./tabs/tab/columns/column/sections/section")


def _find_control_row(root: ET.Element, datafieldname: str) -> tuple[ET.Element | None, ET.Element | None]:
    """Find (rows_container, row) that contains a control with the given datafieldname."""
    for section in _find_sections(root):
        rows_elem = section.find("rows")
        if rows_elem is None:
            continue
        for row in rows_elem.findall("row"):
            for cell in row.findall("cell"):
                ctrl = cell.find("control")
                if ctrl is not None and ctrl.get("datafieldname") == datafieldname:
                    return rows_elem, row
    return None, None


def _control_exists(root: ET.Element, datafieldname: str) -> bool:
    _, row = _find_control_row(root, datafieldname)
    return row is not None


def _build_control_row(
    datafieldname: str,
    label: str,
    classid: str,
    *,
    rowspan: int | None = None,
    disabled: bool = False,
    isrequired: bool = False,
) -> ET.Element:
    """Build a <row><cell>…</cell></row> element for a new control.

    rowspan: set on the <cell> element; controls vertical height. The FormXml
      XSD defines this as xs:nonNegativeInteger on FormXmlCellCommon. Use 3+
      for Memo/TextArea controls to give them usable height in the form.
    disabled: maps to the 'disabled' attribute on <control> (xs:boolean in XSD).
    isrequired: maps to 'isrequired' on <control> (xs:boolean). Distinct from the
      column RequiredLevel — this controls the form-level required indicator.
    """
    row = ET.Element("row")
    cell_attrs: dict[str, str] = {"id": _new_guid()}
    if rowspan is not None and rowspan > 1:
        cell_attrs["rowspan"] = str(rowspan)
    cell = ET.SubElement(row, "cell", cell_attrs)
    labels = ET.SubElement(cell, "labels")
    ET.SubElement(labels, "label", {"description": label, "languagecode": "1033"})
    ctrl_attrs: dict[str, str] = {
        "id": datafieldname,
        "classid": classid,
        "datafieldname": datafieldname,
    }
    if disabled:
        ctrl_attrs["disabled"] = "true"
    if isrequired:
        ctrl_attrs["isrequired"] = "true"
    ET.SubElement(cell, "control", ctrl_attrs)
    return row


def _insert_row_into_section(
    section: ET.Element,
    row: ET.Element,
    row_index: int | None,
) -> None:
    rows_elem = section.find("rows")
    if rows_elem is None:
        rows_elem = ET.SubElement(section, "rows")
    existing = list(rows_elem)
    if row_index is None or row_index >= len(existing):
        rows_elem.append(row)
    else:
        rows_elem.insert(row_index, row)


async def _fetch_formxml(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    form_id: str,
) -> dict | None:
    """Return the full systemform record dict, or None if not found."""
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/systemforms({form_id})"
        f"?$select={_SYSTEM_FORM_SELECT},formxml"
    )
    resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def _patch_formxml(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    form_id: str,
    formxml: str,
) -> None:
    """Validate then PATCH the formxml field on a systemform record.

    Raises ValueError with a description of all errors if validation fails,
    so the caller never sends malformed XML to Dataverse.
    """
    errors = _validate_formxml(formxml)
    if errors:
        raise ValueError(
            f"FormXml validation failed — PATCH aborted to protect the form. "
            f"{len(errors)} error(s): " + "; ".join(errors)
        )
    patch_headers = {**headers, "Content-Type": "application/json"}
    resp = await request_with_retry(app_ctx.http_client, "PATCH",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/systemforms({form_id})",
        json={"formxml": formxml},
        headers=patch_headers,
    )
    resp.raise_for_status()


async def _add_form_to_solution(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    form_id: str,
    solution_unique_name: str,
) -> None:
    """Call AddSolutionComponent to include the form (component type 60) in a solution."""
    body = {
        "ComponentId": form_id,
        "ComponentType": _SYSTEM_FORM_COMPONENT_TYPE,
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


async def _publish_table(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    table_logical_name: str,
) -> None:
    """Publish customizations for the given table."""
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


async def _resolve_column_info(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    table_logical_name: str,
    datafieldname: str,
) -> dict | None:
    """Return {attribute_type, display_name, format_name_value} for a column, or None."""
    table_enc = _url_quote(table_logical_name, safe="")
    field_filter = f"LogicalName eq '{odata_quote(datafieldname)}'"

    # Basic metadata (works for all types)
    query = urlencode(
        {"$filter": field_filter, "$select": "LogicalName,AttributeType,DisplayName"},
        safe="$,",
    )
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
        f"EntityDefinitions(LogicalName='{table_enc}')/Attributes?{query}"
    )
    resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
    resp.raise_for_status()
    items = resp.json().get("value", [])
    if not items:
        return None

    col = items[0]
    attribute_type: str = col.get("AttributeType", "String")
    display_name_obj = col.get("DisplayName") or {}
    localized = display_name_obj.get("LocalizedLabels") or []
    display_name = localized[0].get("Label") if localized else datafieldname

    format_name_value: str | None = None
    if attribute_type == "String":
        # Hit the cast endpoint to get FormatName
        cast_query = urlencode(
            {"$filter": field_filter, "$select": "LogicalName,FormatName"},
            safe="$,",
        )
        cast_url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
            f"EntityDefinitions(LogicalName='{table_enc}')"
            f"/Attributes/Microsoft.Dynamics.CRM.StringAttributeMetadata?{cast_query}"
        )
        try:
            cast_resp = await request_with_retry(app_ctx.http_client, "GET", cast_url, headers=headers)
            cast_resp.raise_for_status()
            cast_items = cast_resp.json().get("value", [])
            if cast_items:
                fn = cast_items[0].get("FormatName")
                if isinstance(fn, dict):
                    format_name_value = fn.get("Value")
        except httpx.HTTPStatusError as e:
            logger.debug(
                "Could not resolve FormatName for %s.%s (HTTP %d); continuing without it",
                table_logical_name, datafieldname, e.response.status_code,
            )

    return {
        "attribute_type": attribute_type,
        "display_name": display_name,
        "format_name_value": format_name_value,
    }


# ---------------------------------------------------------------------------
# Tool: dataverse_list_forms
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_list_forms",
    annotations={
        "title": "List Forms",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_forms(params: ListFormsInput, ctx: Context) -> str:
    """List model-driven app forms for a Dataverse table.
    Returns form metadata: formid, name, type, activation state, and whether
    each is the default form. Filter by table_logical_name and/or form_type.

    Common form types: 2 = Main, 5 = Quick Create, 4 = Quick View, 9 = Card.

    Use dataverse_get_form to inspect the full layout of a specific form.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    filters: list[str] = []
    if params.table_logical_name:
        filters.append(f"objecttypecode eq '{odata_quote(params.table_logical_name)}'")
    if params.form_type is not None:
        filters.append(f"type eq {params.form_type}")

    query: dict[str, str] = {
        "$select": _SYSTEM_FORM_SELECT,
        "$top": str(params.top),
    }
    if filters:
        query["$filter"] = " and ".join(filters)

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/systemforms?{urlencode(query, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        forms = [
            {
                "form_id": r.get("formid"),
                "name": r.get("name"),
                "table": r.get("objecttypecode"),
                "type": r.get("type"),
                "type_name": _FORM_TYPE_NAMES.get(r.get("type", 0), "Unknown"),
                "is_default": r.get("isdefault"),
                "activation_state": r.get("formactivationstate"),
                "description": r.get("description"),
            }
            for r in records
        ]
        return finalize_response({
            "forms": forms,
            "count": len(forms),
            "has_more": len(records) >= params.top,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_list_forms")


# ---------------------------------------------------------------------------
# Tool: dataverse_get_form
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_get_form",
    annotations={
        "title": "Get Form",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_form(params: GetFormInput, ctx: Context) -> str:
    """Get a Dataverse form's layout as a structured JSON object.
    Parses the raw FormXml into a readable tabs → sections → controls tree
    so agents don't need to work with raw XML.

    Also returns formxml_backup (the raw XML string) for reference.
    Use dataverse_list_forms to discover form IDs.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        record = await _fetch_formxml(app_ctx, base_url, headers, params.form_id)
        if record is None:
            return json.dumps({"error": True, "message": f"Form '{params.form_id}' not found."})

        formxml = record.get("formxml") or ""
        try:
            root = ET.fromstring(formxml)
            layout = _form_to_structured(root)
        except ET.ParseError as exc:
            return json.dumps({"error": True, "message": f"Could not parse FormXml: {exc}"})

        form_type = record.get("type", 0)
        return finalize_response({
            "form_id": record.get("formid"),
            "name": record.get("name"),
            "table": record.get("objecttypecode"),
            "type": form_type,
            "type_name": _FORM_TYPE_NAMES.get(form_type, "Unknown"),
            "is_default": record.get("isdefault"),
            "activation_state": record.get("formactivationstate"),
            "layout": layout,
            "formxml_backup": formxml,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_get_form")


# ---------------------------------------------------------------------------
# Tool: dataverse_add_form_control
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_add_form_control",
    annotations={
        "title": "Add Form Control",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_add_form_control(params: AddFormControlInput, ctx: Context) -> str:
    """Add a column control to a Dataverse model-driven app form.
    Resolves the correct control classid from the column's metadata automatically —
    supply only the column logical name.

    Memo and TextArea columns automatically receive rowspan=3 so they render
    with usable height. Override with rowspan to change this.

    The control is inserted into the specified section (0-based section_index within
    the first tab, default 0). row_index controls the position; omit to append.

    disabled=True sets the control read-only on the form (maps to the 'disabled'
    attribute on <control> per the FormXml XSD). isrequired=True shows the required
    indicator on the form (maps to 'isrequired' on <control>; distinct from the
    column's RequiredLevel metadata).

    Always publishes after saving — Dataverse requires PublishXml for formxml
    changes to take effect (a bare PATCH writes to an unpublished staging layer
    that is invisible to subsequent reads until published).
    Use dataverse_get_form first to see the current layout and section indices.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # 1. Resolve column metadata
        col_info = await _resolve_column_info(
            app_ctx, base_url, headers, params.table_logical_name, params.datafieldname
        )
        if col_info is None:
            return json.dumps({
                "error": True,
                "message": (
                    f"Column '{params.datafieldname}' not found on table "
                    f"'{params.table_logical_name}'."
                ),
            })

        classid = _resolve_classid(col_info["attribute_type"], col_info["format_name_value"])
        label = params.label or col_info["display_name"]

        # Auto-rowspan: multiline text controls need vertical space to be usable.
        # Default to 3 rows unless the caller overrides.
        effective_rowspan: int | None = params.rowspan
        if effective_rowspan is None and _is_multiline(
            col_info["attribute_type"], col_info["format_name_value"]
        ):
            effective_rowspan = 3

        # 2. Fetch form
        record = await _fetch_formxml(app_ctx, base_url, headers, params.form_id)
        if record is None:
            return json.dumps({"error": True, "message": f"Form '{params.form_id}' not found."})

        formxml = record.get("formxml") or ""
        table_logical_name = record.get("objecttypecode", params.table_logical_name)

        # 3. Parse XML
        try:
            root = ET.fromstring(formxml)
        except ET.ParseError as exc:
            return json.dumps({"error": True, "message": f"Could not parse FormXml: {exc}"})

        # 4. Duplicate check
        if _control_exists(root, params.datafieldname):
            return json.dumps({
                "error": True,
                "message": (
                    f"Control for '{params.datafieldname}' already exists on form "
                    f"'{params.form_id}'. Remove it first or use a different form."
                ),
            })

        # 5. Find target section
        sections = _find_sections(root)
        if not sections:
            return json.dumps({"error": True, "message": "Form has no sections to add controls to."})

        section_idx = params.section_index or 0
        if section_idx >= len(sections):
            return json.dumps({
                "error": True,
                "message": (
                    f"section_index {section_idx} is out of range — "
                    f"form has {len(sections)} section(s) (0-based)."
                ),
            })

        section = sections[section_idx]

        # 6. Build and insert the row
        new_row = _build_control_row(
            params.datafieldname,
            label,
            classid,
            rowspan=effective_rowspan,
            disabled=params.disabled,
            isrequired=params.isrequired,
        )
        _insert_row_into_section(section, new_row, params.row_index)

        # 7. Serialize and PATCH
        new_formxml = ET.tostring(root, encoding="unicode")
        await _patch_formxml(app_ctx, base_url, headers, params.form_id, new_formxml)
        logger.info("Added control '%s' to form %s", params.datafieldname, params.form_id)

        # 8. Optional: add form to solution
        if params.solution_unique_name:
            try:
                await _add_form_to_solution(
                    app_ctx, base_url, headers, params.form_id, params.solution_unique_name
                )
                logger.info("Added form %s to solution %s", params.form_id, params.solution_unique_name)
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Could not add form to solution %s: %d %s",
                    params.solution_unique_name, e.response.status_code, e.response.text,
                )

        # 9. Publish — required for formxml changes to be visible via the API
        try:
            await _publish_table(app_ctx, base_url, headers, table_logical_name)
            logger.info("Published customizations for %s", table_logical_name)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        return finalize_response({
            "added": True,
            "form_id": params.form_id,
            "datafieldname": params.datafieldname,
            "label": label,
            "classid": classid,
            "attribute_type": col_info["attribute_type"],
            "rowspan": effective_rowspan,
            "disabled": params.disabled,
            "isrequired": params.isrequired,
            "section_index": section_idx,
            "row_index": params.row_index,
            "published": True,
            "solution_unique_name": params.solution_unique_name,
            "formxml_backup": formxml,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_add_form_control")


# ---------------------------------------------------------------------------
# Tool: dataverse_validate_formxml
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_validate_formxml",
    annotations={
        "title": "Validate Form XML",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_validate_formxml(params: ValidateFormInput, ctx: Context) -> str:
    """Validate FormXml against structural rules from the FormXml XSD.

    Two modes:
    - Dry-run (formxml provided): validates the given XML string directly without
      fetching from Dataverse. Use this before calling dataverse_set_formxml to
      catch structural errors before committing.
    - Live (formxml omitted): fetches the current formxml for form_id and validates it.

    Checks performed:
    - XML is well-formed
    - <form> root with required <tabs> / <tab> / <columns> / <column> hierarchy
    - Each <column> has the required 'width' attribute
    - Every <cell> id is a brace-wrapped GUID (FormGuidType: {xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx})
    - Every <control> has a brace-wrapped GUID classid and a datafieldname
    - No duplicate datafieldname values within the form

    Returns valid=true and the control list on success, or valid=false with a full
    error list. Note: all write tools run this validation automatically before every PATCH.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        # Dry-run path: validate the provided XML without hitting Dataverse
        if params.formxml is not None:
            errors = _validate_formxml(params.formxml)
            if errors:
                return json.dumps({
                    "valid": False,
                    "form_id": params.form_id,
                    "error_count": len(errors),
                    "errors": errors,
                })
            root = ET.fromstring(params.formxml)
            controls = [
                ctrl.get("datafieldname")
                for ctrl in root.iter("control")
                if ctrl.get("datafieldname")
            ]
            return json.dumps({
                "valid": True,
                "form_id": params.form_id,
                "control_count": len(controls),
                "controls": controls,
            })

        # Live path: fetch and validate the stored formxml
        headers = await build_headers(app_ctx, base_url)
        record = await _fetch_formxml(app_ctx, base_url, headers, params.form_id)
        if record is None:
            return json.dumps({"error": True, "message": f"Form '{params.form_id}' not found."})

        formxml = record.get("formxml") or ""
        errors = _validate_formxml(formxml)

        if errors:
            return json.dumps({
                "valid": False,
                "form_id": params.form_id,
                "form_name": record.get("name"),
                "table": record.get("objecttypecode"),
                "error_count": len(errors),
                "errors": errors,
            })

        root = ET.fromstring(formxml)
        controls = [
            ctrl.get("datafieldname")
            for ctrl in root.iter("control")
            if ctrl.get("datafieldname")
        ]
        return json.dumps({
            "valid": True,
            "form_id": params.form_id,
            "form_name": record.get("name"),
            "table": record.get("objecttypecode"),
            "control_count": len(controls),
            "controls": controls,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_validate_formxml")


# ---------------------------------------------------------------------------
# Tool: dataverse_remove_form_control
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_remove_form_control",
    annotations={
        "title": "Remove Form Control",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_remove_form_control(params: RemoveFormControlInput, ctx: Context) -> str:
    """Remove a column control from a Dataverse model-driven app form.
    Finds the control by its datafieldname and removes the entire row it sits in.
    Returns an error if the control is not found (safe — no change is made).

    Always publishes after saving — Dataverse requires PublishXml for formxml
    changes to take effect (a bare PATCH writes to an unpublished staging layer
    that is invisible to subsequent reads until published).
    The original FormXml is returned in formxml_backup for rollback if needed.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # 1. Fetch form
        record = await _fetch_formxml(app_ctx, base_url, headers, params.form_id)
        if record is None:
            return json.dumps({"error": True, "message": f"Form '{params.form_id}' not found."})

        formxml = record.get("formxml") or ""
        table_logical_name = record.get("objecttypecode", "")

        # 2. Parse XML
        try:
            root = ET.fromstring(formxml)
        except ET.ParseError as exc:
            return json.dumps({"error": True, "message": f"Could not parse FormXml: {exc}"})

        # 3. Find and remove the row
        rows_elem, row = _find_control_row(root, params.datafieldname)
        if row is None:
            return json.dumps({
                "error": True,
                "message": f"Control for '{params.datafieldname}' not found on form '{params.form_id}'.",
            })

        rows_elem.remove(row)

        # 4. Serialize and PATCH
        new_formxml = ET.tostring(root, encoding="unicode")
        await _patch_formxml(app_ctx, base_url, headers, params.form_id, new_formxml)
        logger.info("Removed control '%s' from form %s", params.datafieldname, params.form_id)

        # 5. Optional: add form to solution
        if params.solution_unique_name:
            try:
                await _add_form_to_solution(
                    app_ctx, base_url, headers, params.form_id, params.solution_unique_name
                )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Could not add form to solution %s: %d %s",
                    params.solution_unique_name, e.response.status_code, e.response.text,
                )

        # 6. Publish — required for formxml changes to be visible via the API
        try:
            await _publish_table(app_ctx, base_url, headers, table_logical_name)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        return finalize_response({
            "removed": True,
            "form_id": params.form_id,
            "datafieldname": params.datafieldname,
            "published": True,
            "solution_unique_name": params.solution_unique_name,
            "formxml_backup": formxml,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_remove_form_control")


# ---------------------------------------------------------------------------
# Tool: dataverse_set_formxml
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_set_formxml",
    annotations={
        "title": "Set Form XML",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_set_formxml(params: SetFormXmlInput, ctx: Context) -> str:
    """Replace a Dataverse form's FormXml with a complete new XML string, then publish.

    Handles form redesign scenarios that add_form_control / remove_form_control cannot:
    adding or removing tabs and sections, reordering controls across sections,
    setting section labels and visibility, and setting column widths.

    Steps performed:
    1. Fetches the current formxml and saves it as formxml_backup in the response.
    2. Validates the incoming formxml against FormXml XSD structural rules.
       Returns validation errors without writing if the XML is invalid.
    3. PATCHes systemforms({form_id}) with {"formxml": <new xml>}.
    4. Publishes customizations for the form's table via PublishXml.

    Use dataverse_get_form to retrieve the current FormXml as a starting point, and
    dataverse_validate_formxml with the formxml parameter as an explicit dry-run
    before calling this tool. formxml_backup in the response can be used to revert
    by calling this tool again with the backup value if the result looks wrong.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # 1. Fetch current form (backup + table name for publish)
        record = await _fetch_formxml(app_ctx, base_url, headers, params.form_id)
        if record is None:
            return json.dumps({"error": True, "message": f"Form '{params.form_id}' not found."})

        formxml_backup = record.get("formxml") or ""
        table_logical_name = record.get("objecttypecode", "")

        # 2. Validate + PATCH (_patch_formxml raises ValueError on invalid XML)
        try:
            await _patch_formxml(app_ctx, base_url, headers, params.form_id, params.formxml)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)})

        logger.info("Set formxml for form %s", params.form_id)

        # 3. Optional: add form to solution
        if params.solution_unique_name:
            try:
                await _add_form_to_solution(
                    app_ctx, base_url, headers, params.form_id, params.solution_unique_name
                )
                logger.info("Added form %s to solution %s", params.form_id, params.solution_unique_name)
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Could not add form to solution %s: %d %s",
                    params.solution_unique_name, e.response.status_code, e.response.text,
                )

        # 4. Publish
        try:
            await _publish_table(app_ctx, base_url, headers, table_logical_name)
            logger.info("Published customizations for %s", table_logical_name)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        return finalize_response({
            "updated": True,
            "published": True,
            "form_id": params.form_id,
            "table": table_logical_name,
            "solution_unique_name": params.solution_unique_name,
            "formxml_backup": formxml_backup,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_set_formxml")
