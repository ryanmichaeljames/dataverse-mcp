"""Dependency analysis tools for the Dataverse MCP server."""

import json
import logging

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("solutions")
from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    build_headers,
    get_app_ctx,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
)
from dataverse_mcp.models import AnalyzeDependenciesInput
from dataverse_mcp.tools.solutions import COMPONENT_TYPE_NAMES

logger = logging.getLogger(__name__)

_DIRECTION_FUNCTION = {
    "blocking_delete": "RetrieveDependenciesForDelete",
    "dependents": "RetrieveDependentComponents",
    "required": "RetrieveRequiredComponents",
}


def _resolve_type(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return COMPONENT_TYPE_NAMES.get(code, f"ComponentType({code})")


@tool(
    name="dataverse_analyze_dependencies",
    annotations={
        "title": "Analyze Component Dependencies",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_analyze_dependencies(
    params: AnalyzeDependenciesInput, ctx: Context
) -> str:
    """Analyze dependencies for a Dataverse solution component.

    Exposes three directions via the direction parameter:
    - blocking_delete: components that must be removed before this one can be deleted.
    - dependents: all components that reference/depend on this component.
    - required: all components this component requires to exist.

    Use component_type integer codes (1=Entity, 2=Attribute, 61=WebResource, etc.)
    and the component's metadata GUID for component_id.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    function_name = _DIRECTION_FUNCTION[params.direction]
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{function_name}(ComponentType=@ct,ObjectId=@oid)"
        f"?@ct={params.component_type}&@oid={params.component_id}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as e:
        return tool_error_response(e, "dataverse_analyze_dependencies")
    except Exception as e:
        return tool_error_response(e, "dataverse_analyze_dependencies")

    raw_deps = payload.get("value", [])

    # Enrich with human-readable type names; surface the fields relevant to this direction
    dependencies = []
    for dep in raw_deps:
        dep.pop("@odata.type", None)
        entry: dict = {}
        if params.direction in ("blocking_delete", "dependents"):
            entry = {
                "dependent_component_type": dep.get("dependentcomponenttype"),
                "dependent_component_type_name": _resolve_type(dep.get("dependentcomponenttype")),
                "dependent_component_id": dep.get("dependentcomponentobjectid"),
                "required_component_type": dep.get("requiredcomponenttype"),
                "required_component_type_name": _resolve_type(dep.get("requiredcomponenttype")),
                "required_component_id": dep.get("requiredcomponentobjectid"),
            }
        else:  # required
            entry = {
                "required_component_type": dep.get("requiredcomponenttype"),
                "required_component_type_name": _resolve_type(dep.get("requiredcomponenttype")),
                "required_component_id": dep.get("requiredcomponentobjectid"),
                "dependent_component_type": dep.get("dependentcomponenttype"),
                "dependent_component_type_name": _resolve_type(dep.get("dependentcomponenttype")),
                "dependent_component_id": dep.get("dependentcomponentobjectid"),
            }
        dependencies.append(entry)

    return json.dumps({
        "component": {
            "id": params.component_id,
            "type": params.component_type,
            "type_name": _resolve_type(params.component_type),
        },
        "direction": params.direction,
        "function": function_name,
        "count": len(dependencies),
        "dependencies": dependencies,
    })
