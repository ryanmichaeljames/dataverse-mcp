"""FastMCP application instance.

This module exists to avoid circular imports between server.py and tool
modules.  Tool modules import ``mcp`` from here; server.py imports ``mcp``
from here and registers tool modules.
"""

from mcp.server.fastmcp import FastMCP

from dataverse_mcp.client import dataverse_lifespan

mcp = FastMCP(
    "dataverse_mcp",
    instructions=(
        "Dataverse MCP server for interacting with Microsoft Dataverse "
        "environments. Use dataverse_list_solutions to discover solutions, "
        "dataverse_query_table to search records, and "
        "dataverse_list_tables / dataverse_get_table_metadata for schema "
        "exploration. Use dataverse_get_dependent_components and "
        "dataverse_get_required_components to map component dependencies, "
        "dataverse_get_dependencies_for_delete to check what blocks deletion, "
        "dataverse_get_dependencies_for_uninstall to check what blocks "
        "uninstalling a managed solution, and "
        "dataverse_get_missing_dependencies to validate a solution is "
        "self-contained before deployment. Use "
        "dataverse_is_component_customizable to check whether a component "
        "can be modified, and dataverse_get_app_components to enumerate "
        "all components belonging to a model-driven app."
    ),
    lifespan=dataverse_lifespan,
)
