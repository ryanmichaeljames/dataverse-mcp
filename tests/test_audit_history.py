"""Unit tests for audit history tools and input models.

Coverage:
- RetrieveRecordChangeHistoryInput / GetAuditDetailsInput / ListAuditInput
  model validation (required fields, GUID format, extra='forbid').
- dataverse_retrieve_record_change_history happy path — AuditDetailCollection response,
  with the org-level audit-CONFIGURATION rows partitioned out of the changes.
- dataverse_retrieve_record_change_history keeps an entry whose @odata.type it does not
  recognize as a RESULT rather than dropping it, and reports detail_types over the page.
- dataverse_retrieve_record_change_history treats a typeless entry as configuration ONLY
  when AuditRecord._objectid_value is the all-zero GUID; every other typeless shape is
  kept as a result and counted in unclassified_typeless_count. The known configuration
  actions (105/107/110) still classify as configuration — the check is behaviour-neutral.
- audit_configuration_events_count: 0 is a normal outcome — the rows arrive only when an
  audit-configuration change falls inside the target's own history window.
- dataverse_retrieve_record_change_history suppresses the TotalRecordCount = -1 sentinel.
- dataverse_retrieve_record_change_history reports has_more when the client-side trim cut
  genuine changes, even though the server said MoreRecords: false.
- dataverse_retrieve_record_change_history degrades to normalized: false + raw_response
  when the AuditDetailCollection container is missing — never a fabricated empty list.
- dataverse_retrieve_record_change_history error path — HTTP error surfaced structurally.
- dataverse_get_audit_details happy path — bound function URL form.
- dataverse_get_audit_details 404 path — audit record not found.
- dataverse_list_audit happy path — paginated records with filter/orderby.
- URL construction verified: RetrieveRecordChangeHistory uses alias param @p1;
  RetrieveAuditDetails uses bound function URL form.
"""

import json
from typing import Any
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
_ALL_ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def _configuration_row(action: int = 110, objecttypecode: str = "account") -> dict:
    """An org-level audit-CONFIGURATION row as the platform really sends it.

    No ``@odata.type`` at all, ``AuditRecord`` and nothing else, and an ALL-ZERO
    ``_objectid_value`` because the row is about auditing rather than about a record.
    It carries the TARGET TABLE's ``objecttypecode`` (live: 'account', 'systemuser' —
    NOT 'organization') and an ``action`` 105 row has no ``attributemask`` key at all;
    neither field takes any part in the classification.
    """
    return {
        "AuditRecord": {
            "auditid": _AUDIT_ID,
            "action": action,
            "objecttypecode": objecttypecode,
            "_objectid_value": _ALL_ZERO_GUID,
        }
    }


_CONFIGURATION_ROW = _configuration_row()


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


def _attribute_detail(name: str) -> dict:
    """A genuine AttributeAuditDetail — the shape a real change arrives in."""
    return {
        "@odata.type": "#Microsoft.Dynamics.CRM.AttributeAuditDetail",
        "AuditRecord": {
            "auditid": _AUDIT_ID,
            "createdon": "2024-06-01T12:00:00Z",
            "operation": 2,
            "action": 2,
            "objecttypecode": "account",
        },
        "OldValue": {"name": f"Old {name}"},
        "NewValue": {"name": f"New {name}"},
    }


async def _run_record_change_history(body: dict, **overrides: Any) -> dict:
    """Call the tool against a mocked response body and return the parsed result."""
    ctx = _make_ctx(_make_app_ctx())
    params = RetrieveRecordChangeHistoryInput(
        dataverse_url=_BASE_URL,
        entity_set_name="accounts",
        record_id=_ACCOUNT_ID,
        **overrides,
    )
    with (
        patch(
            "dataverse_mcp.tools.security.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.security.request_with_retry",
            new=AsyncMock(return_value=_mock_response(200, body)),
        ),
    ):
        return json.loads(await dataverse_retrieve_record_change_history(params, ctx))


@pytest.mark.asyncio
async def test_retrieve_record_change_history_happy_path() -> None:
    """Configuration rows are partitioned out of count/has_more, not counted as changes.

    The two org-level audit-CONFIGURATION rows accompany EVERY response whatever the
    target, so counting the raw list reported changes that never happened. They are
    surfaced under their own key rather than dropped, and their POSITION in the list is
    deliberately not the leading one here — classification is by shape, never by index.
    """
    api_body = {
        "AuditDetailCollection": {
            "AuditDetails": [
                _CONFIGURATION_ROW,
                _attribute_detail("Corp"),
                _CONFIGURATION_ROW,
            ],
            "MoreRecords": False,
            "PagingCookie": None,
            "TotalRecordCount": 1,
        }
    }

    ctx = _make_ctx(_make_app_ctx())
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
    assert data["normalized"] is True
    assert data["count"] == 1, "an audit-configuration row was counted as a change"
    assert data["has_more"] is False
    assert data["entity_set_name"] == "accounts"
    assert data["record_id"] == _ACCOUNT_ID
    assert len(data["audit_details"]) == 1
    assert data["audit_details"][0]["@odata.type"].endswith("AttributeAuditDetail")
    assert data["detail_types"] == {"#Microsoft.Dynamics.CRM.AttributeAuditDetail": 1}
    assert data["unclassified_typeless_count"] == 0
    assert data["audit_configuration_events_count"] == 2
    assert data["audit_configuration_events"] == [_CONFIGURATION_ROW, _CONFIGURATION_ROW]
    assert "audit_configuration_events" in data["message"]
    assert data["total_record_count"] == 1

    # Verify URL encodes Target as alias @p1 with relative @odata.id
    call_url = mock_request.call_args[0][2]
    assert "RetrieveRecordChangeHistory(Target=@p1)" in call_url
    assert "@p1=" in call_url
    assert "accounts" in call_url
    assert _ACCOUNT_ID in call_url


@pytest.mark.asyncio
async def test_retrieve_record_change_history_keeps_unrecognized_detail_types() -> None:
    """An unknown @odata.type is an unknown RESULT and must never be dropped.

    A record-scoped call spans more subtypes than a column-scoped one, so filtering to
    AttributeAuditDetail would silently lose sharing, relationship and access history.
    """
    entries = [
        {"@odata.type": "#Microsoft.Dynamics.CRM.ShareAuditDetail", "AuditRecord": {}},
        {"@odata.type": "#Microsoft.Dynamics.CRM.SomethingNotYetInvented"},
        "not-an-object",
        _CONFIGURATION_ROW,
    ]
    data = await _run_record_change_history(
        {"AuditDetailCollection": {"AuditDetails": entries, "MoreRecords": False}}
    )

    assert data["count"] == 3, f"an entry was dropped: {data['audit_details']}"
    assert data["audit_details"] == entries[:3]
    assert data["audit_configuration_events_count"] == 1
    # detail_types is the record-scoped tool's census of the WIDER subtype mix it sees
    # (ShareAuditDetail is live-confirmed here), matching its column-scoped sibling.
    assert data["detail_types"] == {
        "#Microsoft.Dynamics.CRM.ShareAuditDetail": 1,
        "#Microsoft.Dynamics.CRM.SomethingNotYetInvented": 1,
        "non-object": 1,
    }
    # Nothing typeless was kept: a non-object entry is not a typeless entry.
    assert data["unclassified_typeless_count"] == 0


@pytest.mark.asyncio
async def test_retrieve_record_change_history_suppresses_negative_total() -> None:
    """TotalRecordCount = -1 means 'not counted' and is omitted, never passed through.

    Also pins that ZERO audit-configuration rows is a normal answer: they arrive only
    when an audit-configuration change falls inside the target's history window, so a
    record created after the last such change gets none and no message is added.
    """
    data = await _run_record_change_history(
        {
            "AuditDetailCollection": {
                "AuditDetails": [_attribute_detail("Corp")],
                "MoreRecords": False,
                "TotalRecordCount": -1,
            }
        }
    )

    assert "total_record_count" not in data
    assert data["count"] == 1
    assert data["audit_configuration_events_count"] == 0
    assert data["audit_configuration_events"] == []
    assert "message" not in data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audit_record,reason",
    [
        pytest.param(
            {"action": 105, "objecttypecode": "account"},
            "no _objectid_value at all — nothing proves this is configuration",
            id="objectid-absent",
        ),
        pytest.param(
            {"action": 2, "_objectid_value": _ACCOUNT_ID},
            "a REAL record id: every genuine event points at a record",
            id="real-objectid",
        ),
        pytest.param(
            {"_objectid_value": None},
            "a null objectid is not the all-zero GUID",
            id="null-objectid",
        ),
        pytest.param(
            "not-an-object", "AuditRecord is not a dict", id="audit-record-not-a-dict"
        ),
    ],
)
async def test_retrieve_record_change_history_keeps_typeless_rows_without_a_zero_objectid(
    audit_record: Any, reason: str
) -> None:
    """The all-zero objectid is REQUIRED for configuration — every edge keeps the row.

    Live, every typeless row carried the all-zero AuditRecord._objectid_value and every
    typed row a real record id, across 11 rows with no divergence. Requiring it can only
    move a row OUT of configuration and INTO the results, which is the safe direction:
    a bare genuine event (base AuditDetail declares only AuditRecord, and OData omits
    @odata.type when the instance type equals the declared type) is surfaced instead of
    being silently dropped.
    """
    entry = {"AuditRecord": audit_record}
    data = await _run_record_change_history(
        {
            "AuditDetailCollection": {
                "AuditDetails": [_CONFIGURATION_ROW, entry],
                "MoreRecords": False,
            }
        }
    )

    assert data["audit_details"] == [entry], reason
    assert data["count"] == 1
    # It was kept, and the caller is told the tool met something it could not name.
    assert data["unclassified_typeless_count"] == 1
    assert data["detail_types"] == {"unspecified": 1}
    # ...and the row that DOES carry the all-zero objectid is still configuration.
    assert data["audit_configuration_events"] == [_CONFIGURATION_ROW]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [105, 107, 110])
@pytest.mark.parametrize(
    "objectid",
    [
        pytest.param(_ALL_ZERO_GUID, id="plain"),
        pytest.param(_ALL_ZERO_GUID.upper(), id="uppercase"),
        pytest.param(f"{{{_ALL_ZERO_GUID}}}", id="braced"),
    ],
)
async def test_retrieve_record_change_history_known_configuration_rows_still_classify(
    action: int, objectid: str
) -> None:
    """BEHAVIOUR-NEUTRAL: every row observed as configuration still classifies as one.

    The objectid check is defence in depth, not a reclassification — the known
    audit-configuration actions (105 Entity Audit Started, 107, 110) all carry the
    all-zero objectid live, so nothing moves today. The GUID is compared
    case-insensitively and tolerates the optional braces.
    """
    row = _configuration_row(action=action)
    row["AuditRecord"]["_objectid_value"] = objectid
    data = await _run_record_change_history(
        {"AuditDetailCollection": {"AuditDetails": [row], "MoreRecords": False}}
    )

    assert data["audit_details"] == []
    assert data["count"] == 0
    assert data["unclassified_typeless_count"] == 0
    assert data["audit_configuration_events"] == [row]
    assert data["audit_configuration_events_count"] == 1


@pytest.mark.asyncio
async def test_retrieve_record_change_history_has_more_covers_the_trim() -> None:
    """has_more is true when the client-side trim cut genuine rows, MoreRecords or not.

    PagingInfo is not sent, so `top` is applied here; reporting only the server's flag
    told a caller whose page had been cut that they already had everything.
    """
    data = await _run_record_change_history(
        {
            "AuditDetailCollection": {
                "AuditDetails": [
                    _attribute_detail("One"),
                    _CONFIGURATION_ROW,
                    _attribute_detail("Two"),
                ],
                "MoreRecords": False,
            }
        },
        top=1,
    )

    assert data["count"] == 1
    assert data["has_more"] is True

    # The configuration rows are NOT changes, so trimming to a top that covers every
    # genuine row must not claim there is more.
    exact = await _run_record_change_history(
        {
            "AuditDetailCollection": {
                "AuditDetails": [_attribute_detail("One"), _CONFIGURATION_ROW],
                # A truthy non-bool must not be read as the server saying "more".
                "MoreRecords": 1,
            }
        },
        top=1,
    )
    assert exact["count"] == 1
    assert exact["has_more"] is False


@pytest.mark.asyncio
async def test_retrieve_record_change_history_missing_container_is_not_no_changes() -> None:
    """A payload without AuditDetailCollection returns the raw body, not an empty list."""
    data = await _run_record_change_history({"@odata.context": "...", "Unexpected": 1})

    assert data.get("error") is not True
    assert data["normalized"] is False
    assert "audit_details" not in data, "an empty change list was fabricated"
    assert "count" not in data
    assert data["raw_response"] == {"Unexpected": 1}
    assert "AuditDetailCollection" in data["message"]


@pytest.mark.asyncio
async def test_retrieve_record_change_history_http_error_surfaces_structurally() -> None:
    """An HTTP error returns a structured error, never raises.

    The wrong (singular) entity set is the live-confirmed failure here — a 404 NAMING
    the bad segment. Auditing being disabled is NOT one of these: it is live-confirmed
    to return HTTP 200 with zero genuine changes.
    """
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = RetrieveRecordChangeHistoryInput(
        dataverse_url=_BASE_URL,
        entity_set_name="account",
        record_id=_ACCOUNT_ID,
    )

    error_resp = _mock_response(
        status_code=404,
        json_body={
            "error": {
                "code": "0x80060888",
                "message": "Resource not found for the segment 'account'.",
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
    assert "404" in data["message"]
    assert "account" in data["message"]


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
