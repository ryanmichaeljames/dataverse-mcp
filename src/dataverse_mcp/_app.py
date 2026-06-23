"""FastMCP application instance.

This module exists to avoid circular imports between server.py and tool
modules.  Tool modules import ``mcp`` from here; server.py imports ``mcp``
from here and registers tool modules.
"""

import logging
import os

from mcp.server.fastmcp import FastMCP

from dataverse_mcp.client import dataverse_lifespan

logger = logging.getLogger(__name__)

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

_KNOWN_CATEGORIES = frozenset({
    "core", "schema", "solutions", "flows", "forms", "views", "apps",
    "connections", "variables", "plugins", "security", "jobs", "webresources",
})

_raw_tools_env = os.environ.get("DATAVERSE_TOOLS", "").strip()
if not _raw_tools_env:
    _ENABLED_CATEGORIES: frozenset[str] | None = None
    logger.info("DATAVERSE_TOOLS active categories: all")
else:
    _parsed: set[str] = set()
    for _token in _raw_tools_env.split(","):
        _token = _token.strip().lower()
        if not _token:
            continue
        if _token not in _KNOWN_CATEGORIES:
            logger.warning(
                "DATAVERSE_TOOLS: unknown category %r — ignored (known: %s)",
                _token,
                ", ".join(sorted(_KNOWN_CATEGORIES)),
            )
        else:
            _parsed.add(_token)
    _parsed.add("core")  # core is always on
    _ENABLED_CATEGORIES = frozenset(_parsed)
    logger.info("DATAVERSE_TOOLS active categories: %s", ", ".join(sorted(_ENABLED_CATEGORIES)))


def _category_enabled(category: str) -> bool:
    """Return True when the given category is active."""
    return _ENABLED_CATEGORIES is None or category in _ENABLED_CATEGORIES


def category_tools(category: str):
    """Return (tool, write_tool, delete_tool) decorators scoped to *category*.

    Each decorator composes category gating with the existing write/delete env
    flags.  When a gate is closed the decorator is a no-op (the function is
    defined but not exposed as an MCP tool).
    """
    def tool(**kwargs):
        return mcp.tool(**kwargs) if _category_enabled(category) else (lambda f: f)

    def write_tool(**kwargs):
        return mcp.tool(**kwargs) if (_ALLOW_WRITE and _category_enabled(category)) else (lambda f: f)

    def delete_tool(**kwargs):
        return mcp.tool(**kwargs) if (_ALLOW_DELETE and _category_enabled(category)) else (lambda f: f)

    return tool, write_tool, delete_tool
