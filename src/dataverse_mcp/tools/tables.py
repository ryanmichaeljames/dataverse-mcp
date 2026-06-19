"""Table query tools for the Dataverse MCP server."""

import json
import logging
import os
import uuid
from urllib.parse import urlencode

from mcp.server.fastmcp import Context

from dataverse_mcp._app import delete_tool, mcp, write_tool
from dataverse_mcp.batch import build_batch_body, parse_batch_response
from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    build_headers,
    finalize_response,
    get_app_ctx,
    paginate_records,
    request_with_retry,
    resolve_base_url,
    tool_error_response,
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

_DEFAULT_RECORD_SELECT = ["createdon", "modifiedon"]

# $batch requests bundle many operations and need longer than the client default.
_BATCH_TIMEOUT = 120.0


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
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    top = params.top
    entity_set = params.entity_set_name
    query_params: dict[str, str] = {"$top": str(top)}
    select = params.select or _DEFAULT_RECORD_SELECT
    query_params["$select"] = ",".join(select)
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
            count_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}/$count"
            count_headers = await build_headers(app_ctx, base_url)
            if params.filter:
                count_params = {"$filter": params.filter, "$count": "true", "$top": "1"}
                count_url = (
                    f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}?"
                    f"{urlencode(count_params, safe='$,')}"
                )
                count_resp = await request_with_retry(app_ctx.http_client, "GET", count_url, headers=count_headers)
                count_resp.raise_for_status()
                count_body = count_resp.json()
                result["total_count"] = int(count_body.get("@odata.count", 0))
            else:
                count_resp = await request_with_retry(app_ctx.http_client, "GET", count_url, headers=count_headers)
                count_resp.raise_for_status()
                result["total_count"] = int(count_resp.text.strip().lstrip("\ufeff"))
        return finalize_response(result)
    except Exception as e:
        return tool_error_response(e, "dataverse_query_table")


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
    Returns selected columns for the given table
    and record GUID. Use dataverse_query_table first to find record IDs.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    entity_set = params.entity_set_name
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}({params.record_id})"
    select = params.select or _DEFAULT_RECORD_SELECT
    url += f"?$select={','.join(select)}"

    extra_headers: dict[str, str] = {}
    if params.include_formatted_values:
        extra_headers["Prefer"] = (
            'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
        )

    try:
        headers = await build_headers(app_ctx, base_url, extra=extra_headers or None)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        record = resp.json()
        record.pop("@odata.context", None)
        return finalize_response({"record": record})
    except Exception as e:
        return tool_error_response(e, "dataverse_get_record")


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
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
            resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            total = body.get("@odata.count", 0)
        else:
            url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}/$count"
            resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
            resp.raise_for_status()
            total = int(resp.text.strip().lstrip("\ufeff"))
        return json.dumps({
            "total_count": total,
            "capped": total >= 5000,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_count_records")


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
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        records = body.get("value", [])
        return finalize_response({
            "records": records,
            "count": len(records),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_aggregate_table")


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
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        response = await request_with_retry(app_ctx.http_client, "POST", url, headers=headers, json=body)
        response.raise_for_status()
        return json.dumps({"success": True})
    except Exception as e:
        return tool_error_response(e, "dataverse_associate_records")


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
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{params.entity_set_name}({params.record_id})"
        f"/{params.navigation_property}({params.related_record_id})/$ref"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(app_ctx.http_client, "DELETE", url, headers=headers)
        response.raise_for_status()
        return json.dumps({"success": True})
    except Exception as e:
        return tool_error_response(e, "dataverse_disassociate_records")


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
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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
        response = await request_with_retry(app_ctx.http_client, "POST", url, headers=headers, json=body)
        response.raise_for_status()
        return json.dumps({"success": True})
    except Exception as e:
        return tool_error_response(e, "dataverse_merge_records")


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
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
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

    batch_boundary = f"batch_{uuid.uuid4().hex}"
    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/$batch"

    try:
        batch_body = build_batch_body(params.operations, base_url, batch_boundary)
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
        if params.continue_on_error:
            req_headers["Prefer"] = "odata.continue-on-error"

        response = await request_with_retry(app_ctx.http_client, "POST",
            url, headers=req_headers, content=batch_body_bytes, timeout=_BATCH_TIMEOUT
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

        results = parse_batch_response(response.text, resp_boundary)
        indexed = [{"index": i, **r} for i, r in enumerate(results)]
        for item in indexed:
            logger.debug(
                "Batch result[%d]: status=%s", item["index"], item.get("status_code")
            )
        return finalize_response({
            "results": indexed,
            "count": len(indexed),
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_execute_batch")
