"""Table and column metadata tools for the Dataverse MCP server."""

import asyncio
import json
import logging

from mcp.server.fastmcp import Context
from PowerPlatform.Dataverse.core.errors import DataverseError, HttpError

from dataverse_mcp._app import mcp
from dataverse_mcp.client import AppContext, get_dataverse_client
from dataverse_mcp.models import GetTableMetadataInput, ListTablesInput

logger = logging.getLogger(__name__)

_DEFAULT_TABLE_SELECT = [
    "LogicalName",
    "SchemaName",
    "DisplayName",
    "EntitySetName",
    "IsCustomEntity",
    "IsManaged",
]


def _get_client(ctx: Context, dataverse_url: str | None):
    """Resolve the DataverseClient for the requested environment."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    return get_dataverse_client(app_ctx, dataverse_url)


@mcp.tool(
    name="dataverse_list_tables",
    annotations={
        "title": "List Tables",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_list_tables(params: ListTablesInput, ctx: Context) -> str:
    """List available tables (entities) in the Dataverse environment.

    Returns table metadata including logical name, schema name, and display
    name. By default returns all non-private tables. Use filter to narrow
    results (e.g., "IsCustomEntity eq true" for custom tables only).

    Use this tool to discover which tables exist before querying them with
    dataverse_query_table or inspecting their schema with
    dataverse_get_table_metadata.
    """
    select = params.select or _DEFAULT_TABLE_SELECT

    try:
        client = _get_client(ctx, params.dataverse_url)

        def _query():
            return client.tables.list(
                filter=params.filter,
                select=select,
            )

        tables = await asyncio.to_thread(_query)
        return json.dumps({
            "tables": tables,
            "count": len(tables),
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
        logger.exception("Unexpected error in dataverse_list_tables")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_table_metadata",
    annotations={
        "title": "Get Table Metadata",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_table_metadata(
    params: GetTableMetadataInput, ctx: Context
) -> str:
    """Get detailed metadata for a specific Dataverse table.

    Returns the table's schema name, logical name, entity set name,
    primary key attribute, and primary name attribute. Use this to
    understand a table's structure before querying it with
    dataverse_query_table.
    """
    try:
        client = _get_client(ctx, params.dataverse_url)

        def _query():
            return client.tables.get(params.table_name)

        info = await asyncio.to_thread(_query)

        if info is None:
            return json.dumps({
                "error": True,
                "message": f"Table not found: '{params.table_name}'",
            })

        return json.dumps({
            "table": {
                "logical_name": info.logical_name,
                "schema_name": info.schema_name,
                "entity_set_name": info.entity_set_name,
                "metadata_id": info.metadata_id,
                "primary_id_attribute": info.primary_id_attribute,
                "primary_name_attribute": info.primary_name_attribute,
            },
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
        logger.exception("Unexpected error in dataverse_get_table_metadata")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
