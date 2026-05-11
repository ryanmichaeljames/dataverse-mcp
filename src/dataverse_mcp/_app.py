"""FastMCP application instance.

This module exists to avoid circular imports between server.py and tool
modules.  Tool modules import ``mcp`` from here; server.py imports ``mcp``
from here and registers tool modules.
"""

import os

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

_ALLOW_WRITE = os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
_ALLOW_DELETE = os.environ.get("DATAVERSE_ALLOW_DELETE", "").lower() == "true"


def write_tool(**kwargs):
    """Register a tool only when DATAVERSE_ALLOW_WRITE=true.

    If the env var is not set, returns a no-op decorator so the function is
    defined but not exposed as an MCP tool.
    """
    if _ALLOW_WRITE:
        return mcp.tool(**kwargs)
    return lambda f: f


def delete_tool(**kwargs):
    """Register a tool only when DATAVERSE_ALLOW_DELETE=true.

    If the env var is not set, returns a no-op decorator so the function is
    defined but not exposed as an MCP tool.
    """
    if _ALLOW_DELETE:
        return mcp.tool(**kwargs)
    return lambda f: f
