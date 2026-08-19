"""MCPServer application instance.

This module exists to avoid circular imports between server.py and tool
modules.  Tool modules import ``mcp`` from here; server.py imports ``mcp``
from here and registers tool modules.
"""

import logging
import os

from mcp.server.mcpserver import MCPServer

from dataverse_mcp.client import dataverse_lifespan

logger = logging.getLogger(__name__)

mcp = MCPServer(
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
    "customapis",
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


TOOL_CATEGORIES: dict[str, str] = {}
"""Maps tool function name -> category token.

Populated at decoration time by :func:`category_tools`, *regardless* of whether
the env gate for that tool is open.  The MCP registry only ever contains the
tools whose gates are open, so it cannot answer "which category does this tool
belong to?" for a gated-off tool; this mapping can.  Read-only bookkeeping —
it has no effect on which tools are exposed.  Used by
``scripts/gen_wiki_tools.py`` to group tools onto wiki pages.
"""


def category_tools(category: str):
    """Return (tool, write_tool, delete_tool) decorators scoped to *category*.

    Each decorator composes category gating with the existing write/delete env
    flags.  When a gate is closed the decorator is a no-op (the function is
    defined but not exposed as an MCP tool).  Either way the function's
    category is recorded in :data:`TOOL_CATEGORIES`.
    """
    def _gated(is_open: bool, kwargs: dict):
        register = mcp.tool(**kwargs) if is_open else None

        def decorate(func):
            TOOL_CATEGORIES[func.__name__] = category
            return register(func) if register is not None else func

        return decorate

    def tool(**kwargs):
        return _gated(_category_enabled(category), kwargs)

    def write_tool(**kwargs):
        return _gated(_ALLOW_WRITE and _category_enabled(category), kwargs)

    def delete_tool(**kwargs):
        return _gated(_ALLOW_DELETE and _category_enabled(category), kwargs)

    return tool, write_tool, delete_tool
