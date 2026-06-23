"""Unit tests for the three async operation (system job) tools (issue #92).

Coverage:
- ListAsyncOperationsInput / GetAsyncOperationInput / CancelAsyncOperationInput
  model validation.
- dataverse_list_async_operations: builds correct $filter for each combination
  of state_code / status_code / operation_type (and none → no filter);
  count/has_more; statecode_label/statuscode_label enrichment.
- dataverse_get_async_operation: returns {"record": {...}}; strips @odata.context;
  enriches statecode_label/statuscode_label.
- dataverse_cancel_async_operation: PATCHes the correct body
  {"statecode": 3, "statuscode": 32}; returns {"cancelled": true, ...}.
- Bad GUIDs → ValidationError; extra fields → ValidationError.
- HTTP 4xx/5xx → {"error": true, "message": ...}.

Mocking strategy: patch build_headers to return {} and replace
app_ctx.http_client with an AsyncMock whose .request method returns a crafted
httpx.Response — mirroring test_solution_import_export.py style.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import (
    CancelAsyncOperationInput,
    GetAsyncOperationInput,
    ListAsyncOperationsInput,
)
from dataverse_mcp.tools.jobs import (
    _STATECODE_LABELS,
    _STATUSCODE_LABELS,
    dataverse_cancel_async_operation,
    dataverse_get_async_operation,
    dataverse_list_async_operations,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_OP_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_OP_ID2 = "11111111-2222-3333-4444-555555555555"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_ctx() -> AppContext:
    return AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )


def _make_ctx(app_ctx: AppContext) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _make_response(status_code: int, body: dict, method: str = "GET") -> httpx.Response:
    content = json.dumps(body).encode()
    return httpx.Response(
        status_code=status_code,
        headers={"Content-Type": "application/json"},
        content=content,
        request=httpx.Request(method, _BASE_URL),
    )


# ---------------------------------------------------------------------------
# Input model validation — ListAsyncOperationsInput
# ---------------------------------------------------------------------------


def test_list_async_operations_input_defaults():
    m = ListAsyncOperationsInput(dataverse_url=_BASE_URL)
    assert m.top == 50
    assert m.state_code is None
    assert m.status_code is None
    assert m.operation_type is None
    assert m.select is None


def test_list_async_operations_input_with_filters():
    m = ListAsyncOperationsInput(
        dataverse_url=_BASE_URL,
        state_code=2,
        status_code=20,
        operation_type=25,
        top=10,
    )
    assert m.state_code == 2
    assert m.status_code == 20
    assert m.operation_type == 25
    assert m.top == 10


def test_list_async_operations_input_top_out_of_range():
    with pytest.raises(ValidationError):
        ListAsyncOperationsInput(dataverse_url=_BASE_URL, top=0)
    with pytest.raises(ValidationError):
        ListAsyncOperationsInput(dataverse_url=_BASE_URL, top=5001)


def test_list_async_operations_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        ListAsyncOperationsInput(dataverse_url=_BASE_URL, bogus_field="bad")


# ---------------------------------------------------------------------------
# Input model validation — GetAsyncOperationInput
# ---------------------------------------------------------------------------


def test_get_async_operation_input_valid():
    m = GetAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id=_OP_ID)
    assert m.async_operation_id == _OP_ID


def test_get_async_operation_input_bad_guid():
    with pytest.raises(ValidationError):
        GetAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id="not-a-guid")


def test_get_async_operation_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        GetAsyncOperationInput(
            dataverse_url=_BASE_URL,
            async_operation_id=_OP_ID,
            extra="nope",
        )


# ---------------------------------------------------------------------------
# Input model validation — CancelAsyncOperationInput
# ---------------------------------------------------------------------------


def test_cancel_async_operation_input_valid():
    m = CancelAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id=_OP_ID)
    assert m.async_operation_id == _OP_ID


def test_cancel_async_operation_input_bad_guid():
    with pytest.raises(ValidationError):
        CancelAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id="bad")


def test_cancel_async_operation_input_extra_field_forbidden():
    with pytest.raises(ValidationError):
        CancelAsyncOperationInput(
            dataverse_url=_BASE_URL,
            async_operation_id=_OP_ID,
            unwanted="x",
        )


# ---------------------------------------------------------------------------
# dataverse_list_async_operations — no filters
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.jobs.paginate_records", new_callable=AsyncMock)
async def test_list_async_operations_no_filter(mock_paginate, mock_headers):
    """No filters: $filter absent from URL; count/has_more returned."""
    mock_records = [
        {
            "asyncoperationid": _OP_ID,
            "name": "Import Solution",
            "operationtype": 25,
            "statecode": 3,
            "statuscode": 30,
        }
    ]
    mock_paginate.return_value = mock_records
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListAsyncOperationsInput(dataverse_url=_BASE_URL, top=10)
    result = json.loads(await dataverse_list_async_operations(params, ctx))

    assert result["count"] == 1
    assert result["has_more"] is False
    assert result["records"][0]["asyncoperationid"] == _OP_ID

    # No $filter in URL
    called_url = mock_paginate.call_args.args[0]
    assert "$filter" not in called_url


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.jobs.paginate_records", new_callable=AsyncMock)
async def test_list_async_operations_with_state_code_filter(mock_paginate, mock_headers):
    """state_code filter adds 'statecode eq N' to $filter."""
    mock_paginate.return_value = []
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListAsyncOperationsInput(dataverse_url=_BASE_URL, state_code=3)
    await dataverse_list_async_operations(params, ctx)

    called_url = mock_paginate.call_args.args[0]
    assert "statecode+eq+3" in called_url or "statecode eq 3" in called_url


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.jobs.paginate_records", new_callable=AsyncMock)
async def test_list_async_operations_with_status_code_filter(mock_paginate, mock_headers):
    """status_code filter adds 'statuscode eq N' to $filter."""
    mock_paginate.return_value = []
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListAsyncOperationsInput(dataverse_url=_BASE_URL, status_code=31)
    await dataverse_list_async_operations(params, ctx)

    called_url = mock_paginate.call_args.args[0]
    assert "statuscode" in called_url


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.jobs.paginate_records", new_callable=AsyncMock)
async def test_list_async_operations_with_operation_type_filter(mock_paginate, mock_headers):
    """operation_type filter adds 'operationtype eq N' to $filter."""
    mock_paginate.return_value = []
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListAsyncOperationsInput(dataverse_url=_BASE_URL, operation_type=25)
    await dataverse_list_async_operations(params, ctx)

    called_url = mock_paginate.call_args.args[0]
    assert "operationtype" in called_url


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.jobs.paginate_records", new_callable=AsyncMock)
async def test_list_async_operations_with_all_filters(mock_paginate, mock_headers):
    """All three filters combine with ' and '."""
    mock_paginate.return_value = []
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListAsyncOperationsInput(
        dataverse_url=_BASE_URL,
        state_code=2,
        status_code=20,
        operation_type=25,
    )
    await dataverse_list_async_operations(params, ctx)

    called_url = mock_paginate.call_args.args[0]
    assert "statecode" in called_url
    assert "statuscode" in called_url
    assert "operationtype" in called_url


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.jobs.paginate_records", new_callable=AsyncMock)
async def test_list_async_operations_has_more(mock_paginate, mock_headers):
    """has_more is True when returned record count equals top."""
    top = 3
    mock_paginate.return_value = [
        {"asyncoperationid": f"{i}" * 8 + "-0000-0000-0000-" + "0" * 12}
        for i in range(top)
    ]
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListAsyncOperationsInput(dataverse_url=_BASE_URL, top=top)
    result = json.loads(await dataverse_list_async_operations(params, ctx))

    assert result["has_more"] is True
    assert result["count"] == top


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.jobs.paginate_records", new_callable=AsyncMock)
async def test_list_async_operations_enriches_labels(mock_paginate, mock_headers):
    """Records are enriched with statecode_label and statuscode_label."""
    mock_paginate.return_value = [
        {
            "asyncoperationid": _OP_ID,
            "statecode": 3,
            "statuscode": 30,
        }
    ]
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListAsyncOperationsInput(dataverse_url=_BASE_URL)
    result = json.loads(await dataverse_list_async_operations(params, ctx))

    record = result["records"][0]
    assert record["statecode_label"] == "Completed"
    assert record["statuscode_label"] == "Succeeded"


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
@patch("dataverse_mcp.tools.jobs.paginate_records", new_callable=AsyncMock)
async def test_list_async_operations_http_error(mock_paginate, mock_headers):
    """HTTP error from paginate_records returns {"error": true, ...}."""
    mock_paginate.side_effect = httpx.HTTPStatusError(
        "500 Internal Server Error",
        request=httpx.Request("GET", _BASE_URL),
        response=httpx.Response(
            500,
            content=b'{"error":{"message":"server error"}}',
            request=httpx.Request("GET", _BASE_URL),
        ),
    )
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = ListAsyncOperationsInput(dataverse_url=_BASE_URL)
    result = json.loads(await dataverse_list_async_operations(params, ctx))

    assert result["error"] is True
    assert "message" in result


# ---------------------------------------------------------------------------
# dataverse_get_async_operation — happy path
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_async_operation_returns_record(mock_headers):
    """Successful GET returns {"record": {...}} and strips @odata.context."""
    record_body = {
        "@odata.context": "https://yourorg.crm.dynamics.com/api/data/v9.2/$metadata#asyncoperations/$entity",
        "asyncoperationid": _OP_ID,
        "name": "Import Solution",
        "operationtype": 25,
        "statecode": 3,
        "statuscode": 30,
    }
    resp = _make_response(200, record_body)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=resp)
    ctx = _make_ctx(app_ctx)

    params = GetAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id=_OP_ID)
    result = json.loads(await dataverse_get_async_operation(params, ctx))

    assert "record" in result
    assert result["record"]["asyncoperationid"] == _OP_ID
    # @odata.context must be stripped
    assert "@odata.context" not in result["record"]


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_async_operation_enriches_labels(mock_headers):
    """Get enriches statecode_label and statuscode_label on the record."""
    record_body = {
        "asyncoperationid": _OP_ID,
        "statecode": 2,
        "statuscode": 20,
    }
    resp = _make_response(200, record_body)
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=resp)
    ctx = _make_ctx(app_ctx)

    params = GetAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id=_OP_ID)
    result = json.loads(await dataverse_get_async_operation(params, ctx))

    assert result["record"]["statecode_label"] == "Locked"
    assert result["record"]["statuscode_label"] == "In Progress"


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
async def test_get_async_operation_404_returns_error(mock_headers):
    """404 response returns a structured error."""
    not_found = httpx.Response(
        status_code=404,
        content=b'{"error":{"code":"0x80040217","message":"Object not found"}}',
        request=httpx.Request("GET", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=not_found)
    ctx = _make_ctx(app_ctx)

    params = GetAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id=_OP_ID)
    result = json.loads(await dataverse_get_async_operation(params, ctx))

    assert result["error"] is True
    assert "message" in result


# ---------------------------------------------------------------------------
# dataverse_cancel_async_operation — happy path
# ---------------------------------------------------------------------------


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
async def test_cancel_async_operation_patches_correct_body(mock_headers):
    """Cancel PATCHes statecode=3, statuscode=32 and returns {"cancelled": true, ...}."""
    # Dataverse returns 204 No Content on a successful PATCH
    patch_resp = httpx.Response(
        status_code=204,
        content=b"",
        request=httpx.Request("PATCH", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=patch_resp)
    ctx = _make_ctx(app_ctx)

    params = CancelAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id=_OP_ID)
    result = json.loads(await dataverse_cancel_async_operation(params, ctx))

    assert result["cancelled"] is True
    assert result["async_operation_id"] == _OP_ID

    # Verify the PATCH body contained statecode=3, statuscode=32
    call_args = app_ctx.http_client.request.call_args
    sent_body = call_args.kwargs.get("json", {})
    assert sent_body.get("statecode") == 3
    assert sent_body.get("statuscode") == 32

    # Verify method was PATCH
    method = call_args.args[0] if call_args.args else call_args.kwargs.get("method")
    assert method == "PATCH"


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
async def test_cancel_async_operation_url_contains_id(mock_headers):
    """PATCH URL contains the asyncoperation GUID."""
    patch_resp = httpx.Response(
        status_code=204,
        content=b"",
        request=httpx.Request("PATCH", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=patch_resp)
    ctx = _make_ctx(app_ctx)

    params = CancelAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id=_OP_ID)
    await dataverse_cancel_async_operation(params, ctx)

    call_args = app_ctx.http_client.request.call_args
    url = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("url", "")
    assert _OP_ID in url
    assert "asyncoperations" in url


@patch("dataverse_mcp.tools.jobs.build_headers", new_callable=AsyncMock, return_value={})
async def test_cancel_async_operation_http_error(mock_headers):
    """HTTP 4xx/5xx from cancel returns {"error": true, "message": ...}."""
    bad_resp = httpx.Response(
        status_code=400,
        content=b'{"error":{"code":"0x80040203","message":"Cannot cancel completed job"}}',
        request=httpx.Request("PATCH", _BASE_URL),
    )
    app_ctx = _make_app_ctx()
    app_ctx.http_client.request = AsyncMock(return_value=bad_resp)
    ctx = _make_ctx(app_ctx)

    params = CancelAsyncOperationInput(dataverse_url=_BASE_URL, async_operation_id=_OP_ID)
    result = json.loads(await dataverse_cancel_async_operation(params, ctx))

    assert result["error"] is True
    assert "message" in result


# ---------------------------------------------------------------------------
# Label map sanity checks
# ---------------------------------------------------------------------------


def test_statecode_label_map():
    """Verify the statecode label map covers all standard values."""
    assert _STATECODE_LABELS[0] == "Ready"
    assert _STATECODE_LABELS[1] == "Suspended"
    assert _STATECODE_LABELS[2] == "Locked"
    assert _STATECODE_LABELS[3] == "Completed"


def test_statuscode_label_map():
    """Verify the statuscode label map covers all standard values."""
    assert _STATUSCODE_LABELS[0] == "Waiting For Resources"
    assert _STATUSCODE_LABELS[10] == "Waiting"
    assert _STATUSCODE_LABELS[20] == "In Progress"
    assert _STATUSCODE_LABELS[21] == "Pausing"
    assert _STATUSCODE_LABELS[22] == "Canceling"
    assert _STATUSCODE_LABELS[30] == "Succeeded"
    assert _STATUSCODE_LABELS[31] == "Failed"
    assert _STATUSCODE_LABELS[32] == "Canceled"
