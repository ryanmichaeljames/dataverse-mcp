"""FastMCP server for Dataverse MCP tools."""

import logging
import os
import pathlib
import sys

# Load .env file if present (local development / mcp dev / mcp inspector)
_env_file = pathlib.Path(__file__).parent.parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# Configure logging to stderr (stdout reserved for stdio transport)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)

from dataverse_mcp._app import mcp  # noqa: E402

# Import tool modules to trigger @mcp.tool() registration
import dataverse_mcp.tools.solutions  # noqa: E402, F401
import dataverse_mcp.tools.tables  # noqa: E402, F401
import dataverse_mcp.tools.metadata  # noqa: E402, F401
import dataverse_mcp.tools.dependencies  # noqa: E402, F401


def main():
    """Entry point for the Dataverse MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
