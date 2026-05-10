"""Table query tools for the Dataverse MCP server."""

import asyncio
import json
import logging

import httpx
from mcp.server.fastmcp import Context
from PowerPlatform.Dataverse.core.errors import DataverseError, HttpError

from dataverse_mcp._app import mcp
from dataverse_mcp.client import AppContext, get_bearer_token, get_dataverse_client
from dataverse_mcp.models import AssociateRecordsInput, DisassociateRecordsInput, GetRecordInput, QueryTableInput

logger = logging.getLogger(__name__)

_DATAVERSE_API_VERSION = "v9.2"


def _get_client(ctx: Context, dataverse_url: str | None):
    """Resolve the DataverseClient for the requested environment."""
    app_ctx: AppContext = ctx.request_context.lifespan_context
    return get_dataverse_client(app_ctx, dataverse_url)


def _resolve_base_url(app_ctx: AppContext, dataverse_url: str | None) -> str | None:
    """Resolve the Dataverse base URL from input or configured fallback."""
    base_url = dataverse_url or app_ctx.fallback_dataverse_url
    if not base_url:
        return None
    return base_url.rstrip("/")


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
    top = params.top

    try:
        client = _get_client(ctx, params.dataverse_url)

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
    try:
        client = _get_client(ctx, params.dataverse_url)

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


# ---------------------------------------------------------------------------
# Record association write tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="dataverse_associate_records",
    annotations={
        "title": "Associate Records",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_associate_records(
    params: AssociateRecordsInput, ctx: Context
) -> str:
    """Create an association between two records via a collection-valued navigation property.

    Sends POST /{entity_set_name}({record_id})/{navigation_property}/$ref with
    {"@odata.id": "<absolute_uri>"} to link the related record.

    Use dataverse_list_relationships to discover the correct navigation_property name.
    Use dataverse_get_entity_sets to resolve entity set names.

    Set allow_write=False (default) to preview the request payload and URL without
    executing it. Set allow_write=True to perform the association.

    Returns {"success": true} on success (HTTP 204), or a preview object when
    allow_write=False.
    """
    app_ctx: AppContext = ctx.request_context.lifespan_context
    base_url = _resolve_base_url(app_ctx, params.dataverse_url)
    if not base_url:
        return json.dumps({
            "error": True,
            "message": (
                "No Dataverse environment URL was provided. Supply dataverse_url "
                "on the tool input, or set DATAVERSE_URL as a fallback."
            ),
        })

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{params.entity_set_name}({params.record_id})"
        f"/{params.navigation_property}/$ref"
    )
    related_uri = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{params.related_entity_set_name}({params.related_record_id})"
    )
    body = {"@odata.id": related_uri}

    if not params.allow_write:
        return json.dumps({
            "preview": True,
            "method": "POST",
            "url": url,
            "body": body,
            "message": "Set allow_write=True to execute this association.",
        })

    try:
        token = await asyncio.to_thread(
            get_bearer_token,
            app_ctx,
            f"{base_url}/.default",
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }

        def _post():
            with httpx.Client(timeout=30) as client:
                return client.post(url, headers=headers, json=body)

        response = await asyncio.to_thread(_post)
        if response.status_code == 204:
            return json.dumps({"success": True})
        try:
            err = response.json()
        except Exception:
            err = response.text
        return json.dumps({
            "error": True,
            "message": f"HTTP {response.status_code}: {err}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_associate_records")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_disassociate_records",
    annotations={
        "title": "Disassociate Records",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_disassociate_records(
    params: DisassociateRecordsInput, ctx: Context
) -> str:
    """Remove an association between two records via a collection-valued navigation property.

    Sends DELETE /{entity_set_name}({record_id})/{navigation_property}({related_record_id})/$ref
    to unlink the related record.

    Set allow_delete=False (default) to preview the URL without executing it.
    Set allow_delete=True to perform the disassociation.

    Returns {"success": true} on success (HTTP 204), or a preview object when
    allow_delete=False.
    """
    app_ctx: AppContext = ctx.request_context.lifespan_context
    base_url = _resolve_base_url(app_ctx, params.dataverse_url)
    if not base_url:
        return json.dumps({
            "error": True,
            "message": (
                "No Dataverse environment URL was provided. Supply dataverse_url "
                "on the tool input, or set DATAVERSE_URL as a fallback."
            ),
        })

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{params.entity_set_name}({params.record_id})"
        f"/{params.navigation_property}({params.related_record_id})/$ref"
    )

    if not params.allow_delete:
        return json.dumps({
            "preview": True,
            "method": "DELETE",
            "url": url,
            "message": "Set allow_delete=True to execute this disassociation.",
        })

    try:
        token = await asyncio.to_thread(
            get_bearer_token,
            app_ctx,
            f"{base_url}/.default",
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }

        def _delete():
            with httpx.Client(timeout=30) as client:
                return client.delete(url, headers=headers)

        response = await asyncio.to_thread(_delete)
        if response.status_code == 204:
            return json.dumps({"success": True})
        try:
            err = response.json()
        except Exception:
            err = response.text
        return json.dumps({
            "error": True,
            "message": f"HTTP {response.status_code}: {err}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_disassociate_records")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
