"""Table query tools for the Dataverse MCP server."""

import json
import logging
import os
import re
import uuid
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

import defusedxml.ElementTree as DET
import httpx
from defusedxml.common import DefusedXmlException
from mcp.server.mcpserver import Context

from dataverse_mcp._app import category_tools

tool, write_tool, delete_tool = category_tools("core")
from dataverse_mcp.batch import build_batch_body, parse_batch_response
from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    build_headers,
    encode_odata_literal,
    extract_error_message,
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
    GetTotalRecordCountsInput,
    MergeRecordsInput,
    QueryTableInput,
    SwapFlowConnectionReferenceInput,
    UpdateRecordInput,
    ValidateFetchXmlInput,
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


# Severity threshold that separates an error from a warning in
# ValidateFetchXmlExpressionResponse. The mapping is OBSERVED, not documented:
# live calls returned Severity 1 for performance warnings (leading wildcard, too
# many select attributes) and Severity 3 for hard errors ("Error handling
# FetchXML: The entity with a name = '...' was not found in the MetadataCache").
# Anything at or above this value is treated as an error, so an unrecognized
# higher code errs on the side of "do not run this query".
_FETCH_XML_ERROR_SEVERITY = 3


def _summarize_validation_messages(payload: Any) -> dict[str, Any] | None:
    """Summarize ``ValidationResults.Messages`` by severity, or None if absent.

    The live payload carries exactly one top-level property, ``ValidationResults``,
    holding ``Helplink`` and a ``Messages`` list. Each message has ``Severity``,
    ``TypeCode``, ``LocalizedMessageText`` and an ``OptionalPropertyBag``. This is
    the only place the error/warning split is derived, because Dataverse answers
    HTTP 200 even when the expression is invalid.

    Every message is counted exactly once: ``count == error_count +
    warning_count``. Only a message whose ``Severity`` is an integer below the
    error threshold is a warning; anything else — a higher code, a missing
    ``Severity``, a non-integer one, or an entry that is not an object at all —
    is counted as an error rather than dropped or assumed benign.

    Returns None when the payload is not in the expected shape, so the caller can
    fall back to raw pass-through instead of fabricating counts.
    """
    if not isinstance(payload, dict):
        return None
    results = payload.get("ValidationResults")
    if not isinstance(results, dict):
        return None
    messages = results.get("Messages")
    if not isinstance(messages, list):
        return None

    errors: list[str] = []
    warning_count = 0
    for item in messages:
        severity = item.get("Severity") if isinstance(item, dict) else None
        # bool is a subclass of int; never let True/False pass as a severity code.
        if (
            isinstance(severity, int)
            and not isinstance(severity, bool)
            and severity < _FETCH_XML_ERROR_SEVERITY
        ):
            warning_count += 1
            continue
        text = item.get("LocalizedMessageText") if isinstance(item, dict) else None
        if not isinstance(text, str) or not text:
            text = f"(no message text; Severity={severity!r})"
        errors.append(text)

    return {
        "has_errors": bool(errors),
        "count": len(messages),
        "error_count": len(errors),
        "warning_count": warning_count,
        "errors": errors,
    }


@tool(
    name="dataverse_validate_fetchxml",
    annotations={
        "title": "Validate FetchXML",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_validate_fetchxml(params: ValidateFetchXmlInput, ctx: Context) -> str:
    """Check a FetchXML query for problems and performance warnings WITHOUT running it.

    Pre-flight companion to dataverse_execute_fetchxml. Calls the unbound
    ValidateFetchXmlExpression function, which parses and analyses the expression
    server-side and reports validation errors plus performance suggestions (for
    example unrestricted column lists or filters that cannot use an index). No
    records are read and nothing is modified. Run it before executing an expensive
    or machine-generated query — a FetchXML query that returns results can still be
    a query that scans a table.

    No entity set name is required: the root <entity name="..."> inside the
    document identifies the table.

    HTTP 200 DOES NOT MEAN THE QUERY IS VALID — check has_errors / error_count.
    A FetchXml naming a table or attribute that does not exist comes back as a
    successful HTTP 200 carrying an error-severity message ("Error handling
    FetchXML: The entity with a name = '...' was not found in the MetadataCache"),
    not as an HTTP 400. Treating a non-error response as "this query works" is
    wrong. Read has_errors first, then errors for the error texts.

    Findings are reported as: count (total messages), error_count, warning_count
    (count == error_count + warning_count, so nothing is dropped), has_errors, and
    errors (the error texts). The severity mapping is OBSERVED, NOT DOCUMENTED:
    live responses used 1 for performance warnings and 3 for errors, so severity
    >= 3 is counted as an error and < 3 as a warning. Any message whose severity
    is missing or not an integer is bucketed conservatively as an error rather
    than assumed benign, and still appears in count.

    The full payload is also returned unchanged under raw_response (minus the
    @odata envelope): ValidationResults.Helplink, each message's
    LocalizedMessageText and its OptionalPropertyBag (which carries details such
    as AttributeCount/AttributeLimit) are worth reading. If the payload is not in
    the expected ValidationResults.Messages shape it is returned raw with
    normalized=false and no counts, rather than being guessed at.

    The query is checked locally for XML well-formedness first, using a hardened
    parser that rejects DTDs and entity declarations, so malformed or hostile
    markup fails immediately with a clear message instead of costing a round trip.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    fetch_xml = params.fetch_xml

    # Cheap local pre-check. Two jobs: fail obviously broken markup fast (Dataverse
    # cannot analyse a document it cannot parse, and its 400 is far less specific
    # than a parser's line/column), and refuse to relay DTD / entity declarations —
    # FetchXml never needs them and an entity-expansion payload should not be
    # forwarded to the server. defusedxml is used for exactly this reason; the
    # stdlib parser must never see caller-supplied XML.
    try:
        DET.fromstring(fetch_xml)
    except DefusedXmlException:
        logger.warning("Rejected fetch_xml carrying forbidden XML constructs (DTD/entities)")
        return json.dumps({
            "error": True,
            "message": (
                "fetch_xml declares a DTD or XML entities and was rejected before "
                "the request was sent. FetchXml never needs them, and entity "
                "expansion is a denial-of-service vector. Remove the <!DOCTYPE> / "
                "&entity; declarations and validate again."
            ),
        })
    except ET.ParseError as exc:
        return json.dumps({
            "error": True,
            "message": (
                f"fetch_xml is not well-formed XML: {exc}. Fix the markup (check "
                "for unclosed tags and unescaped <, > or & characters inside "
                "attribute values) before validating — Dataverse cannot analyse a "
                "document it cannot parse."
            ),
        })

    # FetchXml is a whole XML document, so it cannot be inlined into the function
    # call; it rides a parameter alias holding a single-quoted OData string literal.
    # Unlike the JSON array in dataverse_get_total_record_counts, that literal DOES
    # need OData escaping: encode_odata_literal doubles every embedded single quote
    # before percent-encoding, so a quote inside the FetchXml (perfectly legal in an
    # OData filter value such as <condition value="o'brien" />) cannot terminate the
    # literal early and navigate to another resource once Dataverse percent-decodes
    # the URL. The '@' of the alias and the quotes delimiting the literal are the
    # only characters left unencoded, matching the key-predicate call sites.
    fetch_enc = encode_odata_literal(fetch_xml)
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/ValidateFetchXmlExpression(FetchXml=@p1)?@p1='{fetch_enc}'"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 414:
            logger.error(
                "ValidateFetchXmlExpression URL rejected as too long (%d raw / %d encoded chars)",
                len(fetch_xml), len(fetch_enc),
            )
            return json.dumps({
                "error": True,
                "message": (
                    "Dataverse rejected the request URL as too long (HTTP 414). The "
                    f"{len(fetch_xml)}-character FetchXml percent-encodes to "
                    f"{len(fetch_enc)} characters in the query string. Shorten the "
                    "query — remove insignificant whitespace and comments, trim "
                    "<attribute> elements to the columns you need, or validate "
                    "<link-entity> branches one at a time — and try again."
                ),
            })
        return tool_error_response(e, "dataverse_validate_fetchxml")
    except Exception as e:
        return tool_error_response(e, "dataverse_validate_fetchxml")

    # Parse defensively: a non-JSON or empty body is surfaced as-is rather than
    # raising.
    try:
        payload: Any = response.json()
    except ValueError:
        logger.warning("ValidateFetchXmlExpression returned a non-JSON body")
        payload = response.text or None

    body: Any = payload
    if isinstance(payload, dict):
        body = {k: v for k, v in payload.items() if not k.startswith("@odata.")}

    # HTTP 200 only means Dataverse analysed the expression — an invalid query
    # (unknown table or attribute) also answers 200, with a Severity-3 message.
    # The verdict lives in the payload, so it is lifted to the top level here.
    summary = _summarize_validation_messages(body)
    result: dict[str, Any] = {"normalized": summary is not None}

    if summary is None:
        logger.warning(
            "ValidateFetchXmlExpression response was not in the expected "
            "ValidationResults.Messages shape; returning it raw"
        )
        result["message"] = (
            "Dataverse answered HTTP 200 but the payload did not carry the "
            "expected ValidationResults.Messages structure, so no error or "
            "warning counts could be derived and none are reported. HTTP 200 does "
            "NOT mean the query is valid — read raw_response yourself before "
            "trusting this FetchXml."
        )
    else:
        result.update(summary)
        if summary["has_errors"]:
            result["message"] = (
                f"NOT VALID: Dataverse reported {summary['error_count']} "
                f"error-severity message(s) (and {summary['warning_count']} "
                "warning(s)) for this FetchXml. The HTTP 200 only means the "
                "expression was analysed — executing this query would fail. Fix "
                "the problems listed in errors, then validate again."
            )
        elif summary["count"]:
            result["message"] = (
                f"No errors reported; {summary['warning_count']} performance "
                "warning(s). The query should run, but read "
                "raw_response.ValidationResults.Messages — each message's "
                "LocalizedMessageText and OptionalPropertyBag say which construct "
                "is expensive and, where relevant, the limit that was exceeded."
            )
        else:
            result["message"] = (
                "Dataverse analysed the expression and reported no errors and no "
                "performance warnings."
            )

    result["fetch_xml_length"] = len(fetch_xml)
    result["raw_response"] = body

    return finalize_response(result)


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


@write_tool(
    name="dataverse_swap_flow_connection_reference",
    annotations={
        "title": "Swap Flow Connection Reference",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_swap_flow_connection_reference(
    params: SwapFlowConnectionReferenceInput, ctx: Context
) -> str:
    """Swap a connection reference logical name inside a cloud flow's clientdata.

    Reads the flow's clientdata, replaces every literal occurrence of
    old_logical_name with new_logical_name, and PATCHes the result back — all
    server-side, so the multi-KB clientdata JSON never travels as a tool
    argument (which would otherwise risk truncation at the transport boundary
    and a "Flow clientdata is in invalid format" error from Dataverse). This
    is a literal string replace, not a JSON reparse, so unrelated formatting
    is preserved byte-for-byte. Requires DATAVERSE_ALLOW_WRITE=true.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    get_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/workflows({params.workflow_id})?$select=clientdata"
    )
    patch_url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/workflows({params.workflow_id})"
    )

    try:
        headers = await build_headers(app_ctx, base_url, include_content_type=True)

        get_resp = await request_with_retry(
            app_ctx.http_client, "GET", get_url, headers=headers
        )
        get_resp.raise_for_status()
        clientdata = get_resp.json().get("clientdata") or ""

        replacements = clientdata.count(params.old_logical_name)
        if replacements == 0:
            return finalize_response({
                "updated": False,
                "workflow_id": params.workflow_id,
                "replacements": 0,
                "message": "old_logical_name not found in clientdata",
            })

        new_clientdata = clientdata.replace(
            params.old_logical_name, params.new_logical_name
        )

        patch_resp = await request_with_retry(
            app_ctx.http_client,
            "PATCH",
            patch_url,
            headers=headers,
            json={"clientdata": new_clientdata},
        )
        patch_resp.raise_for_status()

        logger.info(
            "Swapped connection reference in workflow %s: %s -> %s (%d replacement(s))",
            params.workflow_id,
            params.old_logical_name,
            params.new_logical_name,
            replacements,
        )
        return finalize_response({
            "updated": True,
            "workflow_id": params.workflow_id,
            "replacements": replacements,
            "old_logical_name": params.old_logical_name,
            "new_logical_name": params.new_logical_name,
        })
    except Exception as e:
        return tool_error_response(e, "dataverse_swap_flow_connection_reference")


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


# RetrieveTotalRecordCount is all-or-nothing: one unrecognized logical name fails
# the whole batch with HTTP 400 and this error code. The message names the offending
# table, which is the single most useful thing to hand back to the caller.
_ENTITY_NOT_FOUND_CODE = "0x80040203"
_ENTITY_NOT_FOUND_RE = re.compile(
    r"Entity\s+([A-Za-z_][A-Za-z0-9_]*)\s+was not found", re.IGNORECASE
)


def _entity_not_found_message(e: httpx.HTTPStatusError, requested: int) -> str | None:
    """Return an actionable message for the unknown-entity 400, else None."""
    if e.response.status_code != 400:
        return None
    detail = extract_error_message(e.response)
    if _ENTITY_NOT_FOUND_CODE not in detail:
        return None
    match = _ENTITY_NOT_FOUND_RE.search(detail)
    culprit = (
        f"'{match.group(1)}' is not a table in this environment"
        if match
        else "one of the requested names is not a table in this environment"
    )
    fix = (
        f"Remove {match.group(1)!r} from entity_names and call again"
        if match
        else "Remove the unrecognized name from entity_names and call again"
    )
    return (
        f"Dataverse returned HTTP 400: {detail}. {culprit}. "
        "RetrieveTotalRecordCount is all-or-nothing: this rejected the entire "
        f"batch, so no counts were returned for any of the {requested} requested "
        f"name(s). {fix}. Names must be singular lowercase logical names "
        "('account', not 'accounts'); use dataverse_list_tables to confirm them."
    )


def _extract_count_map(payload: Any) -> dict[str, int] | None:
    """Return a {logical_name: count} map from a RetrieveTotalRecordCount payload.

    Dataverse serializes the .NET dictionary in ``EntityRecordCountCollection`` as
    an object carrying parallel ``Keys`` / ``Values`` arrays (alongside ``Count``
    and ``IsReadOnly``). The property that wraps it is not documented, so the
    wrapper is located by shape rather than by name: the payload itself and each
    of its object-valued properties are checked for the parallel-array form.

    Returns None when nothing matches — including on a length mismatch or a
    non-string/non-integer element — so the caller can fall back to returning the
    raw payload instead of pairing up values that may not correspond.
    """
    if not isinstance(payload, dict):
        return None
    candidates = [payload, *(v for v in payload.values() if isinstance(v, dict))]
    for candidate in candidates:
        keys = candidate.get("Keys")
        values = candidate.get("Values")
        if not isinstance(keys, list) or not isinstance(values, list):
            continue
        if len(keys) != len(values):
            logger.warning(
                "RetrieveTotalRecordCount returned %d keys but %d values; "
                "refusing to zip them",
                len(keys), len(values),
            )
            continue
        if not all(isinstance(k, str) for k in keys):
            continue
        # bool is a subclass of int; exclude it so IsReadOnly-style data is never
        # mistaken for a row count.
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            continue
        return dict(zip(keys, values))
    return None


@tool(
    name="dataverse_get_total_record_counts",
    annotations={
        "title": "Get Total Record Counts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def dataverse_get_total_record_counts(
    params: GetTotalRecordCountsInput, ctx: Context
) -> str:
    """Get approximate row counts for many Dataverse tables in one round trip.

    Calls the unbound RetrieveTotalRecordCount function with up to 50 table
    logical names (singular and lowercase — 'account', not 'accounts') and returns
    a {logical_name: count} map.

    The counts come from a snapshot Dataverse takes at most once every 24 hours,
    so they are approximate and can lag reality by up to a day: a table populated
    an hour ago may report 0, and recent deletions may still be included. Worse,
    on an environment where the snapshot job has not run, EVERY count comes back 0
    while the tables actually hold data (observed live on an org whose real counts
    were in the hundreds). The response flags that case with all_counts_zero=true
    — read it as "unknown", not "empty". Use this tool for cheap bulk sizing
    (which tables hold data, rough magnitudes, migration planning), and
    dataverse_count_records whenever an exact, live, or filtered count matters.

    Unknown names are all-or-nothing, NOT silently dropped: a single logical name
    Dataverse does not recognize fails the whole call with HTTP 400 ([0x80040203]
    "Entity X was not found in the CRM system") and no partial results come back.
    Pass names you have already confirmed exist — dataverse_list_tables is the
    cheap way to confirm them. The error message names the offending table so you
    can drop it and retry.

    If the response is not in the expected shape it is returned unchanged under
    raw_response with normalized=false rather than being guessed at.
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    entity_names = params.entity_names

    # EntityNames is Collection(Edm.String); a collection-typed parameter cannot be
    # inlined in the function call, so the JSON array travels as a parameter alias
    # value in the query string: RetrieveTotalRecordCount(EntityNames=@p1)?@p1=[...].
    # The alias value is JSON, not an OData single-quoted string literal, so
    # encode_odata_literal/odata_quote do NOT apply here — doubling quotes would
    # corrupt the JSON. Percent-encoding is the correct (and only) escape, and it is
    # safe because every name is already constrained to the Dataverse identifier
    # grammar by GetTotalRecordCountsInput.
    alias_value = json.dumps(entity_names, separators=(",", ":"))
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveTotalRecordCount(EntityNames=@p1)?"
        f"{urlencode({'@p1': alias_value}, safe='@')}"
    )

    try:
        headers = await build_headers(app_ctx, base_url)
        response = await request_with_retry(
            app_ctx.http_client, "GET", url, headers=headers
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as e:
        message = _entity_not_found_message(e, len(entity_names))
        if message:
            logger.error(
                "RetrieveTotalRecordCount rejected a batch of %d name(s): %s",
                len(entity_names), message,
            )
            return json.dumps({"error": True, "message": message})
        return tool_error_response(e, "dataverse_get_total_record_counts")
    except Exception as e:
        return tool_error_response(e, "dataverse_get_total_record_counts")

    body: Any = payload
    if isinstance(payload, dict):
        body = {k: v for k, v in payload.items() if not k.startswith("@odata.")}

    counts = _extract_count_map(body)
    if counts is None:
        logger.warning(
            "RetrieveTotalRecordCount response was not in the expected "
            "Keys/Values shape; returning it raw"
        )
        return finalize_response({
            "normalized": False,
            "approximate": True,
            "message": (
                "The RetrieveTotalRecordCount response did not carry the expected "
                "parallel Keys/Values arrays, so it is returned unchanged (minus "
                "the @odata envelope) under raw_response. Read the counts from "
                "there."
            ),
            "requested": entity_names,
            "raw_response": body,
        })

    # An all-zero result is far more often "the 24h snapshot job has not run on this
    # environment" than "every one of these tables is empty" — say so rather than
    # letting a caller act on a fleet of confident zeros.
    all_zero = bool(counts) and not any(counts.values())
    result: dict[str, Any] = {
        "normalized": True,
        "approximate": True,
        "counts": counts,
        "count": len(counts),
        "requested_count": len(entity_names),
        "all_counts_zero": all_zero,
    }
    if all_zero:
        logger.info(
            "RetrieveTotalRecordCount returned 0 for all %d table(s); the snapshot "
            "job may not have run on this environment",
            len(counts),
        )
        result["message"] = (
            "Every returned count is 0. These counts come from a snapshot taken at "
            "most once every 24 hours, so this can mean the tables really are empty "
            "OR that the snapshot job has not run on this environment — a live org "
            "holding hundreds of rows has been observed returning 0 for every "
            "table. Confirm with dataverse_count_records before acting on it."
        )

    return finalize_response(result)


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

    POST/PUT/PATCH operations require DATAVERSE_ALLOW_WRITE=true; DELETE operations require
    DATAVERSE_ALLOW_DELETE=true. Group operations with the same
    change_set_id to run them atomically (all-or-nothing, up to 1,000 operations per request).
    Returns per-operation results [{index, status_code, body}].
    """
    app_ctx = get_app_ctx(ctx)
    try:
        base_url = resolve_base_url(params.dataverse_url)
    except ValueError as e:
        return json.dumps({"error": True, "message": str(e)})

    has_writes = any(op.method in ("POST", "PUT", "PATCH") for op in params.operations)
    if has_writes:
        write_enabled = os.environ.get("DATAVERSE_ALLOW_WRITE", "").lower() == "true"
        if not write_enabled:
            return json.dumps({
                "error": True,
                "message": (
                    "Batch operations containing POST/PUT/PATCH methods require "
                    "DATAVERSE_ALLOW_WRITE=true in the MCP server environment."
                ),
            })

    has_deletes = any(op.method == "DELETE" for op in params.operations)
    if has_deletes:
        delete_enabled = os.environ.get("DATAVERSE_ALLOW_DELETE", "").lower() == "true"
        if not delete_enabled:
            return json.dumps({
                "error": True,
                "message": (
                    "Batch operations containing DELETE methods require "
                    "DATAVERSE_ALLOW_DELETE=true in the MCP server environment."
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
