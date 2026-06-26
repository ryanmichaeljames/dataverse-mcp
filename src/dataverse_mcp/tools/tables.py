"""Table query tools for the Dataverse MCP server."""

import json
import logging
import os
import re
import uuid
from urllib.parse import urlencode

from mcp.server.fastmcp import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("core")
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
    BatchOperationItem,
    BulkUpsertInput,
    CountRecordsInput,
    CreateRecordInput,
    DeleteRecordInput,
    DisassociateRecordsInput,
    ExecuteBatchInput,
    ExecuteFetchXmlInput,
    GetRecordInput,
    MergeRecordsInput,
    QueryTableInput,
    UpdateRecordInput,
)

logger = logging.getLogger(__name__)

_DEFAULT_RECORD_SELECT = ["createdon", "modifiedon"]

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# $batch requests bundle many operations and need longer than the client default.
_BATCH_TIMEOUT = 120.0


@tool(
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
    """Query records from a Dataverse table with OData filtering, ordering, and expansion.

    For a single record by GUID use dataverse_get_record. For just a count use
    dataverse_count_records. For group-by aggregation use dataverse_aggregate_table.
    To create, update, or delete records use dataverse_create_record,
    dataverse_update_record, or dataverse_delete_record.

    Always specify select to limit returned columns and keep payloads small.
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


@tool(
    name="dataverse_execute_fetchxml",
    annotations={
        "title": "Execute FetchXML",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_execute_fetchxml(params: ExecuteFetchXmlInput, ctx: Context) -> str:
    """Execute a FetchXML query against a Dataverse table and return matching records.

    FetchXML supports complex joins (link-entity), aggregation, and queries that
    OData $filter cannot express. Use dataverse_query_table for simple OData queries.
    Use dataverse_get_entity_sets to discover entity_set_name; the entity_set_name
    must match the root <entity name="..."> logical name's collection name.

    FetchXML uses paging cookies (not @odata.nextLink). This tool returns one page
    plus paging metadata (has_more, paging_cookie) so the caller can page if needed.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    entity_set = params.entity_set_name
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}?"
        f"{urlencode({'fetchXml': params.fetch_xml}, safe='')}"
    )

    extra_headers: dict[str, str] = {}
    if params.include_formatted_values:
        extra_headers["Prefer"] = (
            'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
        )

    try:
        headers = await build_headers(app_ctx, base_url, extra=extra_headers or None)
        resp = await request_with_retry(app_ctx.http_client, "GET", url, headers=headers)
        resp.raise_for_status()
        body = resp.json()

        records = body.get("value", [])
        # Remove top-level @odata.context but preserve per-record @odata.etag
        body.pop("@odata.context", None)

        # FetchXML-over-OData signals more pages via the presence of a paging
        # cookie (@Microsoft.Dynamics.CRM.fetchxmlpagingcookie); there is no
        # "morerecords" annotation in the Web API response. The total count, when
        # requested via returntotalrecordcount="true", comes back as @odata.count.
        paging_cookie = body.get("@Microsoft.Dynamics.CRM.fetchxmlpagingcookie")

        result: dict = {
            "records": records,
            "count": len(records),
            "has_more": paging_cookie is not None,
        }

        total_record_count = body.get("@odata.count")
        if total_record_count is not None and total_record_count >= 0:
            result["total_record_count"] = total_record_count

        if paging_cookie is not None:
            result["paging_cookie"] = paging_cookie

        return finalize_response(result)
    except Exception as e:
        return tool_error_response(e, "dataverse_execute_fetchxml")


@tool(
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
    """Retrieve a single Dataverse record by its GUID.

    For multiple records with filtering use dataverse_query_table.
    Use dataverse_query_table first to find record IDs if you do not have one.
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


# ---------------------------------------------------------------------------
# Record CRUD write tools
# ---------------------------------------------------------------------------


@write_tool(
    name="dataverse_create_record",
    annotations={
        "title": "Create Record",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def dataverse_create_record(params: CreateRecordInput, ctx: Context) -> str:
    """Create a single record in any Dataverse table and return the new record's id.

    For updating an existing record use dataverse_update_record. For bulk or atomic
    multi-operation writes use dataverse_execute_batch. Requires DATAVERSE_ALLOW_WRITE=true.
    Use dataverse_get_entity_sets to discover entity_set_name; use
    dataverse_list_columns to discover column names.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{params.entity_set_name}"

    try:
        # A plain create returns 204 + the OData-EntityId header (the new record
        # URI). We do NOT request return=representation: Dataverse omits the
        # OData-EntityId header when the entity body is returned, which would
        # leave us without a reliable, entity-agnostic source for the new id.
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client, "POST", url, headers=headers, json=params.data
        )
        resp.raise_for_status()

        # Extract the new record GUID from the OData-EntityId header.
        entity_id_header = resp.headers.get("OData-EntityId", "")
        m = _GUID_RE.search(entity_id_header)
        if not m:
            return json.dumps({
                "error": True,
                "message": (
                    "Record created but the new id could not be read from the "
                    "OData-EntityId response header."
                ),
            })
        new_id = m.group(0)
        logger.info("Created record in %s: id=%s", params.entity_set_name, new_id)
        return finalize_response({"created": True, "id": new_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_create_record")


@write_tool(
    name="dataverse_update_record",
    annotations={
        "title": "Update Record",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_update_record(params: UpdateRecordInput, ctx: Context) -> str:
    """Partially update a single Dataverse record via PATCH — only supplied columns change.

    For creating a new record use dataverse_create_record. For bulk or atomic
    multi-operation writes use dataverse_execute_batch. Requires DATAVERSE_ALLOW_WRITE=true.
    Unlike metadata tools, this is a PATCH partial update — no full definition needed.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{params.entity_set_name}({params.record_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)
        resp = await request_with_retry(
            app_ctx.http_client, "PATCH", url, headers=headers, json=params.data
        )
        resp.raise_for_status()
        logger.info(
            "Updated record %s in %s", params.record_id, params.entity_set_name
        )
        return finalize_response({"updated": True, "id": params.record_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_update_record")


@delete_tool(
    name="dataverse_delete_record",
    annotations={
        "title": "Delete Record",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_delete_record(params: DeleteRecordInput, ctx: Context) -> str:
    """Permanently delete a single Dataverse record by GUID — this action cannot be undone.

    Requires DATAVERSE_ALLOW_DELETE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/{params.entity_set_name}({params.record_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        resp = await request_with_retry(
            app_ctx.http_client, "DELETE", url, headers=headers
        )
        resp.raise_for_status()
        logger.info(
            "Deleted record %s from %s", params.record_id, params.entity_set_name
        )
        return finalize_response({"deleted": True, "id": params.record_id})
    except Exception as e:
        return tool_error_response(e, "dataverse_delete_record")


@tool(
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
    """Count records in a table (optionally filtered) and return only the integer total.

    Use this instead of dataverse_query_table when you need a number, not rows.
    For per-group counts (e.g. count by status) use dataverse_aggregate_table.
    The total is capped at 5,000 by Dataverse.
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


@tool(
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
    """Group and aggregate Dataverse records with an OData $apply expression.

    Use this for per-group questions (e.g. count by status, sum revenue by region).
    For a single total count use dataverse_count_records; for raw rows use dataverse_query_table.
    Works on up to 50,000 records. See the apply parameter for expression examples.
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
    """Associate two Dataverse records via a collection-valued navigation property.

    Navigation property names are case-sensitive — use dataverse_list_relationships
    to discover the correct name. For the reverse operation use dataverse_disassociate_records.
    Requires DATAVERSE_ALLOW_WRITE=true.
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
    """Remove an existing association between two Dataverse records.

    Navigation property names are case-sensitive — use dataverse_list_relationships
    to discover the correct name. Requires DATAVERSE_ALLOW_DELETE=true.
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
    """Merge a subordinate record into a target record for account, contact, lead, or incident.

    The subordinate record is deactivated (not deleted) after the merge.
    Use update_content to carry specific field values from the subordinate to the target.
    Requires DATAVERSE_ALLOW_WRITE=true.
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


@tool(
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
    """Execute bulk or atomic multi-operation reads and writes via the OData $batch endpoint.

    Use this for bulk record operations or when multiple writes must succeed or fail together.
    For single-record writes use dataverse_create_record / dataverse_update_record /
    dataverse_delete_record instead. For metadata/schema changes use the
    dataverse_create_*/update_*/delete_* metadata tools.

    Non-GET operations require DATAVERSE_ALLOW_WRITE=true. Group operations with the same
    change_set_id to run them atomically (all-or-nothing, up to 1,000 operations per request).
    Returns per-operation results [{index, status_code, body}].
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


# ---------------------------------------------------------------------------
# Bulk upsert
# ---------------------------------------------------------------------------

_GUID_RE_FULL = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _build_upsert_url(
    entity_set_name: str,
    record: dict,
    key_columns: list[str] | None,
) -> tuple[str, dict]:
    """Return (relative_url, body_without_key_fields) for a PATCH upsert."""
    if key_columns:
        key_parts = []
        for col in key_columns:
            val = record.get(col)
            if val is None:
                raise ValueError(f"Record is missing key column '{col}'")
            if isinstance(val, str) and not _GUID_RE_FULL.match(val):
                key_parts.append(f"{col}='{val}'")
            else:
                key_parts.append(f"{col}={val}")
        key_str = ",".join(key_parts)
        url = f"/{entity_set_name}({key_str})"
        body = {k: v for k, v in record.items() if k not in key_columns}
    else:
        # Detect primary GUID field: first field whose value matches GUID pattern
        primary_id: str | None = None
        primary_col: str | None = None
        for col, val in record.items():
            if isinstance(val, str) and _GUID_RE_FULL.match(val):
                primary_id = val
                primary_col = col
                break
        if not primary_id:
            raise ValueError(
                "No GUID-valued field found in record for primary key upsert. "
                "Provide key_columns to use an alternate key."
            )
        url = f"/{entity_set_name}({primary_id})"
        body = {k: v for k, v in record.items() if k != primary_col}
    return url, body


@write_tool(
    name="dataverse_bulk_upsert",
    annotations={
        "title": "Bulk Upsert Records",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_bulk_upsert(params: BulkUpsertInput, ctx: Context) -> str:
    """Upsert many records in one call using OData $batch PATCH operations.

    Each record is PATCHed individually (not in a change set) so a single bad
    row does not roll back the rest when continue_on_error=true. Records are
    chunked into requests of up to chunk_size operations each.

    Key detection:
    - Provide key_columns to upsert by alternate key
      (e.g., key_columns=['accountnumber'] → PATCH accounts(accountnumber='AN001')).
    - Omit key_columns to upsert by primary GUID — the tool detects the first
      GUID-valued field in each record and uses it as the primary key.

    Returns per-row outcomes (created, updated, or failed) with row index and id.
    Requires DATAVERSE_ALLOW_WRITE=true.
    """
    write_enabled = os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
    if not write_enabled:
        return json.dumps({
            "error": True,
            "message": "dataverse_bulk_upsert requires DATAVERSE_ALLOW_WRITE=true.",
        })

    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    # Build per-row operations, capturing build errors up front
    operations: list[tuple[int, BatchOperationItem | None, str | None]] = []
    for i, record in enumerate(params.records):
        try:
            rel_url, body = _build_upsert_url(
                params.entity_set_name, record, params.key_columns
            )
            op = BatchOperationItem(method="PATCH", url=rel_url, body=body)
            operations.append((i, op, None))
        except (ValueError, KeyError) as exc:
            operations.append((i, None, str(exc)))

    results: list[dict] = []

    # Rows that failed during URL-build
    valid_ops: list[tuple[int, BatchOperationItem]] = []
    for i, op, err in operations:
        if op is None:
            results.append({"row": i, "outcome": "failed", "error": err})
        else:
            valid_ops.append((i, op))

    chunk = params.chunk_size
    for chunk_start in range(0, len(valid_ops), chunk):
        chunk_slice = valid_ops[chunk_start : chunk_start + chunk]
        batch_ops = [op for _, op in chunk_slice]
        row_indices = [i for i, _ in chunk_slice]

        boundary = f"batch_{uuid.uuid4().hex}"
        url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/$batch"

        try:
            batch_body = build_batch_body(batch_ops, base_url, boundary)
            headers = await build_headers(app_ctx, base_url)
            req_headers = {
                **headers,
                "Content-Type": f"multipart/mixed; boundary={boundary}",
                "Accept": "multipart/mixed",
            }
            if params.continue_on_error:
                req_headers["Prefer"] = "odata.continue-on-error"

            response = await request_with_retry(
                app_ctx.http_client,
                "POST",
                url,
                headers=req_headers,
                content=batch_body.encode("utf-8"),
                timeout=_BATCH_TIMEOUT,
            )

            content_type = response.headers.get("Content-Type", "")
            resp_boundary = boundary
            if "boundary=" in content_type:
                resp_boundary = content_type.split("boundary=")[1].split(";")[0].strip()

            if response.status_code not in (200, 202):
                # Whole chunk failed
                for ri in row_indices:
                    results.append({
                        "row": ri,
                        "outcome": "failed",
                        "error": f"Batch HTTP {response.status_code}",
                    })
                continue

            batch_results = parse_batch_response(response.text, resp_boundary)
            for ri, br in zip(row_indices, batch_results):
                status = br.get("status_code", 0)
                if status in (200, 201, 204):
                    outcome = "updated" if status in (200, 204) else "created"
                    # Extract created record id from Location or OData-EntityId header
                    record_id: str | None = None
                    loc = br.get("headers", {}).get("OData-EntityId") or br.get("headers", {}).get("Location")
                    if loc:
                        m = _GUID_RE.search(loc)
                        if m:
                            record_id = m.group(0)
                    results.append({"row": ri, "outcome": outcome, "id": record_id})
                else:
                    err_body = br.get("body") or {}
                    err_msg = (
                        err_body.get("error", {}).get("message")
                        if isinstance(err_body, dict)
                        else str(err_body)
                    )
                    results.append({"row": ri, "outcome": "failed", "error": err_msg})

        except Exception as exc:
            for ri in row_indices:
                results.append({"row": ri, "outcome": "failed", "error": str(exc)})

    results.sort(key=lambda r: r["row"])
    succeeded = sum(1 for r in results if r["outcome"] in ("created", "updated"))
    failed = sum(1 for r in results if r["outcome"] == "failed")

    return json.dumps({
        "total": len(params.records),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    })
