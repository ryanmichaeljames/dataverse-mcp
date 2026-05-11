"""Table query tools for the Dataverse MCP server."""

import asyncio
import json
import logging
import os

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


def _build_inner_request(method: str, url: str, body: dict | None) -> str:
    """Build the inner HTTP/1.1 request string for a single batch operation.

    Includes Content-Type and Content-Length only for write operations
    (POST, PUT, PATCH) that carry a body. GET and DELETE requests are always
    serialized without a body to remain OData-compliant regardless of the
    ``body`` argument.
    """
    inner = f"{method} {url} HTTP/1.1\r\nAccept: application/json\r\n"
    if method.upper() in ("POST", "PUT", "PATCH") and body is not None:
        body_bytes = json.dumps(body).encode("utf-8")
        inner += f"Content-Type: application/json\r\nContent-Length: {len(body_bytes)}\r\n\r\n"
        inner += body_bytes.decode("utf-8")
    else:
        inner += "\r\n"
    return inner


def _build_batch_body(operations: list, base_url: str, batch_boundary: str) -> str:
    """Build an OData-compliant multipart/mixed batch request body.

    Change set operations include a ``Content-ID`` header (required by
    Dataverse) and all write operations carry ``Content-Length`` so the
    server can parse the inner request body stream correctly.
    """
    parts: list[str] = []

    ops = list(operations)
    processed_change_sets: set[str] = set()
    i = 0

    while i < len(ops):
        op = ops[i]
        if op.change_set_id and op.change_set_id not in processed_change_sets:
            cs_id = op.change_set_id
            cs_boundary = f"changeset_{cs_id}"
            processed_change_sets.add(cs_id)

            cs_ops = [o for o in ops if o.change_set_id == cs_id]
            cs_parts: list[str] = []
            for cs_idx, cs_op in enumerate(cs_ops):
                inner_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}{cs_op.url}"
                inner = _build_inner_request(cs_op.method, inner_url, cs_op.body)
                # Content-ID is required by OData for every change set part
                op_part = (
                    f"Content-Type: application/http\r\n"
                    f"Content-Transfer-Encoding: binary\r\n"
                    f"Content-ID: {cs_idx + 1}\r\n"
                    f"\r\n"
                    f"{inner}"
                )
                logger.debug(
                    "Batch change set op %d/%d [%s]: %s %s",
                    cs_idx + 1, len(cs_ops), cs_id, cs_op.method, inner_url,
                )
                cs_parts.append(op_part)

            cs_body = f"\r\n--{cs_boundary}\r\n".join(cs_parts)
            part = (
                f"Content-Type: multipart/mixed; boundary={cs_boundary}\r\n"
                f"\r\n"
                f"--{cs_boundary}\r\n{cs_body}\r\n--{cs_boundary}--"
            )
            parts.append(part)
        elif not op.change_set_id:
            inner_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}{op.url}"
            inner = _build_inner_request(op.method, inner_url, op.body)
            part = (
                f"Content-Type: application/http\r\n"
                f"Content-Transfer-Encoding: binary\r\n"
                f"\r\n"
                f"{inner}"
            )
            logger.debug("Batch standalone op: %s %s", op.method, inner_url)
            parts.append(part)
        i += 1

    logger.debug(
        "Building batch body: boundary=%s, parts=%d", batch_boundary, len(parts)
    )
    body = (
        f"--{batch_boundary}\r\n"
        + f"\r\n--{batch_boundary}\r\n".join(parts)
        + f"\r\n--{batch_boundary}--"
    )
    return body


def _parse_batch_response(response_text: str, boundary: str) -> list[dict]:
    """Parse a multipart/mixed batch response into a list of per-operation results."""
    results: list[dict] = []
    parts = response_text.split(f"--{boundary}")

    for part in parts:
        part = part.strip()
        if not part or part == "--":
            continue

        # Split part headers from body on the first blank line
        if "\r\n\r\n" in part:
            part_headers, part_body = part.split("\r\n\r\n", 1)
        else:
            part_headers = part
            part_body = ""

        # Check if this is a change set response (nested multipart) — inspect
        # only the part's own headers to avoid false positives in the body.
        if "multipart/mixed" in part_headers:
            inner_boundary_match = part_headers.find("boundary=")
            if inner_boundary_match != -1:
                inner_boundary = (
                    part_headers[inner_boundary_match + 9:]
                    .split("\r\n")[0]
                    .split(";")[0]
                    .strip()
                )
                inner_results = _parse_batch_response(part_body, inner_boundary)
                results.extend(inner_results)
            continue

        # Find the HTTP response status line in the part body
        lines = part_body.split("\r\n")
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

        # Skip response headers after the status line
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


@mcp.tool(
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
    if has_mutations:
        write_enabled = os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
        if not write_enabled:
            return json.dumps({
                "error": True,
                "message": (
                    "Batch operations containing non-GET methods require "
                    "DATAVERSE_ALLOW_WRITE=true in the MCP server environment."
                ),
            })

    batch_boundary = "batch_dataverse_mcp"
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/$batch"

    try:
        batch_body = _build_batch_body(params.operations, base_url, batch_boundary)
        batch_body_bytes = batch_body.encode("utf-8")
        logger.debug(
            "Executing batch: url=%s boundary=%s operations=%d bytes=%d",
            url, batch_boundary, len(params.operations), len(batch_body_bytes),
        )
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
                return client.post(url, headers=req_headers, content=batch_body_bytes)

        response = await asyncio.to_thread(_post)
        logger.debug(
            "Batch response: status=%d content_type=%s",
            response.status_code, response.headers.get("Content-Type", ""),
        )

        if response.status_code not in (200, 202):
            try:
                err = response.json()
            except Exception:
                err = response.text
            logger.error(
                "Batch request failed: status=%d error=%s", response.status_code, err
            )
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
        for item in indexed:
            logger.debug(
                "Batch result[%d]: status=%s", item["index"], item.get("status_code")
            )
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
