"""FastMCP server for Dataverse MCP tools."""

import logging
import sys

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


def main():
    """Entry point for the Dataverse MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
