"""Solution component dependency tools for the Dataverse MCP server."""

import asyncio
import json
import logging

from mcp.server.fastmcp import Context
from PowerPlatform.Dataverse.core.errors import DataverseError, HttpError

from dataverse_mcp._app import mcp
from dataverse_mcp.client import AppContext
from dataverse_mcp.models import (
    AppComponentsInput,
    ComponentDependencyInput,
    SolutionDependencyInput,
)
from dataverse_mcp.tools.solutions import COMPONENT_TYPE_NAMES

logger = logging.getLogger(__name__)


def _get_client(ctx: Context):
    """Extract the DataverseClient from the FastMCP lifespan context."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    return app_ctx.client


def _enrich_dependency(record: dict) -> dict:
    """Add human-readable component type names to a dependency record."""
    dep_type = record.get("dependentcomponenttype")
    if dep_type is not None:
        record["dependentcomponenttype_name"] = COMPONENT_TYPE_NAMES.get(
            dep_type, f"Unknown ({dep_type})"
        )
    req_type = record.get("requiredcomponenttype")
    if req_type is not None:
        record["requiredcomponenttype_name"] = COMPONENT_TYPE_NAMES.get(
            req_type, f"Unknown ({req_type})"
        )
    return record


def _call_unbound_function(client, url: str) -> list[dict]:
    """Call an OData unbound function and return the value array.

    Uses the SDK's internal OData client to make an authenticated GET request.
    The OData function response has the shape: {"value": [...]}.
    """
    with client._scoped_odata() as odata:
        response = odata._request("get", url)
        data = response.json()
        return data.get("value", [])


def _call_scalar_function(client, url: str) -> dict:
    """Call an OData unbound function that returns a single complex type object.

    Used for functions that return a ComplexType response rather than a
    collection (e.g., IsComponentCustomizable returns IsComponentCustomizableResponse).
    """
    with client._scoped_odata() as odata:
        response = odata._request("get", url)
        return response.json()


@mcp.tool(
    name="dataverse_get_dependent_components",
    annotations={
        "title": "Get Dependent Components",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_dependent_components(
    params: ComponentDependencyInput, ctx: Context
) -> str:
    """Retrieve solution components that directly depend on the specified component.

    Calls the RetrieveDependentComponents Web API function. Returns a list of
    dependency records showing which components would be affected if this
    component were changed or removed.

    Use this to answer "who depends on me?" — useful when planning to move or
    modify a component across solutions. Combine with
    dataverse_get_required_components to build a full dependency picture.

    Each dependency record includes both the integer type code and a
    human-readable type name for both the dependent and required components.
    """
    client = _get_client(ctx)

    try:

        def _query():
            url = (
                f"{client._get_odata().api}"
                f"/RetrieveDependentComponents"
                f"(ObjectId=@obj,ComponentType=@comp)"
                f"?@obj={params.object_id}&@comp={params.component_type}"
            )
            raw = _call_unbound_function(client, url)
            return [_enrich_dependency(r) for r in raw]

        dependencies = await asyncio.to_thread(_query)
        return json.dumps({
            "dependencies": dependencies,
            "count": len(dependencies),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_dependent_components")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_required_components",
    annotations={
        "title": "Get Required Components",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_required_components(
    params: ComponentDependencyInput, ctx: Context
) -> str:
    """Retrieve solution components that this component directly requires.

    Calls the RetrieveRequiredComponents Web API function. Returns a list of
    dependency records showing which components this component depends on.

    Use this to answer "what do I need?" — useful when moving a component to
    a new solution to ensure all prerequisites are also included. Pair with
    dataverse_get_dependent_components to get the full dependency graph.

    Each dependency record includes both the integer type code and a
    human-readable type name for both the dependent and required components.
    """
    client = _get_client(ctx)

    try:

        def _query():
            url = (
                f"{client._get_odata().api}"
                f"/RetrieveRequiredComponents"
                f"(ObjectId=@obj,ComponentType=@comp)"
                f"?@obj={params.object_id}&@comp={params.component_type}"
            )
            raw = _call_unbound_function(client, url)
            return [_enrich_dependency(r) for r in raw]

        dependencies = await asyncio.to_thread(_query)
        return json.dumps({
            "dependencies": dependencies,
            "count": len(dependencies),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_required_components")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_dependencies_for_delete",
    annotations={
        "title": "Get Dependencies for Delete",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_dependencies_for_delete(
    params: ComponentDependencyInput, ctx: Context
) -> str:
    """Retrieve dependencies that would block deleting the specified component.

    Calls the RetrieveDependenciesForDelete Web API function. Returns records
    describing which other components depend on this component and would
    prevent it from being deleted.

    Use this before attempting to delete a component to understand what needs
    to be cleaned up first. An empty result means the component can be safely
    deleted with no blockers.

    Each dependency record includes both the integer type code and a
    human-readable type name for both the dependent and required components.
    """
    client = _get_client(ctx)

    try:

        def _query():
            url = (
                f"{client._get_odata().api}"
                f"/RetrieveDependenciesForDelete"
                f"(ObjectId=@obj,ComponentType=@comp)"
                f"?@obj={params.object_id}&@comp={params.component_type}"
            )
            raw = _call_unbound_function(client, url)
            return [_enrich_dependency(r) for r in raw]

        dependencies = await asyncio.to_thread(_query)
        return json.dumps({
            "dependencies": dependencies,
            "count": len(dependencies),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_dependencies_for_delete")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_dependencies_for_uninstall",
    annotations={
        "title": "Get Dependencies for Uninstall",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_dependencies_for_uninstall(
    params: SolutionDependencyInput, ctx: Context
) -> str:
    """Retrieve dependencies that would block uninstalling a managed solution.

    Calls the RetrieveDependenciesForUninstall Web API function. Returns
    dependency records describing which components from other solutions depend
    on components in this solution, preventing uninstall.

    Use this before attempting to uninstall a managed solution to identify
    what other solutions or components reference it. Resolving these
    dependencies (by updating or removing the dependent components) is
    required before the solution can be removed.

    Each dependency record includes both the integer type code and a
    human-readable type name for both the dependent and required components.
    """
    client = _get_client(ctx)
    escaped_name = params.solution_unique_name.replace("'", "''")

    try:

        def _query():
            url = (
                f"{client._get_odata().api}"
                f"/RetrieveDependenciesForUninstall"
                f"(SolutionUniqueName=@sol)"
                f"?@sol='{escaped_name}'"
            )
            raw = _call_unbound_function(client, url)
            return [_enrich_dependency(r) for r in raw]

        dependencies = await asyncio.to_thread(_query)
        return json.dumps({
            "dependencies": dependencies,
            "count": len(dependencies),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
    except Exception as e:
        logger.exception(
            "Unexpected error in dataverse_get_dependencies_for_uninstall"
        )
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_missing_dependencies",
    annotations={
        "title": "Get Missing Dependencies",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_missing_dependencies(
    params: SolutionDependencyInput, ctx: Context
) -> str:
    """Retrieve required components missing from the specified solution.

    Calls the RetrieveMissingDependencies Web API function. Returns dependency
    records for components that are referenced by the solution but not included
    in it — meaning they must exist in the target environment for the solution
    to import successfully.

    Use this to validate a solution before export or deployment. An empty
    result means the solution is self-contained or all dependencies are
    satisfied by the current environment.

    Each dependency record includes both the integer type code and a
    human-readable type name for both the dependent and required components.
    """
    client = _get_client(ctx)
    escaped_name = params.solution_unique_name.replace("'", "''")

    try:

        def _query():
            url = (
                f"{client._get_odata().api}"
                f"/RetrieveMissingDependencies"
                f"(SolutionUniqueName=@sol)"
                f"?@sol='{escaped_name}'"
            )
            raw = _call_unbound_function(client, url)
            return [_enrich_dependency(r) for r in raw]

        dependencies = await asyncio.to_thread(_query)
        return json.dumps({
            "dependencies": dependencies,
            "count": len(dependencies),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_missing_dependencies")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_is_component_customizable",
    annotations={
        "title": "Is Component Customizable",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_is_component_customizable(
    params: ComponentDependencyInput, ctx: Context
) -> str:
    """Check whether a solution component is customizable.

    Calls the IsComponentCustomizable Web API function. Returns a boolean
    indicating whether the component's customizable flag is set to true.

    Use this as a pre-flight check before attempting to modify or relayer a
    component. Managed components with IsCustomizable=false are locked by the
    publisher and cannot be changed — attempting to modify them will fail.

    Pair with dataverse_get_dependent_components and
    dataverse_get_required_components to build a full picture before
    restructuring solutions.
    """
    client = _get_client(ctx)

    try:

        def _query():
            url = (
                f"{client._get_odata().api}"
                f"/IsComponentCustomizable"
                f"(ComponentId=@id,ComponentType=@comp)"
                f"?@id={params.object_id}&@comp={params.component_type}"
            )
            return _call_scalar_function(client, url)

        result = await asyncio.to_thread(_query)
        return json.dumps({
            "is_customizable": result.get("IsComponentCustomizable"),
            "object_id": params.object_id,
            "component_type": params.component_type,
            "component_type_name": COMPONENT_TYPE_NAMES.get(
                params.component_type, f"Unknown ({params.component_type})"
            ),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in dataverse_is_component_customizable")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_app_components",
    annotations={
        "title": "Get App Components",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_app_components(
    params: AppComponentsInput, ctx: Context
) -> str:
    """Retrieve all components belonging to a model-driven app.

    Calls the RetrieveAppComponents Web API function. Returns the full set
    of solution components included in the specified model-driven app (AppModule).

    Use this to understand exactly what an app depends on before moving it to
    a new solution or cleaning up its component references. Query the appmodule
    table with dataverse_query_table to find app module IDs.

    Each component record includes the component type code. Use
    dataverse_list_solution_components, dataverse_get_dependent_components,
    and dataverse_get_required_components to drill further into specific
    components returned here.
    """
    client = _get_client(ctx)

    try:

        def _query():
            url = (
                f"{client._get_odata().api}"
                f"/RetrieveAppComponents"
                f"(AppModuleId=@id)"
                f"?@id={params.app_module_id}"
            )
            return _call_unbound_function(client, url)

        components = await asyncio.to_thread(_query)
        return json.dumps({
            "components": components,
            "count": len(components),
        })
    except HttpError as e:
        logger.error("Dataverse HTTP error: %s (status=%d)", e.message, e.status_code)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.status_code}: {e.message}",
            "is_transient": e.is_transient,
        })
    except DataverseError as e:
        logger.error("Dataverse error: %s", e.message)
        return json.dumps({"error": True, "message": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_app_components")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
