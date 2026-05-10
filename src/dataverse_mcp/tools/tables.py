"""Table query tools for the Dataverse MCP server."""

import asyncio
import json
import logging

import httpx
from mcp.server.fastmcp import Context
from PowerPlatform.Dataverse.core.errors import DataverseError, HttpError

from dataverse_mcp._app import delete_tool, mcp, write_tool
from dataverse_mcp.client import AppContext, get_bearer_token, get_dataverse_client
from dataverse_mcp.models import (
    AssociateRecordsInput,
    DisassociateRecordsInput,
    ExecuteBatchInput,
    GetRecordInput,
    MergeRecordsInput,
    QueryTableInput,
)

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


@write_tool(
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

    Links the related record by POSTing an @odata.id reference to the navigation property.

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


@delete_tool(
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

    Unlinks the related record by sending a DELETE to the navigation property $ref endpoint.

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


# ---------------------------------------------------------------------------
# Record merge and batch tools
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_merge_records",
    annotations={
        "title": "Merge Records",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_merge_records(params: MergeRecordsInput, ctx: Context) -> str:
    """Merge a subordinate record into a target record using the Dataverse Merge action.

    Supported entity types: account, contact, lead, incident.

    The subordinate record is deactivated (not deleted) after the merge.
    Use update_content to carry specific field values from the subordinate
    to the target.

    Set allow_write=False (default) to preview the request body without
    executing. Set allow_write=True to perform the merge.

    Returns {"success": true} on HTTP 204, or a preview object when
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

    entity_type = f"Microsoft.Dynamics.CRM.{params.entity_logical_name}"
    id_field = f"{params.entity_logical_name}id"

    body: dict = {
        "Target": {"@odata.type": entity_type, id_field: params.target_id},
        "Subordinate": {"@odata.type": entity_type, id_field: params.subordinate_id},
        "PerformParentingChecks": params.perform_parenting_checks,
    }
    if params.update_content:
        body["UpdateContent"] = {**params.update_content, "@odata.type": entity_type}

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/Merge"

    if not params.allow_write:
        return json.dumps({
            "preview": True,
            "method": "POST",
            "url": url,
            "body": body,
            "message": "Set allow_write=True to execute this merge.",
        })

    try:
        token = await asyncio.to_thread(get_bearer_token, app_ctx, f"{base_url}/.default")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }

        def _post():
            with httpx.Client(timeout=60) as client:
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
        logger.exception("Unexpected error in dataverse_merge_records")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


def _build_batch_body(operations: list, base_url: str, batch_boundary: str) -> str:
    """Build a multipart/mixed batch request body."""
    parts: list[str] = []

    # Group operations: change-set ops go into their own multipart part
    # Non-change-set ops are individual request parts
    # We need to interleave in order, so process sequentially
    i = 0
    ops = list(operations)
    processed_change_sets: set[str] = set()

    while i < len(ops):
        op = ops[i]
        if op.change_set_id and op.change_set_id not in processed_change_sets:
            cs_id = op.change_set_id
            cs_boundary = f"changeset_{cs_id}"
            processed_change_sets.add(cs_id)

            cs_ops = [o for o in ops if o.change_set_id == cs_id]
            cs_parts: list[str] = []
            for cs_op in cs_ops:
                op_headers = f"Content-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n"
                op_request = f"{cs_op.method} {base_url}/api/data/{_DATAVERSE_API_VERSION}{cs_op.url} HTTP/1.1\r\n"
                op_request += "Content-Type: application/json\r\n\r\n"
                if cs_op.body:
                    op_request += json.dumps(cs_op.body)
                cs_parts.append(f"{op_headers}\r\n{op_request}")

            cs_body = f"\r\n--{cs_boundary}\r\n".join(cs_parts)
            part = (
                f"Content-Type: multipart/mixed; boundary={cs_boundary}\r\n\r\n"
                f"--{cs_boundary}\r\n{cs_body}\r\n--{cs_boundary}--"
            )
            parts.append(part)
        elif not op.change_set_id:
            op_request = f"{op.method} {base_url}/api/data/{_DATAVERSE_API_VERSION}{op.url} HTTP/1.1\r\n"
            op_request += "Accept: application/json\r\nContent-Type: application/json\r\n\r\n"
            if op.body:
                op_request += json.dumps(op.body)
            part = f"Content-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n\r\n{op_request}"
            parts.append(part)
        i += 1

    body = f"--{batch_boundary}\r\n" + f"\r\n--{batch_boundary}\r\n".join(parts) + f"\r\n--{batch_boundary}--"
    return body


def _parse_batch_response(response_text: str, boundary: str) -> list[dict]:
    """Parse a multipart/mixed batch response into a list of per-operation results."""
    results: list[dict] = []
    parts = response_text.split(f"--{boundary}")

    for part in parts:
        part = part.strip()
        if not part or part == "--":
            continue

        # Check if this is a change set response (nested multipart)
        if "multipart/mixed" in part:
            inner_boundary_match = part.find("boundary=")
            if inner_boundary_match != -1:
                inner_boundary = part[inner_boundary_match + 9:].split("\r\n")[0].split(";")[0].strip()
                inner_results = _parse_batch_response(part, inner_boundary)
                results.extend(inner_results)
            continue

        # Find the HTTP response status line
        lines = part.split("\r\n")
        http_status_line = None
        body_start = 0
        for j, line in enumerate(lines):
            if line.startswith("HTTP/1.1"):
                http_status_line = line
                body_start = j + 1
                break

        if not http_status_line:
            continue

        try:
            status_code = int(http_status_line.split(" ")[1])
        except (IndexError, ValueError):
            status_code = 0

        # Skip header lines after status line
        while body_start < len(lines) and lines[body_start].strip():
            body_start += 1
        body_start += 1  # skip blank line

        body_text = "\r\n".join(lines[body_start:]).strip()
        body_json = None
        if body_text:
            try:
                body_json = json.loads(body_text)
            except Exception:
                body_json = body_text

        results.append({"status_code": status_code, "body": body_json})

    return results


@write_tool(
    name="dataverse_execute_batch",
    annotations={
        "title": "Execute Batch",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_execute_batch(params: ExecuteBatchInput, ctx: Context) -> str:
    """Execute multiple OData operations in a single HTTP request using the $batch endpoint.

    Supports up to 1,000 operations per request. Operations in the same
    change_set_id are executed atomically — if any fails, all in that set
    are rolled back.

    When allow_write=False (default) and the batch contains mutations (POST,
    PUT, PATCH, DELETE), returns a preview of the operations without executing.
    Read-only batches (GET only) are always executed regardless of allow_write.
    Set allow_write=True to execute mutation batches.

    Returns a list of per-operation results: [{index, status_code, body}].
    Change set results are flattened into the list in order.
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

    has_mutations = any(op.method != "GET" for op in params.operations)

    if not params.allow_write and has_mutations:
        preview_ops = [
            {
                "index": i,
                "method": op.method,
                "url": op.url,
                "has_body": op.body is not None,
                "change_set_id": op.change_set_id,
            }
            for i, op in enumerate(params.operations)
        ]
        return json.dumps({
            "preview": True,
            "operation_count": len(params.operations),
            "operations": preview_ops,
            "message": "Set allow_write=True to execute this batch.",
        })

    batch_boundary = "batch_dataverse_mcp"
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/$batch"

    try:
        batch_body = _build_batch_body(params.operations, base_url, batch_boundary)
        token = await asyncio.to_thread(get_bearer_token, app_ctx, f"{base_url}/.default")
        req_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/mixed; boundary={batch_boundary}",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Accept": "multipart/mixed",
        }
        if params.continue_on_error:
            req_headers["Prefer"] = "odata.continue-on-error"

        def _post():
            with httpx.Client(timeout=120) as client:
                return client.post(url, headers=req_headers, content=batch_body.encode("utf-8"))

        response = await asyncio.to_thread(_post)

        if response.status_code not in (200, 202):
            try:
                err = response.json()
            except Exception:
                err = response.text
            return json.dumps({
                "error": True,
                "message": f"HTTP {response.status_code}: {err}",
            })

        # Parse response boundary from Content-Type header
        content_type = response.headers.get("Content-Type", "")
        resp_boundary = batch_boundary
        if "boundary=" in content_type:
            resp_boundary = content_type.split("boundary=")[1].split(";")[0].strip()

        results = _parse_batch_response(response.text, resp_boundary)
        indexed = [{"index": i, **r} for i, r in enumerate(results)]
        return json.dumps({
            "results": indexed,
            "count": len(indexed),
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_execute_batch")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })
