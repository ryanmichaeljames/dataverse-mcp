"""Unit tests for audit history tools and input models.

Coverage:
- RetrieveRecordChangeHistoryInput / GetAuditDetailsInput / ListAuditInput
  model validation (required fields, GUID format, extra='forbid').
- dataverse_retrieve_record_change_history happy path — AuditDetailCollection response.
- dataverse_retrieve_record_change_history error path — auditing-disabled HTTP error.
- dataverse_get_audit_details happy path — bound function URL form.
- dataverse_get_audit_details 404 path — audit record not found.
- dataverse_list_audit happy path — paginated records with filter/orderby.
- URL construction verified: RetrieveRecordChangeHistory uses alias param @p1;
  RetrieveAuditDetails uses bound function URL form.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import (
    GetAuditDetailsInput,
    ListAuditInput,
    RetrieveRecordChangeHistoryInput,
)
from dataverse_mcp.tools.security import (
    dataverse_get_audit_details,
    dataverse_list_audit,
    dataverse_retrieve_record_change_history,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_ACCOUNT_ID = "aaaabbbb-0000-cccc-1111-dddd2222eeee"
_AUDIT_ID = "11112222-3333-4444-5555-666677778888"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_ctx() -> AppContext:
    """Return a minimal AppContext with a mock http_client."""
    return AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )


def _make_ctx(app_ctx: AppContext) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _mock_response(
    status_code: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.headers = headers or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Input model validation — RetrieveRecordChangeHistoryInput
# ---------------------------------------------------------------------------


def test_retrieve_record_change_history_input_valid() -> None:
    m = RetrieveRecordChangeHistoryInput(
        dataverse_url=_BASE_URL,
        entity_set_name="accounts",
        record_id=_ACCOUNT_ID,
    )
    assert m.entity_set_name == "accounts"
    assert m.record_id == _ACCOUNT_ID
    assert m.top == 50


def test_retrieve_record_change_history_input_top_override() -> None:
    m = RetrieveRecordChangeHistoryInput(
        dataverse_url=_BASE_URL,
        entity_set_name="contacts",
        record_id=_ACCOUNT_ID,
        top=10,
    )
    assert m.top == 10


def test_retrieve_record_change_history_input_invalid_guid() -> None:
    with pytest.raises(Exception):
        RetrieveRecordChangeHistoryInput(
            dataverse_url=_BASE_URL,
            entity_set_name="accounts",
            record_id="not-a-guid",
        )


def test_retrieve_record_change_history_input_missing_entity_set() -> None:
    with pytest.raises(Exception):
        RetrieveRecordChangeHistoryInput(
            dataverse_url=_BASE_URL,
            record_id=_ACCOUNT_ID,
        )


def test_retrieve_record_change_history_input_extra_field_forbidden() -> None:
    with pytest.raises(Exception):
        RetrieveRecordChangeHistoryInput(
            dataverse_url=_BASE_URL,
            entity_set_name="accounts",
            record_id=_ACCOUNT_ID,
            unexpected_field="x",
        )


# ---------------------------------------------------------------------------
# Input model validation — GetAuditDetailsInput
# ---------------------------------------------------------------------------


def test_get_audit_details_input_valid() -> None:
    m = GetAuditDetailsInput(dataverse_url=_BASE_URL, audit_id=_AUDIT_ID)
    assert m.audit_id == _AUDIT_ID


def test_get_audit_details_input_invalid_guid() -> None:
    with pytest.raises(Exception):
        GetAuditDetailsInput(dataverse_url=_BASE_URL, audit_id="bad-guid")


def test_get_audit_details_input_extra_field_forbidden() -> None:
    with pytest.raises(Exception):
        GetAuditDetailsInput(
            dataverse_url=_BASE_URL,
            audit_id=_AUDIT_ID,
            extra="field",
        )


# ---------------------------------------------------------------------------
# Input model validation — ListAuditInput
# ---------------------------------------------------------------------------


def test_list_audit_input_defaults() -> None:
    m = ListAuditInput(dataverse_url=_BASE_URL)
    assert m.filter is None
    assert m.select is None
    assert m.orderby is None
    assert m.top == 50


def test_list_audit_input_with_filter_and_orderby() -> None:
    m = ListAuditInput(
        dataverse_url=_BASE_URL,
        filter="operation eq 2",
        orderby=["createdon desc"],
        top=25,
    )
    assert m.filter == "operation eq 2"
    assert m.orderby == ["createdon desc"]
    assert m.top == 25


def test_list_audit_input_top_bounds() -> None:
    with pytest.raises(Exception):
        ListAuditInput(dataverse_url=_BASE_URL, top=0)
    with pytest.raises(Exception):
        ListAuditInput(dataverse_url=_BASE_URL, top=5001)


def test_list_audit_input_extra_field_forbidden() -> None:
    with pytest.raises(Exception):
        ListAuditInput(dataverse_url=_BASE_URL, bogus_field="x")


# ---------------------------------------------------------------------------
# Tool: dataverse_retrieve_record_change_history — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_record_change_history_happy_path() -> None:
    """Returns structured AuditDetailCollection with count/has_more."""
    audit_details = [
        {
            "@odata.type": "#Microsoft.Dynamics.CRM.AttributeAuditDetail",
            "AuditRecord": {
                "auditid": _AUDIT_ID,
                "createdon": "2024-06-01T12:00:00Z",
                "operation": 2,
                "action": 2,
                "objecttypecode": "account",
            },
            "OldValue": {"name": "Old Corp"},
            "NewValue": {"name": "New Corp"},
        }
    ]
    api_body = {
        "AuditDetailCollection": {
            "AuditDetails": audit_details,
            "MoreRecords": False,
            "PagingCookie": None,
            "TotalRecordCount": 1,
        }
    }

    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = RetrieveRecordChangeHistoryInput(
        dataverse_url=_BASE_URL,
        entity_set_name="accounts",
        record_id=_ACCOUNT_ID,
    )

    with (
        patch(
            "dataverse_mcp.tools.security.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.security.request_with_retry",
            new=AsyncMock(return_value=_mock_response(200, api_body)),
        ) as mock_request,
    ):
        result = await dataverse_retrieve_record_change_history(params, ctx)

    data = json.loads(result)
    assert data["count"] == 1
    assert data["has_more"] is False
    assert data["entity_set_name"] == "accounts"
    assert data["record_id"] == _ACCOUNT_ID
    assert len(data["audit_details"]) == 1
    assert data["total_record_count"] == 1

    # Verify URL encodes Target as alias @p1 with relative @odata.id
    call_url = mock_request.call_args[0][2]
    assert "RetrieveRecordChangeHistory(Target=@p1)" in call_url
    assert "@p1=" in call_url
    assert "accounts" in call_url
    assert _ACCOUNT_ID in call_url


@pytest.mark.asyncio
async def test_retrieve_record_change_history_audit_disabled_surfaces_error() -> None:
    """HTTP error (e.g., audit disabled) returns structured error, never raises."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = RetrieveRecordChangeHistoryInput(
        dataverse_url=_BASE_URL,
        entity_set_name="accounts",
        record_id=_ACCOUNT_ID,
    )

    error_resp = _mock_response(
        status_code=400,
        json_body={
            "error": {
                "code": "0x80044350",
                "message": "Auditing is not enabled for this organization.",
            }
        },
    )

    with (
        patch(
            "dataverse_mcp.tools.security.build_headers",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "dataverse_mcp.tools.security.request_with_retry",
            new=AsyncMock(return_value=error_resp),
        ),
    ):
        result = await dataverse_retrieve_record_change_history(params, ctx)

    data = json.loads(result)
    assert data["error"] is True
    assert "400" in data["message"] or "Auditing" in data["message"]


# ---------------------------------------------------------------------------
# Tool: dataverse_get_audit_details — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit_details_happy_path() -> None:
    """Returns AuditDetail for the given audit_id with correct bound URL."""
    audit_detail = {
        "@odata.type": "#Microsoft.Dynamics.CRM.AttributeAuditDetail",
        "AuditRecord": {
            "auditid": _AUDIT_ID,
            "createdon": "2024-06-01T12:00:00Z",
            "operation": 2,
            "objecttypecode": "account",
        },
        "OldValue": {"name": "Contoso"},
        "NewValue": {"name": "Contoso Ltd"},
        "InvalidNewValueAttributes": [],
    }
    api_body = {"@odata.context": "...", "AuditDetail": audit_detail}

    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = GetAuditDetailsInput(dataverse_url=_BASE_URL, audit_id=_AUDIT_ID)

    with (
        patch(
            "dataverse_mcp.tools.security.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.security.request_with_retry",
            new=AsyncMock(return_value=_mock_response(200, api_body)),
        ) as mock_request,
    ):
        result = await dataverse_get_audit_details(params, ctx)

    data = json.loads(result)
    assert data["audit_id"] == _AUDIT_ID
    assert data["audit_detail"]["@odata.type"] == "#Microsoft.Dynamics.CRM.AttributeAuditDetail"
    assert data["audit_detail"]["OldValue"]["name"] == "Contoso"

    # Verify bound function URL form
    call_url = mock_request.call_args[0][2]
    assert f"audits({_AUDIT_ID})" in call_url
    assert "Microsoft.Dynamics.CRM.RetrieveAuditDetails" in call_url
    # Must NOT use unbound form with AuditId parameter
    assert "AuditId=" not in call_url


@pytest.mark.asyncio
async def test_get_audit_details_not_found_surfaces_error() -> None:
    """HTTP 404 returns structured error, never raises."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = GetAuditDetailsInput(dataverse_url=_BASE_URL, audit_id=_AUDIT_ID)

    error_resp = _mock_response(
        status_code=404,
        json_body={
            "error": {
                "code": "0x80040217",
                "message": f"auditid With Id = {_AUDIT_ID} Does Not Exist",
            }
        },
    )

    with (
        patch(
            "dataverse_mcp.tools.security.build_headers",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "dataverse_mcp.tools.security.request_with_retry",
            new=AsyncMock(return_value=error_resp),
        ),
    ):
        result = await dataverse_get_audit_details(params, ctx)

    data = json.loads(result)
    assert data["error"] is True
    assert "404" in data["message"]


# ---------------------------------------------------------------------------
# Tool: dataverse_list_audit — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_audit_happy_path() -> None:
    """Returns paginated audit records with default columns."""
    records = [
        {
            "auditid": _AUDIT_ID,
            "createdon": "2024-06-01T12:00:00Z",
            "operation": 2,
            "action": 2,
            "objecttypecode": "account",
            "_userid_value": "cccc1111-dddd-eeee-ffff-000011112222",
            "_objectid_value": _ACCOUNT_ID,
            "transactionid": "aaaabbbb-cccc-dddd-eeee-000011112222",
        }
    ]

    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ListAuditInput(dataverse_url=_BASE_URL)

    with (
        patch(
            "dataverse_mcp.tools.security.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.security.paginate_records",
            new=AsyncMock(return_value=records),
        ) as mock_paginate,
    ):
        result = await dataverse_list_audit(params, ctx)

    data = json.loads(result)
    assert data["count"] == 1
    assert data["records"][0]["auditid"] == _AUDIT_ID
    assert "has_more" in data

    # Verify URL includes correct entity set and default select
    call_url = mock_paginate.call_args[0][0]
    assert "/audits?" in call_url
    assert "$select=" in call_url
    assert "auditid" in call_url


@pytest.mark.asyncio
async def test_list_audit_with_filter_and_orderby() -> None:
    """Filter and orderby parameters are encoded into the URL."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ListAuditInput(
        dataverse_url=_BASE_URL,
        filter="operation eq 2",
        orderby=["createdon desc"],
        top=10,
    )

    with (
        patch(
            "dataverse_mcp.tools.security.build_headers",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "dataverse_mcp.tools.security.paginate_records",
            new=AsyncMock(return_value=[]),
        ) as mock_paginate,
    ):
        result = await dataverse_list_audit(params, ctx)

    call_url = mock_paginate.call_args[0][0]
    assert "$filter=" in call_url
    assert "$orderby=" in call_url
    assert "createdon" in call_url

    data = json.loads(result)
    assert data["count"] == 0
    assert data["has_more"] is False
