"""Environment variable value tools for the Dataverse MCP server."""

import json
import logging
import re
from urllib.parse import urlencode

from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("variables")
from dataverse_mcp.client import (
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
    CreateEnvironmentVariableValueInput,
    DeleteEnvironmentVariableValueInput,
    GetEnvironmentVariableValuesInput,
    UpdateEnvironmentVariableValueInput,
)
from dataverse_mcp.tools.environment_variables import _resolve_definition_by_name_or_id

logger = logging.getLogger(__name__)

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

_VALUE_SELECT = (
    "environmentvariablevalueid,"
    "value,"
    "_environmentvariabledefinitionid_value"
)


def _strip_odata(record: dict) -> dict:
    return {k: v for k, v in record.items() if "@" not in k}


# ---------------------------------------------------------------------------
# Tool: dataverse_get_environment_variable_values
# ---------------------------------------------------------------------------


@tool(
    name="dataverse_get_environment_variable_values",
    annotations={
        "title": "Get Environment Variable Values",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_environment_variable_values(
    params: GetEnvironmentVariableValuesInput, ctx: Context
) -> str:
    """Get environment variable value record(s).

    Provide exactly one targeting path:
    - value_id: fetch a single value record by its own GUID directly.
    - definition_id: list value record(s) linked to the given definition GUID.
    - name: resolve the definition by schema name or display name, then list
      its value record(s). Schema name is tried first; display name is the
      fallback.

    Returns a list shape (records, count, has_more) in all cases for a
    consistent calling convention, even when a single record is expected.
    When no value record exists for a definition the list will be empty.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        if params.value_id is not None:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariablevalues({params.value_id})"
                f"?$select={_VALUE_SELECT}"
            )
            resp = await request_with_retry(
                app_ctx.http_client, "GET", url, headers=headers
            )
            if resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": (
                        f"Environment variable value '{params.value_id}' not found."
                    ),
                })
            resp.raise_for_status()
            record = _strip_odata(resp.json())
            return finalize_response({
                "records": [record],
                "count": 1,
                "has_more": False,
            })

        # Resolve definition (by definition_id or name)
        definition = await _resolve_definition_by_name_or_id(
            app_ctx,
            base_url,
            headers,
            definition_id=params.definition_id,
            name=params.name,
        )
        if definition.get("error"):
            return json.dumps(definition)

        def_id = definition["environmentvariabledefinitionid"]
        query = urlencode(
            {
                "$filter": (
                    f"_environmentvariabledefinitionid_value eq "
                    f"'{odata_quote(def_id)}'"
                ),
                "$select": _VALUE_SELECT,
                "$top": str(params.top),
            },
            safe="$,",
        )
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/environmentvariablevalues?{query}"
        )
        records = await paginate_records(url, headers, params.top, app_ctx.http_client)
        cleaned = [_strip_odata(r) for r in records]
        return finalize_response({
            "records": cleaned,
            "count": len(cleaned),
            "has_more": len(cleaned) >= params.top,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_get_environment_variable_values")


# ---------------------------------------------------------------------------
# Tool: dataverse_create_environment_variable_value
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_environment_variable_value",
    annotations={
        "title": "Create Environment Variable Value",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_environment_variable_value(
    params: CreateEnvironmentVariableValueInput, ctx: Context
) -> str:
    """Create a new environment variable value record bound to a definition.

    Provide definition_id (GUID) or name (schema name or display name) to
    identify the parent definition. Schema name is tried first; display name
    is the fallback.

    The value record is created via POST with an OData bind to the definition.
    One value record per definition per environment is the Dataverse convention;
    use dataverse_update_environment_variable_value to change an existing value.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        definition = await _resolve_definition_by_name_or_id(
            app_ctx,
            base_url,
            headers,
            definition_id=params.definition_id,
            name=params.name,
        )
        if definition.get("error"):
            return json.dumps(definition)

        def_id = definition["environmentvariabledefinitionid"]
        val_url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/environmentvariablevalues"
        )
        body = {
            "value": params.value,
            "EnvironmentVariableDefinitionId@odata.bind": (
                f"/environmentvariabledefinitions({def_id})"
            ),
        }
        resp = await request_with_retry(
            app_ctx.http_client, "POST", val_url, json=body, headers=headers
        )
        resp.raise_for_status()

        location = resp.headers.get("OData-EntityId") or resp.headers.get("location", "")
        m = _GUID_RE.search(location)
        value_id = m.group(0) if m else ""

        logger.info(
            "Created environment variable value %s for definition %s",
            value_id,
            def_id,
        )
        return json.dumps({
            "created": True,
            "environment_variable_value_id": value_id,
            "environment_variable_definition_id": def_id,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_create_environment_variable_value")


# ---------------------------------------------------------------------------
# Tool: dataverse_update_environment_variable_value
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_update_environment_variable_value",
    annotations={
        "title": "Update Environment Variable Value",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_environment_variable_value(
    params: UpdateEnvironmentVariableValueInput, ctx: Context
) -> str:
    """Update an existing environment variable value record.

    Provide exactly one targeting path:
    - value_id: PATCH the value record by its own GUID directly.
    - definition_id: resolve the definition, look up its value record, PATCH it.
    - name: resolve the definition by schema name or display name, look up its
      value record, PATCH it. Schema name is tried first; display name is the
      fallback.

    If no value record exists for the resolved definition, returns an error —
    use dataverse_create_environment_variable_value to create one first.

    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)
        patch_headers = {**headers, "Content-Type": "application/json"}

        if params.value_id is not None:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariablevalues({params.value_id})"
            )
            resp = await request_with_retry(
                app_ctx.http_client,
                "PATCH",
                url,
                json={"value": params.value},
                headers=patch_headers,
            )
            if resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": (
                        f"Environment variable value '{params.value_id}' not found."
                    ),
                })
            resp.raise_for_status()
            logger.info("Patched environment variable value %s", params.value_id)
            return json.dumps({
                "updated": True,
                "environment_variable_value_id": params.value_id,
            })

        # Resolve definition (by definition_id or name)
        definition = await _resolve_definition_by_name_or_id(
            app_ctx,
            base_url,
            headers,
            definition_id=params.definition_id,
            name=params.name,
        )
        if definition.get("error"):
            return json.dumps(definition)

        def_id = definition["environmentvariabledefinitionid"]
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

        if not existing:
            return json.dumps({
                "error": True,
                "message": (
                    "No value record exists for this definition; "
                    "create one first with dataverse_create_environment_variable_value."
                ),
            })

        val_id = existing[0]["environmentvariablevalueid"]
        patch_url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/environmentvariablevalues({val_id})"
        )
        patch_resp = await request_with_retry(
            app_ctx.http_client,
            "PATCH",
            patch_url,
            json={"value": params.value},
            headers=patch_headers,
        )
        patch_resp.raise_for_status()
        logger.info(
            "Patched environment variable value %s for definition %s", val_id, def_id
        )
        return json.dumps({
            "updated": True,
            "environment_variable_value_id": val_id,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_update_environment_variable_value")


# ---------------------------------------------------------------------------
# Tool: dataverse_delete_environment_variable_value
# ---------------------------------------------------------------------------


@delete_tool(
    name="dataverse_delete_environment_variable_value",
    annotations={
        "title": "Delete Environment Variable Value",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_delete_environment_variable_value(
    params: DeleteEnvironmentVariableValueInput, ctx: Context
) -> str:
    """Delete an environment variable value record (reset to default).

    Provide exactly one targeting path:
    - value_id: delete the value record by its own GUID directly.
    - definition_id: resolve the definition, look up its value record, delete it.
    - name: resolve the definition by schema name or display name, look up its
      value record, delete it. Schema name is tried first; display name is the
      fallback.

    Deleting the value record causes the environment variable to fall back to
    the definition's defaultvalue. The definition itself is not affected.
    To delete the definition (and cascade to the value), use
    dataverse_delete_environment_variable with target='definition' or 'both'.

    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    try:
        headers = await build_headers(app_ctx, base_url)

        if params.value_id is not None:
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
                f"/environmentvariablevalues({params.value_id})"
            )
            resp = await request_with_retry(
                app_ctx.http_client, "DELETE", url, headers=headers
            )
            if resp.status_code == 404:
                return json.dumps({
                    "error": True,
                    "message": (
                        f"Environment variable value '{params.value_id}' not found."
                    ),
                })
            resp.raise_for_status()
            logger.info("Deleted environment variable value %s", params.value_id)
            return json.dumps({
                "deleted": True,
                "environment_variable_value_id": params.value_id,
            })

        # Resolve definition (by definition_id or name)
        definition = await _resolve_definition_by_name_or_id(
            app_ctx,
            base_url,
            headers,
            definition_id=params.definition_id,
            name=params.name,
        )
        if definition.get("error"):
            return json.dumps(definition)

        def_id = definition["environmentvariabledefinitionid"]
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

        if not existing:
            return json.dumps({
                "error": True,
                "message": "No value record found for this definition.",
            })

        val_id = existing[0]["environmentvariablevalueid"]
        del_url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/environmentvariablevalues({val_id})"
        )
        del_resp = await request_with_retry(
            app_ctx.http_client, "DELETE", del_url, headers=headers
        )
        if del_resp.status_code == 404:
            return json.dumps({
                "error": True,
                "message": (
                    f"Environment variable value '{val_id}' not found."
                ),
            })
        del_resp.raise_for_status()
        logger.info(
            "Deleted environment variable value %s for definition %s", val_id, def_id
        )
        return json.dumps({
            "deleted": True,
            "environment_variable_value_id": val_id,
        })

    except Exception as e:
        return tool_error_response(e, "dataverse_delete_environment_variable_value")
