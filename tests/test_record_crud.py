"""Unit tests for the three record CRUD tools and their input models.

Coverage:
- CreateRecordInput / UpdateRecordInput / DeleteRecordInput model validation
  (required fields, extra='forbid', GUID validation).
- dataverse_create_record GUID extraction from OData-EntityId header
  (mocked HTTP client + Context following the _make_app_ctx/_make_ctx pattern).
- dataverse_update_record success path.
- dataverse_delete_record success path.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import CreateRecordInput, DeleteRecordInput, UpdateRecordInput
from dataverse_mcp.tools.tables import (
    dataverse_create_record,
    dataverse_delete_record,
    dataverse_update_record,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_ENTITY_SET = "accounts"
_RECORD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

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
    """Return a mock FastMCP Context backed by *app_ctx*."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


# ---------------------------------------------------------------------------
# Input model validation — CreateRecordInput
# ---------------------------------------------------------------------------


def test_create_record_input_valid() -> None:
    """CreateRecordInput accepts required fields."""
    m = CreateRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        data={"name": "Contoso"},
    )
    assert m.entity_set_name == _ENTITY_SET
    assert m.data == {"name": "Contoso"}


def test_create_record_input_missing_entity_set() -> None:
    """CreateRecordInput rejects missing entity_set_name."""
    with pytest.raises(Exception):
        CreateRecordInput(
            dataverse_url=_BASE_URL,
            data={"name": "Contoso"},
        )


def test_create_record_input_missing_data() -> None:
    """CreateRecordInput rejects missing data."""
    with pytest.raises(Exception):
        CreateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
        )


def test_create_record_input_extra_field_forbidden() -> None:
    """CreateRecordInput rejects unknown extra fields."""
    with pytest.raises(Exception):
        CreateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            data={"name": "Contoso"},
            unknown_field="should_fail",
        )


def test_create_record_input_rejects_empty_data() -> None:
    """CreateRecordInput rejects an empty data dict (no columns to write)."""
    with pytest.raises(Exception):
        CreateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            data={},
        )


def test_update_record_input_rejects_empty_data() -> None:
    """UpdateRecordInput rejects an empty data dict (would be a silent no-op)."""
    with pytest.raises(Exception):
        UpdateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            record_id="00000000-0000-0000-0000-000000000001",
            data={},
        )


# ---------------------------------------------------------------------------
# Input model validation — UpdateRecordInput
# ---------------------------------------------------------------------------


def test_update_record_input_valid() -> None:
    """UpdateRecordInput accepts required fields including a valid GUID record_id."""
    m = UpdateRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        record_id=_RECORD_ID,
        data={"name": "Updated"},
    )
    assert m.record_id == _RECORD_ID


def test_update_record_input_invalid_guid() -> None:
    """UpdateRecordInput rejects a malformed record_id."""
    with pytest.raises(Exception):
        UpdateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            record_id="not-a-guid",
            data={"name": "Updated"},
        )


def test_update_record_input_extra_field_forbidden() -> None:
    """UpdateRecordInput rejects unknown extra fields."""
    with pytest.raises(Exception):
        UpdateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            record_id=_RECORD_ID,
            data={"name": "Updated"},
            surprise="nope",
        )


def test_update_record_input_missing_record_id() -> None:
    """UpdateRecordInput rejects missing record_id."""
    with pytest.raises(Exception):
        UpdateRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            data={"name": "Updated"},
        )


# ---------------------------------------------------------------------------
# Input model validation — DeleteRecordInput
# ---------------------------------------------------------------------------


def test_delete_record_input_valid() -> None:
    """DeleteRecordInput accepts required fields."""
    m = DeleteRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        record_id=_RECORD_ID,
    )
    assert m.entity_set_name == _ENTITY_SET
    assert m.record_id == _RECORD_ID


def test_delete_record_input_invalid_guid() -> None:
    """DeleteRecordInput rejects a malformed record_id."""
    with pytest.raises(Exception):
        DeleteRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            record_id="not-a-guid",
        )


def test_delete_record_input_extra_field_forbidden() -> None:
    """DeleteRecordInput rejects unknown extra fields."""
    with pytest.raises(Exception):
        DeleteRecordInput(
            dataverse_url=_BASE_URL,
            entity_set_name=_ENTITY_SET,
            record_id=_RECORD_ID,
            extra="no",
        )


def test_delete_record_input_missing_entity_set() -> None:
    """DeleteRecordInput rejects missing entity_set_name."""
    with pytest.raises(Exception):
        DeleteRecordInput(
            dataverse_url=_BASE_URL,
            record_id=_RECORD_ID,
        )


# ---------------------------------------------------------------------------
# dataverse_create_record — GUID extraction from OData-EntityId header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_record_extracts_guid_from_header() -> None:
    """dataverse_create_record must extract the GUID from OData-EntityId header."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = CreateRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        data={"name": "Contoso"},
    )

    # A plain create returns 204 + the OData-EntityId header (no body).
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 204
    mock_resp.headers = httpx.Headers({
        "OData-EntityId": f"{_BASE_URL}/api/data/v9.2/accounts({_RECORD_ID})",
    })
    mock_resp.raise_for_status = MagicMock()

    with (
        patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token", "Content-Type": "application/json"}),
        ),
        patch(
            "dataverse_mcp.tools.tables.request_with_retry",
            new=AsyncMock(return_value=mock_resp),
        ),
    ):
        result_str = await dataverse_create_record(params, ctx)

    result = json.loads(result_str)
    assert result.get("id") == _RECORD_ID, f"Expected id={_RECORD_ID!r}, got {result.get('id')!r}"
    assert result.get("created") is True


@pytest.mark.asyncio
async def test_create_record_errors_when_entity_id_header_absent() -> None:
    """dataverse_create_record returns an error (not an empty id) when the
    OData-EntityId header is missing, so a caller never gets a silent empty id."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = CreateRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        data={"name": "Contoso"},
    )

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 204
    mock_resp.headers = httpx.Headers({})
    mock_resp.raise_for_status = MagicMock()

    with (
        patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token", "Content-Type": "application/json"}),
        ),
        patch(
            "dataverse_mcp.tools.tables.request_with_retry",
            new=AsyncMock(return_value=mock_resp),
        ),
    ):
        result_str = await dataverse_create_record(params, ctx)

    result = json.loads(result_str)
    assert result.get("error") is True
    assert "id" in result.get("message", "").lower()


# ---------------------------------------------------------------------------
# dataverse_update_record — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_record_success() -> None:
    """dataverse_update_record returns updated=True and the record_id on success."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = UpdateRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        record_id=_RECORD_ID,
        data={"name": "Updated Name"},
    )

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 204
    mock_resp.raise_for_status = MagicMock()

    with (
        patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token", "Content-Type": "application/json"}),
        ),
        patch(
            "dataverse_mcp.tools.tables.request_with_retry",
            new=AsyncMock(return_value=mock_resp),
        ),
    ):
        result_str = await dataverse_update_record(params, ctx)

    result = json.loads(result_str)
    assert result.get("updated") is True
    assert result.get("id") == _RECORD_ID


# ---------------------------------------------------------------------------
# dataverse_delete_record — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_record_success() -> None:
    """dataverse_delete_record returns deleted=True and the record_id on success."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = DeleteRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        record_id=_RECORD_ID,
    )

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 204
    mock_resp.raise_for_status = MagicMock()

    with (
        patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.tables.request_with_retry",
            new=AsyncMock(return_value=mock_resp),
        ),
    ):
        result_str = await dataverse_delete_record(params, ctx)

    result = json.loads(result_str)
    assert result.get("deleted") is True
    assert result.get("id") == _RECORD_ID


# ---------------------------------------------------------------------------
# Error contract — HTTP failure returns JSON error shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_record_http_error_returns_error_json() -> None:
    """dataverse_create_record returns JSON error on HTTPStatusError."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = CreateRecordInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        data={"name": "Contoso"},
    )

    error_response = MagicMock(spec=httpx.Response)
    error_response.status_code = 400
    error_response.text = "Bad Request"
    error_response.headers = httpx.Headers({"Content-Type": "application/json"})
    try:
        error_response.json.return_value = {"error": {"message": "Invalid attribute"}}
    except Exception:
        pass
    http_error = httpx.HTTPStatusError(
        message="HTTP 400",
        request=MagicMock(spec=httpx.Request),
        response=error_response,
    )

    with (
        patch(
            "dataverse_mcp.tools.tables.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token", "Content-Type": "application/json"}),
        ),
        patch(
            "dataverse_mcp.tools.tables.request_with_retry",
            new=AsyncMock(side_effect=http_error),
        ),
    ):
        result_str = await dataverse_create_record(params, ctx)

    result = json.loads(result_str)
    assert result.get("error") is True
    assert "message" in result
