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
        "exploration."
    ),
    lifespan=dataverse_lifespan,
)
