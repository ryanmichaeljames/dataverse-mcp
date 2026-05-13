"""Table query tools for the Dataverse MCP server."""

import json
import logging
import os
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import Context

from dataverse_mcp._app import delete_tool, mcp, write_tool
from dataverse_mcp.client import (
    AppContext,
    _DATAVERSE_API_VERSION,
    build_headers,
    extract_error_message,
    paginate_records,
    resolve_base_url,
)
from dataverse_mcp.models import (
    AggregateTableInput,
    AssociateRecordsInput,
    CountRecordsInput,
    DisassociateRecordsInput,
    ExecuteBatchInput,
    GetRecordInput,
    MergeRecordsInput,
    QueryTableInput,
)

logger = logging.getLogger(__name__)


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
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    top = params.top
    entity_set = params.entity_set_name
    query_params: dict[str, str] = {"$top": str(top)}
    if params.select:
        query_params["$select"] = ",".join(params.select)
    if params.filter:
        query_params["$filter"] = params.filter
    if params.orderby:
        query_params["$orderby"] = ",".join(params.orderby)
    if params.expand:
        query_params["$expand"] = ",".join(params.expand)

    full_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}?"
        f"{urlencode(query_params, safe='$,')}"
    )

    extra_headers: dict[str, str] = {}
    if params.include_formatted_values:
        extra_headers["Prefer"] = (
            'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
        )

    try:
        headers = await build_headers(app_ctx, base_url, extra=extra_headers or None)
        records = await paginate_records(full_url, headers, top, app_ctx.http_client)
        result: dict = {
            "records": records,
            "count": len(records),
            "has_more": len(records) >= top,
        }
        if params.count:
            count_params: dict[str, str] = {}
            if params.filter:
                count_params["$filter"] = params.filter
            count_url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}/$count"
            )
            if count_params:
                count_url += f"?{urlencode(count_params, safe='$,')}"
            count_headers = await build_headers(app_ctx, base_url)
            count_resp = await app_ctx.http_client.get(count_url, headers=count_headers)
            count_resp.raise_for_status()
            result["total_count"] = int(count_resp.text.strip().lstrip("\ufeff"))
        return json.dumps(result)
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
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
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    entity_set = params.entity_set_name
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}({params.record_id})"
    if params.select:
        url += f"?$select={','.join(params.select)}"

    extra_headers: dict[str, str] = {}
    if params.include_formatted_values:
        extra_headers["Prefer"] = (
            'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
        )

    try:
        headers = await build_headers(app_ctx, base_url, extra=extra_headers or None)
        resp = await app_ctx.http_client.get(url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return json.dumps({"record": record})
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_get_record")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_count_records",
    annotations={
        "title": "Count Records",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_count_records(params: CountRecordsInput, ctx: Context) -> str:
    """Count records in a Dataverse table, optionally filtered.

    Returns an integer count. Counts are capped at 5,000 by Dataverse —
    if total_count equals 5000 the actual count may be higher.

    Use filter to narrow the count to matching records, e.g.,
    "statecode eq 0" to count only active records.
    """
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    entity_set = params.entity_set_name

    try:
        headers = await build_headers(app_ctx, base_url)
        if params.filter:
            # Dataverse does not support $filter on the /$count path; use
            # ?$filter=...&$count=true to get @odata.count from the collection
            query_params: dict[str, str] = {"$filter": params.filter, "$count": "true", "$top": "1"}
            url = (
                f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}?"
                f"{urlencode(query_params, safe='$,')}"
            )
            resp = await app_ctx.http_client.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            total = body.get("@odata.count", 0)
        else:
            url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}/$count"
            resp = await app_ctx.http_client.get(url, headers=headers)
            resp.raise_for_status()
            total = int(resp.text.strip().lstrip("\ufeff"))
        return json.dumps({
            "total_count": total,
            "capped": total >= 5000,
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_count_records")
        return json.dumps({
            "error": True,
            "message": f"Unexpected error: {type(e).__name__}: {e}",
        })


@mcp.tool(
    name="dataverse_aggregate_table",
    annotations={
        "title": "Aggregate Table",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_aggregate_table(params: AggregateTableInput, ctx: Context) -> str:
    """Aggregate data from a Dataverse table using OData $apply expressions.

    Supports groupby, sum, avg, min, max, countdistinct, and distinct value queries.
    Works on up to 50,000 records.

    Common patterns:
    - Count by status:     groupby((statecode),aggregate($count as total))
    - Count distinct IDs:  groupby((statecode),aggregate(accountid with countdistinct as total))
    - Sum a column:        aggregate(revenue with sum as total_revenue)
    - Avg/min/max:         aggregate(numberofemployees with avg as avg_employees)
    - Distinct values:     groupby((statuscode))
    - Total row count:     aggregate($count as total)
    - Filtered agg:        use the filter param to narrow before aggregation

    Note: use 'countdistinct' not 'count' for column aggregation.
    Note: $orderby on aggregate alias values is not supported by Dataverse.
    Note: Lookup fields (e.g. ownerid) cannot be used in groupby — use
    regular columns like statecode, statuscode, or other integer fields.
    """
    app_ctx: AppContext = ctx.request_context.lifespan_context
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    entity_set = params.entity_set_name
    query_params: dict[str, str] = {"$apply": params.apply}
    if params.filter:
        query_params["$filter"] = params.filter

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}?"
        f"{urlencode(query_params, safe='$,')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await app_ctx.http_client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        records = body.get("value", [])
        return json.dumps({
            "records": records,
            "count": len(records),
        })
    except httpx.HTTPStatusError as e:
        msg = extract_error_message(e.response)
        logger.error("Dataverse HTTP %d: %s", e.response.status_code, msg)
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    except Exception as e:
        logger.exception("Unexpected error in dataverse_aggregate_table")
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
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

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
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await app_ctx.http_client.post(url, headers=headers, json=body)
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
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{params.entity_set_name}({params.record_id})"
        f"/{params.navigation_property}({params.related_record_id})/$ref"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await app_ctx.http_client.delete(url, headers=headers)
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
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

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
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        response = await app_ctx.http_client.post(url, headers=headers, json=body)
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
    try:
        base_url = resolve_base_url(app_ctx, params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

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
        headers = await build_headers(app_ctx, base_url)
        req_headers = {
            **headers,
            "Content-Type": f"multipart/mixed; boundary={batch_boundary}",
            "Accept": "multipart/mixed",
        }
        del req_headers["If-None-Match"]
        if params.continue_on_error:
            req_headers["Prefer"] = "odata.continue-on-error"

        response = await app_ctx.http_client.post(
            url, headers=req_headers, content=batch_body_bytes, timeout=120
        )
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
