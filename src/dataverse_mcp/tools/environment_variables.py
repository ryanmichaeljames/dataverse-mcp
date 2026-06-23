"""Environment variable tools for the Dataverse MCP server."""

import json
import logging
import re
from urllib.parse import urlencode

from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("variables")
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
    CreateEnvironmentVariableInput,
    DeleteEnvironmentVariableInput,
    GetEnvironmentVariablesInput,
    UpdateEnvironmentVariableInput,
)
from dataverse_mcp.tools.solutions import (
    _resolve_solution_record,
    _solution_not_found_message,
)

logger = logging.getLogger(__name__)

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# solutioncomponent componenttype for environment variable definitions
_ENV_VAR_DEFINITION_COMPONENT_TYPE = 380

_DEFINITION_SELECT = (
    "environmentvariabledefinitionid,"
    "schemaname,"
    "displayname,"
    "type,"
    "defaultvalue,"
    "description,"
    "ismanaged"
)

_VALUE_EXPAND = (
    "environmentvariabledefinition_environmentvariablevalue"
    "($select=environmentvariablevalueid,value)"
)


def _strip_odata(record: dict) -> dict:
    return {k: v for k, v in record.items() if "@" not in k}


_RESOLVER_SELECT = (
    "environmentvariabledefinitionid,schemaname,displayname"
)


async def _resolve_definition_by_name_or_id(
    app_ctx: AppContext,
    base_url: str,
    headers: dict[str, str],
    *,
    definition_id: str | None,
    name: str | None,
) -> dict:
    """Resolve an environment variable definition by GUID or name.

    If definition_id is given, fetch it directly (returns error dict on 404).
    If name is given, query by schemaname first, then displayname as fallback.

    Returns:
        A dict containing at least environmentvariabledefinitionid and schemaname,
        OR a dict with {"error": True, "message": "..."} for callers to surface.
    """
    if definition_id is not None:
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/environmentvariabledefinitions({definition_id})"
            f"?$select={_RESOLVER_SELECT}"
        )
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        if resp.status_code == 404:
            return {
                "error": True,
                "message": f"Environment variable definition '{definition_id}' not found.",
            }
        resp.raise_for_status()
        return _strip_odata(resp.json())

    # name-based lookup
    q = odata_quote(name or "")

    # Step 1: try schemaname
    schema_query = urlencode(
        {
            "$filter": f"schemaname eq '{q}'",
            "$select": _RESOLVER_SELECT,
            "$top": "2",
        },
        safe="$,",
    )
    schema_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/environmentvariabledefinitions?{schema_query}"
    )
    schema_resp = await request_with_retry(
        app_ctx.http_client, "GET", schema_url, headers=headers
    )
    schema_resp.raise_for_status()
    schema_rows = schema_resp.json().get("value", [])

    if len(schema_rows) == 1:
        return _strip_odata(schema_rows[0])

    if len(schema_rows) >= 2:
        # schemaname is unique; treat first match as resolved (defensive fallback)
        return _strip_odata(schema_rows[0])

    # Step 2: schemaname returned 0 rows — try displayname
    display_query = urlencode(
        {
            "$filter": f"displayname eq '{q}'",
            "$select": _RESOLVER_SELECT,
            "$top": "2",
        },
        safe="$,",
    )
    display_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/environmentvariabledefinitions?{display_query}"
    )
    display_resp = await request_with_retry(
        app_ctx.http_client, "GET", display_url, headers=headers
    )
    display_resp.raise_for_status()
    display_rows = display_resp.json().get("value", [])

    if len(display_rows) == 1:
        return _strip_odata(display_rows[0])

    if len(display_rows) == 0:
        return {
            "error": True,
            "message": f"No environment variable matched name '{name}'.",
        }

    # len >= 2
    return {
        "error": True,
        "message": (
            f"Name '{name}' matched multiple definitions; "
            "use schema_name or a GUID."
        ),
    }


def _merge_value(definition: dict) -> dict:
    """Flatten the expanded value sub-collection into the definition record."""
    expand_key = "environmentvariabledefinition_environmentvariablevalue"
    value_records = definition.pop(expand_key, []) or []
    value_record = value_records[0] if value_records else {}
    definition["value"] = value_record.get("value")
    definition["environmentvariablevalueid"] = value_record.get(
        "environmentvariablevalueid"
    )
    return definition


async def _list_solution_env_var_definition_ids(
    app_ctx: AppContext,
    base_url: str,
    headers: dict[str, str],
    solution_id: str | None,
    solution_unique_name: str | None,
) -> tuple[str, list[str]]:
    """Resolve a solution and return its environment variable definition IDs (componenttype 380)."""
    solution = await _resolve_solution_record(
        app_ctx,
        base_url,
        headers,
        solution_id,
        solution_unique_name,
    )
    if solution is None:
        raise ValueError(_solution_not_found_message(solution_id, solution_unique_name))

    resolved_solution_id = solution.get("solutionid")
    if not resolved_solution_id:
        raise ValueError("Resolved solution is missing solutionid")

    query_params = {
        "$select": "objectid",
        "$filter": (
            f"_solutionid_value eq '{odata_quote(resolved_solution_id)}' and "
            f"componenttype eq {_ENV_VAR_DEFINITION_COMPONENT_TYPE}"
        ),
        "$top": "5000",
    }
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/solutioncomponents"
    full_url = f"{url}?{urlencode(query_params, safe='$,')}"

    components = await paginate_records(full_url, headers, 5000, app_ctx.http_client)
    definition_ids: list[str] = []
    seen: set[str] = set()
    for component in components:
        object_id = component.get("objectid")
        if not object_id:
            continue
        lower_id = object_id.lower()
        if lower_id in seen:
            continue
        seen.add(lower_id)
        definition_ids.append(object_id)

    return resolved_solution_id, definition_ids


# ---------------------------------------------------------------------------
# Tool: dataverse_get_environment_variables
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_get_environment_variables",
    annotations={
        "title": "Get Environment Variables",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_environment_variables(
    params: GetEnvironmentVariablesInput, ctx: Context
) -> str:
    """List environment variable definitions with their current values.

    Each record includes the definition fields (schemaname, displayname, type,
    defaultvalue, description, ismanaged) plus the current value from the linked
    environmentvariablevalue record. The value field is null when no value record
    exists — fall back to defaultvalue in that case.

    Provide name (schemaname or displayname) to look up a single definition.
    Schema name is tried first; display name is the fallback. name cannot be
    combined with solution_id or solution_unique_name.

    Scope results to a specific solution with solution_id or solution_unique_name
    (componenttype 380 solutioncomponents query). Omit both to list all definitions
    in the environment.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        # Single-record lookup by name
        if params.name is not None:
            definition = await _resolve_definition_by_name_or_id(
                app_ctx,
                base_url,
                headers,
                definition_id=None,
                name=params.name,
            )
            if definition.get("error"):
                return json.dumps(definition)

            def_id = definition.get("environmentvariabledefinitionid", "")
            query: dict[str, str] = {
                "$select": _DEFINITION_SELECT,
                "$expand": _VALUE_EXPAND,
            }
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariabledefinitions({def_id})"
                f"?{urlencode(query, safe='$,')}"
            )
            resp = await request_with_retry(
                app_ctx.http_client, "GET", url, headers=headers
            )
            if resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": (
                        f"Environment variable definition '{def_id}' not found."
                    ),
                })
            resp.raise_for_status()
            record = _merge_value(_strip_odata(resp.json()))
            return finalize_response({"record": record})

        if params.solution_id or params.solution_unique_name:
            resolved_solution_id, definition_ids = (
                await _list_solution_env_var_definition_ids(
                    app_ctx,
                    base_url,
                    headers,
                    params.solution_id,
                    params.solution_unique_name,
                )
            )
            if not definition_ids:
                return finalize_response({
                    "records": [],
                    "count": 0,
                    "has_more": False,
                    "solution_id": resolved_solution_id,
                })

            records: list[dict] = []
            remaining = params.top
            chunk_size = 100
            for i in range(0, len(definition_ids), chunk_size):
                if remaining <= 0:
                    break
                chunk = definition_ids[i : i + chunk_size]
                id_filter = " or ".join(
                    f"environmentvariabledefinitionid eq '{odata_quote(def_id)}'"
                    for def_id in chunk
                )
                query: dict[str, str] = {
                    "$select": _DEFINITION_SELECT,
                    "$expand": _VALUE_EXPAND,
                    "$top": str(remaining),
                    "$filter": id_filter,
                }
                url = (
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                    f"/environmentvariabledefinitions?{urlencode(query, safe='$,')}"
                )
                chunk_records = await paginate_records(
                    url, headers, remaining, app_ctx.http_client
                )
                records.extend(chunk_records)
                remaining = params.top - len(records)

            merged = [_merge_value(_strip_odata(r)) for r in records]
            return finalize_response({
                "records": merged,
                "count": len(merged),
                "has_more": len(merged) >= params.top,
                "solution_id": resolved_solution_id,
            })

        # No solution filter — list all definitions
        query = {
            "$select": _DEFINITION_SELECT,
            "$expand": _VALUE_EXPAND,
            "$top": str(params.top),
        }
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/environmentvariabledefinitions?{urlencode(query, safe='$,')}"
        )
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        merged = [_merge_value(_strip_odata(r)) for r in records]
        return finalize_response({
            "records": merged,
            "count": len(merged),
            "has_more": len(merged) >= params.top,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_get_environment_variables")


# ---------------------------------------------------------------------------
# Tool: dataverse_create_environment_variable
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_environment_variable",
    annotations={
        "title": "Create Environment Variable",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_environment_variable(
    params: CreateEnvironmentVariableInput, ctx: Context
) -> str:
    """Create a new environment variable definition and optional initial value.

    Creates the environmentvariabledefinition record. If value is supplied a
    bound environmentvariablevalue record is created in the same operation.

    type values: 100000000=String, 100000001=Number, 100000002=Boolean,
    100000003=JSON, 100000004=Data source, 100000005=Secret.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    body: dict = {
        "schemaname": params.schema_name,
        "displayname": params.display_name,
        "type": params.type,
    }
    if params.default_value is not None:
        body["defaultvalue"] = params.default_value
    if params.description is not None:
        body["description"] = params.description

    def_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/environmentvariabledefinitions"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        if params.solution_unique_name:
            headers = {**headers, "MSCRM.SolutionUniqueName": params.solution_unique_name}

        resp = await request_with_retry(
            app_ctx.http_client, "POST", def_url, json=body, headers=headers
        )
        resp.raise_for_status()

        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        m = _GUID_RE.search(location)
        definition_id = m.group(0) if m else ""

        logger.info(
            "Created environment variable definition '%s' (%s)",
            params.schema_name,
            definition_id,
        )

        value_id: str | None = None
        if params.value is not None and definition_id:
            val_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariablevalues"
            )
            val_body = {
                "value": params.value,
                "EnvironmentVariableDefinitionId@odata.bind": (
                    f"/environmentvariabledefinitions({definition_id})"
                ),
            }
            val_resp = await request_with_retry(
                app_ctx.http_client, "POST", val_url, json=val_body, headers=headers
            )
            val_resp.raise_for_status()
            val_location = (
                val_resp.headers.get("OData-EntityId")
                or val_resp.headers.get("location", "")
            )
            mv = _GUID_RE.search(val_location)
            value_id = mv.group(0) if mv else None
            logger.info(
                "Created environment variable value for definition '%s' (%s)",
                definition_id,
                value_id,
            )

        return json.dumps({
            "created": True,
            "schema_name": params.schema_name,
            "display_name": params.display_name,
            "environment_variable_definition_id": definition_id,
            "environment_variable_value_id": value_id,
            "solution_unique_name": params.solution_unique_name,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_create_environment_variable")


# ---------------------------------------------------------------------------
# Tool: dataverse_update_environment_variable
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_environment_variable",
    annotations={
        "title": "Update Environment Variable",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_environment_variable(
    params: UpdateEnvironmentVariableInput, ctx: Context
) -> str:
    """Update an environment variable definition and/or its current value.

    Patches definition fields (display_name, default_value, description) when
    provided. The value field uses upsert logic: queries for an existing
    environmentvariablevalue record and PATCHes it if found, otherwise POSTs a
    new one bound to the definition.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    def_id = params.environment_variable_definition_id

    try:
        headers = await build_headers(app_ctx, base_url)
        patch_headers = {**headers, "Content-Type": "application/json"}

        # Update definition fields
        def_body: dict = {}
        if params.display_name is not None:
            def_body["displayname"] = params.display_name
        if params.default_value is not None:
            def_body["defaultvalue"] = params.default_value
        if params.description is not None:
            def_body["description"] = params.description

        if def_body:
            def_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariabledefinitions({def_id})"
            )
            def_resp = await request_with_retry(
                app_ctx.http_client, "PATCH", def_url, json=def_body, headers=patch_headers
            )
            if def_resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": (
                        f"Environment variable definition '{def_id}' not found."
                    ),
                })
            def_resp.raise_for_status()
            logger.info("Updated environment variable definition %s", def_id)

        # Upsert value
        value_result: dict = {}
        if params.value is not None:
            find_query = urlencode(
                {
                    "$select": "environmentvariablevalueid",
                    "$filter": (
                        f"_environmentvariabledefinitionid_value eq "
                        f"'{odata_quote(def_id)}'"
                    ),
                    "$top": "1",
                },
                safe="$,",
            )
            find_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariablevalues?{find_query}"
            )
            find_resp = await request_with_retry(
                app_ctx.http_client, "GET", find_url, headers=headers
            )
            find_resp.raise_for_status()
            existing = find_resp.json().get("value", [])

            if existing:
                val_id = existing[0]["environmentvariablevalueid"]
                patch_val_url = (
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                    f"/environmentvariablevalues({val_id})"
                )
                patch_val_resp = await request_with_retry(
                    app_ctx.http_client,
                    "PATCH",
                    patch_val_url,
                    json={"value": params.value},
                    headers=patch_headers,
                )
                patch_val_resp.raise_for_status()
                value_result = {"value_upserted": "patched", "environment_variable_value_id": val_id}
                logger.info(
                    "Patched environment variable value %s for definition %s",
                    val_id,
                    def_id,
                )
            else:
                val_url = (
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                    f"/environmentvariablevalues"
                )
                val_body = {
                    "value": params.value,
                    "EnvironmentVariableDefinitionId@odata.bind": (
                        f"/environmentvariabledefinitions({def_id})"
                    ),
                }
                post_val_resp = await request_with_retry(
                    app_ctx.http_client, "POST", val_url, json=val_body, headers=headers
                )
                post_val_resp.raise_for_status()
                val_location = (
                    post_val_resp.headers.get("OData-EntityId")
                    or post_val_resp.headers.get("location", "")
                )
                mv = _GUID_RE.search(val_location)
                new_val_id = mv.group(0) if mv else None
                value_result = {"value_upserted": "posted", "environment_variable_value_id": new_val_id}
                logger.info(
                    "Posted new environment variable value %s for definition %s",
                    new_val_id,
                    def_id,
                )

        result: dict = {
            "updated": True,
            "environment_variable_definition_id": def_id,
            "definition_changes": def_body,
        }
        result.update(value_result)
        return json.dumps(result)

    except Exception as e:
        return tool_error_response(e, "dataverse_update_environment_variable")


# ---------------------------------------------------------------------------
# Tool: dataverse_delete_environment_variable
# ---------------------------------------------------------------------------


@delete_tool(
    name="dataverse_delete_environment_variable",
    annotations={
        "title": "Delete Environment Variable",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_delete_environment_variable(
    params: DeleteEnvironmentVariableInput, ctx: Context
) -> str:
    """Delete an environment variable definition, its current value, or both.

    target='definition' deletes the definition record. Dataverse cascades the
    delete to the linked value record automatically.
    target='value' deletes only the environmentvariablevalue record, leaving the
    definition intact. Useful to reset to the defaultvalue.
    target='both' explicitly deletes the value record first, then the definition.

    Only unmanaged environment variables can be deleted. Managed ones must be
    removed by uninstalling the owning solution.

    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    def_id = params.environment_variable_definition_id

    try:
        headers = await build_headers(app_ctx, base_url)
        target = params.target

        deleted_value_id: str | None = None

        if target in ("value", "both"):
            # Locate the value record for this definition
            find_query = urlencode(
                {
                    "$select": "environmentvariablevalueid",
                    "$filter": (
                        f"_environmentvariabledefinitionid_value eq "
                        f"'{odata_quote(def_id)}'"
                    ),
                    "$top": "1",
                },
                safe="$,",
            )
            find_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariablevalues?{find_query}"
            )
            find_resp = await request_with_retry(
                app_ctx.http_client, "GET", find_url, headers=headers
            )
            find_resp.raise_for_status()
            existing = find_resp.json().get("value", [])

            if existing:
                val_id = existing[0]["environmentvariablevalueid"]
                del_val_url = (
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                    f"/environmentvariablevalues({val_id})"
                )
                del_val_resp = await request_with_retry(
                    app_ctx.http_client, "DELETE", del_val_url, headers=headers
                )
                if del_val_resp.status_code not in (204, 404):
                    del_val_resp.raise_for_status()
                deleted_value_id = val_id
                logger.info(
                    "Deleted environment variable value %s for definition %s",
                    val_id,
                    def_id,
                )
            else:
                logger.info(
                    "No value record found for environment variable definition %s",
                    def_id,
                )

        if target in ("definition", "both"):
            def_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariabledefinitions({def_id})"
            )
            del_def_resp = await request_with_retry(
                app_ctx.http_client, "DELETE", def_url, headers=headers
            )
            if del_def_resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": (
                        f"Environment variable definition '{def_id}' not found."
                    ),
                })
            del_def_resp.raise_for_status()
            logger.info("Deleted environment variable definition %s", def_id)

        result: dict = {
            "deleted": True,
            "environment_variable_definition_id": def_id,
            "target": target,
        }
        if deleted_value_id is not None:
            result["environment_variable_value_id"] = deleted_value_id
        return json.dumps(result)

    except Exception as e:
        return tool_error_response(e, "dataverse_delete_environment_variable")
