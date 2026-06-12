"""Model-driven app (AppModule) tools for the Dataverse MCP server.

Provides tools to list, inspect, create, and manage Dataverse model-driven apps
including sitemap generation, component management, validation, and publishing.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote as _url_quote

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import mcp, write_tool
from dataverse_mcp.client import (
    AppContext,
    _DATAVERSE_API_VERSION,
    build_headers,
    finalize_response,
    get_app_ctx,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import (
    AddAppComponentsInput,
    AssignAppRoleInput,
    CreateAppInput,
    GetAppInput,
    ListAppsInput,
    PublishAppInput,
    RemoveAppComponentsInput,
    SetAppSitemapInput,
    UpdateAppInput,
    ValidateAppInput,
)

logger = logging.getLogger(__name__)

_DEFAULT_APP_ICON_WEBRESOURCE = "953b9fac-1e5e-e611-80d6-00155ded156f"

_COMPONENT_TYPE_NAMES: dict[int, str] = {
    1: "Entity",
    2: "Attribute",
    26: "View",
    29: "Workflow / BPF",
    59: "Chart",
    60: "Form / Dashboard",
    62: "Sitemap",
    80: "App Module",
}

# Maps component type name → (odata_type, id_field_name)
_COMPONENT_ODATA_TYPES: dict[str, tuple[str, str]] = {
    "form":    ("Microsoft.Dynamics.CRM.systemform",              "formid"),
    "view":    ("Microsoft.Dynamics.CRM.savedquery",              "savedqueryid"),
    "chart":   ("Microsoft.Dynamics.CRM.savedqueryvisualization", "savedqueryvisualizationid"),
    "bpf":     ("Microsoft.Dynamics.CRM.workflow",                "workflowid"),
    "sitemap": ("Microsoft.Dynamics.CRM.sitemap",                 "sitemapid"),
}

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

_APP_SELECT = (
    "appmoduleid,appmoduleidunique,name,uniquename,description,publishedon,statecode,clienttype"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sanitize_id(s: str) -> str:
    """Convert a string to a valid XML ID: letters/digits/underscores, starts with a letter."""
    s = re.sub(r"[^a-zA-Z0-9_]", "_", s)
    if not s or not s[0].isalpha():
        s = "id_" + s
    return s or "id_item"


def _extract_guid_from_header(entity_id_header: str) -> str | None:
    m = _GUID_RE.search(entity_id_header)
    return m.group(0) if m else None


def _build_sitemap_xml(areas: list[dict]) -> str:
    """Build <SiteMap> XML from a structured areas/groups/subareas list."""
    root = ET.Element("SiteMap")
    for area in areas:
        area_title = area.get("title", "Main")
        area_id = area.get("id") or _sanitize_id(f"area_{area_title}")
        area_elem = ET.SubElement(root, "Area", {"Id": area_id, "Title": area_title})
        for group in area.get("groups", []):
            group_title = group.get("title", "Workspace")
            group_id = group.get("id") or _sanitize_id(f"group_{group_title}")
            group_elem = ET.SubElement(area_elem, "Group", {"Id": group_id, "Title": group_title})
            for subarea in group.get("subareas", []):
                entity = subarea.get("entity", "")
                url = subarea.get("url", "")
                title = subarea.get("title", "")
                sub_id = subarea.get("id") or _sanitize_id(
                    f"subarea_{entity or title or 'item'}"
                )
                attrs: dict[str, str] = {"Id": sub_id}
                if entity:
                    attrs["Entity"] = entity
                if url:
                    attrs["Url"] = url
                if title:
                    attrs["Title"] = title
                ET.SubElement(group_elem, "SubArea", attrs)
    return ET.tostring(root, encoding="unicode")


def _tables_to_areas(tables: list[str], area_title: str, group_title: str) -> list[dict]:
    return [{
        "title": area_title,
        "groups": [{
            "title": group_title,
            "subareas": [{"entity": t} for t in tables],
        }],
    }]


def _validate_sitemap_xml(xml: str) -> list[str]:
    """Validate SiteMap XML structure. Returns a list of error strings; empty = valid."""
    errors: list[str] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"XML is not well-formed: {exc}"]

    if root.tag != "SiteMap":
        errors.append(f"Root element must be <SiteMap>, found <{root.tag}>.")
        return errors

    areas = root.findall("Area")
    if not areas:
        errors.append("<SiteMap> must contain at least one <Area>.")

    seen_ids: set[str] = set()
    for area in areas:
        area_id = area.get("Id") or ""
        if not area_id:
            errors.append("<Area> is missing the required 'Id' attribute.")
        elif area_id in seen_ids:
            errors.append(f"Duplicate Id '{area_id}'.")
        else:
            seen_ids.add(area_id)

        groups = area.findall("Group")
        if not groups:
            errors.append(f"<Area Id='{area_id}'> must contain at least one <Group>.")
        for group in groups:
            group_id = group.get("Id") or ""
            if not group_id:
                errors.append("<Group> is missing the required 'Id' attribute.")
            elif group_id in seen_ids:
                errors.append(f"Duplicate Id '{group_id}'.")
            else:
                seen_ids.add(group_id)

            subareas = group.findall("SubArea")
            if not subareas:
                errors.append(f"<Group Id='{group_id}'> must contain at least one <SubArea>.")
            for subarea in subareas:
                sub_id = subarea.get("Id") or ""
                if not sub_id:
                    errors.append("<SubArea> is missing the required 'Id' attribute.")
                elif sub_id in seen_ids:
                    errors.append(f"Duplicate Id '{sub_id}'.")
                else:
                    seen_ids.add(sub_id)
                if not subarea.get("Entity") and not subarea.get("Url"):
                    errors.append(
                        f"<SubArea Id='{sub_id}'> must specify either 'Entity' or 'Url'."
                    )
    return errors


async def _resolve_entity_metadata_id(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    logical_name: str,
) -> str | None:
    enc = _url_quote(logical_name, safe="")
    resp = await request_with_retry(app_ctx.http_client, "GET",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/EntityDefinitions(LogicalName='{enc}')?$select=MetadataId",
        headers=headers,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json().get("MetadataId")


async def _build_component_ref(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    comp_type: str,
    comp_id: str | None,
    logical_name: str | None,
) -> dict | str:
    """Resolve a component spec into a Web API @odata.type ref dict, or an error string."""
    t = comp_type.lower()
    if t == "table":
        if not logical_name:
            return "Component type 'table' requires logical_name."
        meta_id = await _resolve_entity_metadata_id(app_ctx, base_url, headers, logical_name)
        if meta_id is None:
            return f"Table '{logical_name}' not found in EntityDefinitions."
        return {"entityid": meta_id, "@odata.type": "Microsoft.Dynamics.CRM.entity"}
    info = _COMPONENT_ODATA_TYPES.get(t)
    if info is None:
        return (
            f"Unknown component type '{comp_type}'. "
            f"Valid: table, {', '.join(_COMPONENT_ODATA_TYPES)}."
        )
    odata_type, id_key = info
    return {id_key: comp_id, "@odata.type": odata_type}


async def _call_add_app_components(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    app_id: str,
    components: list[dict],
) -> None:
    resp = await request_with_retry(app_ctx.http_client, "POST",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/AddAppComponents",
        json={"AppId": app_id, "Components": components},
        headers={**headers, "Content-Type": "application/json"},
    )
    resp.raise_for_status()


async def _call_remove_app_components(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    app_id: str,
    components: list[dict],
) -> None:
    resp = await request_with_retry(app_ctx.http_client, "POST",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/RemoveAppComponents",
        json={"AppId": app_id, "Components": components},
        headers={**headers, "Content-Type": "application/json"},
    )
    resp.raise_for_status()


async def _publish_app(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    app_id: str,
) -> None:
    xml = (
        f"<importexportxml>"
        f"<appmodules><appmodule>{app_id}</appmodule></appmodules>"
        f"</importexportxml>"
    )
    resp = await request_with_retry(app_ctx.http_client, "POST",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/PublishXml",
        json={"ParameterXml": xml},
        headers={**headers, "Content-Type": "application/json"},
    )
    resp.raise_for_status()


async def _run_validate_app(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    app_id: str,
) -> dict:
    resp = await request_with_retry(app_ctx.http_client, "GET",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/ValidateApp(AppModuleId={app_id})",
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json().get("AppValidationResponse", resp.json())


async def _upsert_sitemap(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    *,
    sitemap_xml: str,
    sitemap_unique_name: str,
    existing_sitemap_id: str | None,
) -> str:
    """Create (POST) or update (PATCH) a sitemap record. Returns sitemapid."""
    body: dict = {"sitemapxml": sitemap_xml}
    if existing_sitemap_id:
        resp = await request_with_retry(app_ctx.http_client, "PATCH",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/sitemaps({existing_sitemap_id})",
            json=body,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return existing_sitemap_id

    body["sitemapnameunique"] = sitemap_unique_name
    resp = await request_with_retry(app_ctx.http_client, "POST",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/sitemaps",
        json=body,
        headers={**headers, "Content-Type": "application/json"},
    )
    resp.raise_for_status()
    entity_id = resp.headers.get("OData-EntityId", "")
    sitemap_id = _extract_guid_from_header(entity_id)
    if not sitemap_id:
        raise ValueError(
            f"Could not extract sitemapid from OData-EntityId header: {entity_id!r}"
        )
    return sitemap_id


async def _fetch_app_sitemap(
    app_ctx: AppContext,
    base_url: str,
    headers: dict,
    app_id: str,
) -> tuple[str | None, str | None]:
    """Return (sitemap_id, sitemapxml) for the app's current sitemap, or (None, None)."""
    resp = await request_with_retry(app_ctx.http_client, "GET",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveAppComponents(AppModuleId={app_id})",
        headers=headers,
    )
    resp.raise_for_status()
    # RetrieveAppComponents returns appmodulecomponent records in "value"
    components = resp.json().get("value", [])
    # objectid is a lookup attribute, so the Web API returns it as _objectid_value
    sitemap_id = next(
        (
            c.get("objectid") or c.get("_objectid_value")
            for c in components
            if c.get("componenttype") == 62
        ),
        None,
    )
    if not sitemap_id:
        return None, None

    sm_resp = await request_with_retry(app_ctx.http_client, "GET",
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/sitemaps({sitemap_id})?$select=sitemapxml",
        headers=headers,
    )
    if sm_resp.status_code == 404:
        return sitemap_id, None
    sm_resp.raise_for_status()
    return sitemap_id, sm_resp.json().get("sitemapxml")


def _summarise_validation(vr: dict) -> dict:
    issues = vr.get("ValidationIssueList", [])
    errors = [i for i in issues if i.get("ErrorType") == "Error"]
    warnings = [i for i in issues if i.get("ErrorType") == "Warning"]
    return {
        "validation_success": vr.get("ValidationSuccess", False),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": [{"message": i.get("Message"), "code": i.get("CRMErrorCode")} for i in errors],
        "warnings": [{"message": i.get("Message"), "code": i.get("CRMErrorCode")} for i in warnings],
    }


# ---------------------------------------------------------------------------
# Tool: dataverse_list_apps
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_list_apps",
    annotations={
        "title": "List Model-Driven Apps",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_apps(params: ListAppsInput, ctx: Context) -> str:
    """List model-driven apps (AppModule records) in a Dataverse environment.
    Returns appmoduleid, name, uniquename, description, publish state, and statecode.
    Set include_unpublished=true to also return draft apps not yet published.
    Use dataverse_get_app to fetch a single app's components.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        if params.include_unpublished:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/appmodules/Microsoft.Dynamics.CRM.RetrieveUnpublishedMultiple()"
                f"?$select={_APP_SELECT}"
            )
        else:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/appmodules?$select={_APP_SELECT}"
            )
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        records = resp.json().get("value", [])
        apps = [
            {
                "app_id": r.get("appmoduleid"),
                "name": r.get("name"),
                "unique_name": r.get("uniquename"),
                "description": r.get("description"),
                "is_published": r.get("publishedon") is not None,
                "state": r.get("statecode"),
                "client_type": r.get("clienttype"),
            }
            for r in records
        ]
        return finalize_response({"apps": apps, "count": len(apps)})
    except Exception as e:
        return tool_error_response(e, "dataverse_list_apps")


# ---------------------------------------------------------------------------
# Tool: dataverse_get_app
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_get_app",
    annotations={
        "title": "Get Model-Driven App",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_app(params: GetAppInput, ctx: Context) -> str:
    """Get a model-driven app's properties and its current components.
    Returns app metadata and a grouped component list (entities, forms, views,
    sitemap, etc.) via RetrieveAppComponents. Use dataverse_list_apps to find app IDs.
    Always call this before any write to confirm current component state.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        app_resp = await request_with_retry(app_ctx.http_client, "GET",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/appmodules({params.app_id})?$select={_APP_SELECT}",
            headers=headers,
        )
        if app_resp.status_code == 404:
            return json.dumps({"error": True, "message": f"App '{params.app_id}' not found."})
        app_resp.raise_for_status()
        app_data = app_resp.json()

        comp_resp = await request_with_retry(app_ctx.http_client, "GET",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/RetrieveAppComponents(AppModuleId={params.app_id})",
            headers=headers,
        )
        comp_resp.raise_for_status()
        # RetrieveAppComponents returns appmodulecomponent records in "value"
        raw_components = comp_resp.json().get("value", [])

        grouped: dict[str, list] = {}
        for c in raw_components:
            ct = c.get("componenttype", 0)
            label = _COMPONENT_TYPE_NAMES.get(ct, f"Type {ct}")
            grouped.setdefault(label, []).append({
                "object_id": c.get("objectid") or c.get("_objectid_value"),
                "component_type": ct,
                "root_component_behavior": c.get("rootcomponentbehavior"),
            })

        return finalize_response({
            "app_id": app_data.get("appmoduleid"),
            "app_id_unique": app_data.get("appmoduleidunique"),
            "name": app_data.get("name"),
            "unique_name": app_data.get("uniquename"),
            "description": app_data.get("description"),
            "is_published": app_data.get("publishedon") is not None,
            "state": app_data.get("statecode"),
            "client_type": app_data.get("clienttype"),
            "components": grouped,
            "component_count": len(raw_components),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_get_app")


# ---------------------------------------------------------------------------
# Tool: dataverse_validate_app
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_validate_app",
    annotations={
        "title": "Validate Model-Driven App",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_validate_app(params: ValidateAppInput, ctx: Context) -> str:
    """Validate a model-driven app using the ValidateApp function.
    Checks for required components (sitemap, etc.) and returns all errors and warnings.
    An app with validation errors cannot be published. Always run this before publishing.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        vr = await _run_validate_app(app_ctx, base_url, headers, params.app_id)
        return finalize_response({"app_id": params.app_id, **_summarise_validation(vr)})
    except Exception as e:
        return tool_error_response(e, "dataverse_validate_app")


# ---------------------------------------------------------------------------
# Tool: dataverse_create_app
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_app",
    annotations={
        "title": "Create Model-Driven App",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_app(params: CreateAppInput, ctx: Context) -> str:
    """Create a new model-driven app (AppModule) in a Dataverse environment.
    When tables is provided, auto-generates a sitemap and adds the entity components.
    Validates and publishes by default (set validate=false or publish=false to skip).
    The returned unique_name includes the publisher prefix added by Dataverse.
    Always provide tables — apps without a sitemap fail validation and cannot publish.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # 1. Create the AppModule record
        create_body: dict = {
            "name": params.name,
            "uniquename": params.unique_name,
            "webresourceid": _DEFAULT_APP_ICON_WEBRESOURCE,
        }
        if params.description:
            create_body["description"] = params.description

        create_resp = await request_with_retry(app_ctx.http_client, "POST",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/appmodules",
            json=create_body,
            headers={**headers, "Content-Type": "application/json"},
        )
        create_resp.raise_for_status()

        entity_id_header = create_resp.headers.get("OData-EntityId", "")
        app_id = _extract_guid_from_header(entity_id_header)
        if not app_id:
            return json.dumps({
                "error": True,
                "message": (
                    f"App created but could not parse appmoduleid from "
                    f"OData-EntityId header: {entity_id_header!r}"
                ),
            })
        logger.info("Created app '%s' id=%s", params.name, app_id)

        sitemap_id: str | None = None
        components_added: list[str] = []
        failed_tables: list[str] = []

        # 2. Auto-generate sitemap + add entity components
        if params.tables:
            areas = _tables_to_areas(params.tables, "Main", "Workspace")
            sitemap_xml = _build_sitemap_xml(areas)

            xml_errors = _validate_sitemap_xml(sitemap_xml)
            if xml_errors:
                return json.dumps({
                    "error": True,
                    "app_id": app_id,
                    "message": f"Generated sitemap XML is invalid: {'; '.join(xml_errors)}",
                })

            # sitemapnameunique allows only letters and numbers, max 40 chars
            sitemap_unique = re.sub(r"[^a-zA-Z0-9]", "", params.unique_name)[:33] + "sitemap"
            sitemap_id = await _upsert_sitemap(
                app_ctx, base_url, headers,
                sitemap_xml=sitemap_xml,
                sitemap_unique_name=sitemap_unique,
                existing_sitemap_id=None,
            )
            logger.info("Created sitemap %s for app %s", sitemap_id, app_id)

            entity_refs: list[dict] = []
            for table in params.tables:
                meta_id = await _resolve_entity_metadata_id(app_ctx, base_url, headers, table)
                if meta_id:
                    entity_refs.append({
                        "entityid": meta_id,
                        "@odata.type": "Microsoft.Dynamics.CRM.entity",
                    })
                    components_added.append(f"table:{table}")
                else:
                    failed_tables.append(table)

            sitemap_ref = {
                "sitemapid": sitemap_id,
                "@odata.type": "Microsoft.Dynamics.CRM.sitemap",
            }
            await _call_add_app_components(
                app_ctx, base_url, headers, app_id, [sitemap_ref] + entity_refs
            )
            components_added.insert(0, f"sitemap:{sitemap_id}")
            logger.info("Added %d component(s) to app %s", 1 + len(entity_refs), app_id)

        # 3. Validate
        validation: dict | None = None
        if params.run_validate:
            try:
                vr = await _run_validate_app(app_ctx, base_url, headers, app_id)
                validation = _summarise_validation(vr)
                if validation["error_count"] > 0 and not params.publish_anyway:
                    return finalize_response({
                        "created": True,
                        "app_id": app_id,
                        "name": params.name,
                        "sitemap_id": sitemap_id,
                        "components_added": components_added,
                        "failed_tables": failed_tables,
                        "validation": validation,
                        "published": False,
                        "message": (
                            "App created but not published — validation errors found. "
                            "Fix them and call dataverse_publish_app, or retry with publish_anyway=true."
                        ),
                    })
            except httpx.HTTPStatusError as e:
                logger.warning("Validation call failed: %s", e.response.text)

        # 4. Publish
        published = False
        if params.publish:
            try:
                await _publish_app(app_ctx, base_url, headers, app_id)
                published = True
                logger.info("Published app %s", app_id)
            except httpx.HTTPStatusError as e:
                logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        return finalize_response({
            "created": True,
            "app_id": app_id,
            "name": params.name,
            "sitemap_id": sitemap_id,
            "components_added": components_added,
            "failed_tables": failed_tables,
            "validation": validation,
            "published": published,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_create_app")


# ---------------------------------------------------------------------------
# Tool: dataverse_update_app
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_app",
    annotations={
        "title": "Update Model-Driven App",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_app(params: UpdateAppInput, ctx: Context) -> str:
    """Update a model-driven app's name or description.
    To change components use dataverse_add_app_components / dataverse_remove_app_components.
    To update navigation use dataverse_set_app_sitemap.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        body: dict = {}
        if params.name is not None:
            body["name"] = params.name
        if params.description is not None:
            body["description"] = params.description
        if not body:
            return json.dumps({
                "error": True,
                "message": "No fields to update — supply at least name or description.",
            })

        resp = await request_with_retry(app_ctx.http_client, "PATCH",
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/appmodules({params.app_id})",
            json=body,
            headers={**headers, "Content-Type": "application/json"},
        )
        if resp.status_code == 404:
            return json.dumps({"error": True, "message": f"App '{params.app_id}' not found."})
        resp.raise_for_status()
        logger.info("Updated app %s", params.app_id)
        return json.dumps({"updated": True, "app_id": params.app_id, **body})

    except Exception as e:
        return tool_error_response(e, "dataverse_update_app")


# ---------------------------------------------------------------------------
# Tool: dataverse_add_app_components
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_add_app_components",
    annotations={
        "title": "Add App Components",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_add_app_components(params: AddAppComponentsInput, ctx: Context) -> str:
    """Add components (tables, forms, views, charts, BPFs) to a model-driven app.
    Each component specifies a type and either an id (GUID) or logical_name (for tables).
    Table components are automatically resolved to their MetadataId. Publishes after.
    Use dataverse_get_app first to check the current component list.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        refs: list[dict] = []
        errors: list[str] = []
        for comp in params.components:
            result = await _build_component_ref(
                app_ctx, base_url, headers, comp.type, comp.id, comp.logical_name
            )
            if isinstance(result, str):
                errors.append(result)
            else:
                refs.append(result)

        if errors:
            return json.dumps({
                "error": True,
                "message": f"Component resolution failed: {'; '.join(errors)}",
            })
        if not refs:
            return json.dumps({"error": True, "message": "No valid components to add."})

        await _call_add_app_components(app_ctx, base_url, headers, params.app_id, refs)
        logger.info("Added %d component(s) to app %s", len(refs), params.app_id)

        try:
            await _publish_app(app_ctx, base_url, headers, params.app_id)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        return json.dumps({
            "added": True,
            "app_id": params.app_id,
            "component_count": len(refs),
            "published": True,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_add_app_components")


# ---------------------------------------------------------------------------
# Tool: dataverse_remove_app_components
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_remove_app_components",
    annotations={
        "title": "Remove App Components",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_remove_app_components(params: RemoveAppComponentsInput, ctx: Context) -> str:
    """Remove components from a model-driven app.
    Same component spec format as dataverse_add_app_components.
    Use object_id values from dataverse_get_app to identify components. Publishes after.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        refs: list[dict] = []
        errors: list[str] = []
        for comp in params.components:
            result = await _build_component_ref(
                app_ctx, base_url, headers, comp.type, comp.id, comp.logical_name
            )
            if isinstance(result, str):
                errors.append(result)
            else:
                refs.append(result)

        if errors:
            return json.dumps({
                "error": True,
                "message": f"Component resolution failed: {'; '.join(errors)}",
            })
        if not refs:
            return json.dumps({"error": True, "message": "No valid components to remove."})

        await _call_remove_app_components(app_ctx, base_url, headers, params.app_id, refs)
        logger.info("Removed %d component(s) from app %s", len(refs), params.app_id)

        try:
            await _publish_app(app_ctx, base_url, headers, params.app_id)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        return json.dumps({
            "removed": True,
            "app_id": params.app_id,
            "component_count": len(refs),
            "published": True,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_remove_app_components")


# ---------------------------------------------------------------------------
# Tool: dataverse_set_app_sitemap
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_set_app_sitemap",
    annotations={
        "title": "Set App Sitemap",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_set_app_sitemap(params: SetAppSitemapInput, ctx: Context) -> str:
    """Create or replace the navigation sitemap for a model-driven app.
    Provide tables (flat list → auto Area/Group) or areas (structured). Validates
    the generated XML before writing. Publishes after updating.
    Returns sitemapxml_backup (prior XML or null if newly created) for rollback.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        if params.tables:
            areas = _tables_to_areas(params.tables, params.area_title, params.group_title)
        else:
            areas = params.areas or []

        sitemap_xml = _build_sitemap_xml(areas)
        xml_errors = _validate_sitemap_xml(sitemap_xml)
        if xml_errors:
            return json.dumps({
                "error": True,
                "message": f"Sitemap XML validation failed: {'; '.join(xml_errors)}",
            })

        existing_id, backup_xml = await _fetch_app_sitemap(app_ctx, base_url, headers, params.app_id)

        # sitemapnameunique allows only letters and numbers, max 40 chars; the
        # GUID's 32 hex chars plus the suffix fit exactly under the limit
        sitemap_unique = re.sub(r"[^a-zA-Z0-9]", "", params.app_id)[:33] + "sitemap"
        sitemap_id = await _upsert_sitemap(
            app_ctx, base_url, headers,
            sitemap_xml=sitemap_xml,
            sitemap_unique_name=sitemap_unique,
            existing_sitemap_id=existing_id,
        )
        logger.info("Upserted sitemap %s for app %s", sitemap_id, params.app_id)

        # Link the new sitemap to the app if it was just created
        if not existing_id:
            await _call_add_app_components(
                app_ctx, base_url, headers, params.app_id,
                [{"sitemapid": sitemap_id, "@odata.type": "Microsoft.Dynamics.CRM.sitemap"}],
            )

        try:
            await _publish_app(app_ctx, base_url, headers, params.app_id)
        except httpx.HTTPStatusError as e:
            logger.warning("Publish failed: %d %s", e.response.status_code, e.response.text)

        return finalize_response({
            "updated": True,
            "app_id": params.app_id,
            "sitemap_id": sitemap_id,
            "sitemap_created": existing_id is None,
            "published": True,
            "sitemapxml_backup": backup_xml,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_set_app_sitemap")


# ---------------------------------------------------------------------------
# Tool: dataverse_publish_app
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_publish_app",
    annotations={
        "title": "Publish Model-Driven App",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_publish_app(params: PublishAppInput, ctx: Context) -> str:
    """Publish a model-driven app to make it visible to users.
    Unpublished changes are invisible until this is called. Use dataverse_validate_app
    first to catch errors before publishing.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        await _publish_app(app_ctx, base_url, headers, params.app_id)
        logger.info("Published app %s", params.app_id)
        return json.dumps({"published": True, "app_id": params.app_id})

    except Exception as e:
        return tool_error_response(e, "dataverse_publish_app")


# ---------------------------------------------------------------------------
# Tool: dataverse_assign_app_role
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_assign_app_role",
    annotations={
        "title": "Assign / Remove App Security Role",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_assign_app_role(params: AssignAppRoleInput, ctx: Context) -> str:
    """Associate or disassociate a Dataverse security role with a model-driven app.
    action='add' grants users in the role access to the app.
    action='remove' revokes that access.
    Use dataverse_query_table against the 'roles' entity set to find role IDs.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        assoc_url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/appmodules({params.app_id})/appmoduleroles_association/$ref"
        )
        role_ref = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/roles({params.role_id})"
        )

        if params.action == "add":
            resp = await request_with_retry(app_ctx.http_client, "POST",
                assoc_url,
                json={"@odata.id": role_ref},
                headers={**headers, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            logger.info("Associated role %s with app %s", params.role_id, params.app_id)
        else:
            resp = await request_with_retry(app_ctx.http_client, "DELETE",
                f"{assoc_url}?$id={role_ref}",
                headers=headers,
            )
            resp.raise_for_status()
            logger.info("Disassociated role %s from app %s", params.role_id, params.app_id)

        return json.dumps({
            "action": params.action,
            "app_id": params.app_id,
            "role_id": params.role_id,
            "success": True,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_assign_app_role")
