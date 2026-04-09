"""Table query tools for the Dataverse MCP server."""

import asyncio
import json
import logging

from mcp.server.fastmcp import Context
from PowerPlatform.Dataverse.core.errors import DataverseError, HttpError

from dataverse_mcp._app import mcp
from dataverse_mcp.client import AppContext
from dataverse_mcp.models import GetRecordInput, QueryTableInput

logger = logging.getLogger(__name__)


def _get_client(ctx: Context):
    """Extract the DataverseClient from the FastMCP lifespan context."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    return app_ctx.client


def _flatten_records(pages, limit: int) -> list[dict]:
    """Flatten paginated Record results into a list of dicts, up to limit."""
    records = []
    for page in pages:
        for record in page:
            records.append(dict(record))
            if len(records) >= limit:
                return records
    return records


@mcp.tool(
    name="dataverse_query_table",
    annotations={
        "title": "Query Table",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_query_table(params: QueryTableInput, ctx: Context) -> str:
    """Query records from any Dataverse table.

    Returns matching records from the specified table. Supports OData-style
    filtering, column selection, sorting, and navigation property expansion.

    Always specify select to limit returned columns and reduce payload size.
    Default top is 50 to prevent overwhelming context — increase if needed.

    Use dataverse_list_tables or dataverse_get_table_metadata first to
    discover available tables and their column names.
    """
    client = _get_client(ctx)
    top = params.top

    try:

        def _query():
            pages = client.records.get(
                params.table_name,
                select=params.select,
                filter=params.filter,
                orderby=params.orderby,
                top=top,
                expand=params.expand,
            )
            return _flatten_records(pages, top)

        records = await asyncio.to_thread(_query)
        return json.dumps({
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
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
        logger.exception("Unexpected error in dataverse_query_table")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_get_record",
    annotations={
        "title": "Get Record",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_record(params: GetRecordInput, ctx: Context) -> str:
    """Retrieve a single record by its ID from any Dataverse table.

    Returns the full record (or selected columns) for the given table
    and record GUID. Use dataverse_query_table first to find record IDs.
    """
    client = _get_client(ctx)

    try:

        def _query():
            record = client.records.get(
                params.table_name,
                record_id=params.record_id,
                select=params.select,
            )
            return dict(record)

        record = await asyncio.to_thread(_query)
        return json.dumps({"record": record})
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
        logger.exception("Unexpected error in dataverse_get_record")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
