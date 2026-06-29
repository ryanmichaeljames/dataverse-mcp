"""Unit tests for the write/delete safeguard enforcement in dataverse_execute_batch.

Regression coverage for the bug where a DELETE operation inside a $batch request
bypassed the delete safeguard: the tool gated all non-GET methods on
DATAVERSE_ALLOW_WRITE only, so a batch containing DELETE executed whenever write
was enabled — regardless of DATAVERSE_ALLOW_DELETE.

Contract now enforced:
- POST/PUT/PATCH in a batch require DATAVERSE_ALLOW_WRITE=true.
- DELETE in a batch requires DATAVERSE_ALLOW_DELETE=true.
- Both checks are independent; a pure-DELETE batch needs only ALLOW_DELETE.

These checks return early (before any HTTP call), so the rejection paths need no
HTTP mocking. One happy-path test mocks the HTTP layer to prove a DELETE is NOT
over-blocked when ALLOW_DELETE=true.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import ExecuteBatchInput
from dataverse_mcp.tools.tables import dataverse_execute_batch

_BASE_URL = "https://yourorg.crm.dynamics.com"
_RECORD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _make_ctx() -> MagicMock:
    """Return a mock FastMCP Context backed by a minimal AppContext."""
    app_ctx = AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _delete_op() -> dict:
    return {"method": "DELETE", "url": f"/accounts({_RECORD_ID})"}


def _post_op() -> dict:
    return {"method": "POST", "url": "/accounts", "body": {"name": "Acme"}}


# ---------------------------------------------------------------------------
# DELETE must require DATAVERSE_ALLOW_DELETE (the regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_blocked_when_delete_disabled_even_if_write_enabled(monkeypatch) -> None:
    """A batch DELETE must be rejected when ALLOW_DELETE is off, even with ALLOW_WRITE on."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.delenv("DATAVERSE_ALLOW_DELETE", raising=False)

    params = ExecuteBatchInput(dataverse_url=_BASE_URL, operations=[_delete_op()])
    result = json.loads(await dataverse_execute_batch(params, _make_ctx()))

    assert result["error"] is True
    assert "DATAVERSE_ALLOW_DELETE" in result["message"]


@pytest.mark.asyncio
async def test_delete_blocked_when_delete_explicitly_false(monkeypatch) -> None:
    """ALLOW_DELETE=false must block a batch DELETE regardless of write flag."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.setenv("DATAVERSE_ALLOW_DELETE", "false")

    params = ExecuteBatchInput(dataverse_url=_BASE_URL, operations=[_delete_op()])
    result = json.loads(await dataverse_execute_batch(params, _make_ctx()))

    assert result["error"] is True
    assert "DATAVERSE_ALLOW_DELETE" in result["message"]


@pytest.mark.asyncio
async def test_mixed_batch_delete_blocked_when_only_write_enabled(monkeypatch) -> None:
    """A batch mixing POST and DELETE is blocked on the DELETE when delete is disabled."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.delenv("DATAVERSE_ALLOW_DELETE", raising=False)

    params = ExecuteBatchInput(
        dataverse_url=_BASE_URL, operations=[_post_op(), _delete_op()]
    )
    result = json.loads(await dataverse_execute_batch(params, _make_ctx()))

    assert result["error"] is True
    assert "DATAVERSE_ALLOW_DELETE" in result["message"]


# ---------------------------------------------------------------------------
# POST/PUT/PATCH still require DATAVERSE_ALLOW_WRITE (preserved behavior)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_blocked_when_write_disabled(monkeypatch) -> None:
    """A batch POST must be rejected when ALLOW_WRITE is off."""
    monkeypatch.delenv("DATAVERSE_ALLOW_WRITE", raising=False)
    monkeypatch.delenv("DATAVERSE_ALLOW_DELETE", raising=False)

    params = ExecuteBatchInput(dataverse_url=_BASE_URL, operations=[_post_op()])
    result = json.loads(await dataverse_execute_batch(params, _make_ctx()))

    assert result["error"] is True
    assert "DATAVERSE_ALLOW_WRITE" in result["message"]


@pytest.mark.asyncio
async def test_write_not_blocked_by_delete_flag(monkeypatch) -> None:
    """A pure POST batch must not be blocked by the delete safeguard."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.delenv("DATAVERSE_ALLOW_DELETE", raising=False)

    params = ExecuteBatchInput(dataverse_url=_BASE_URL, operations=[_post_op()])

    with patch(
        "dataverse_mcp.tools.tables.build_headers", new=AsyncMock(return_value={})
    ), patch(
        "dataverse_mcp.tools.tables.request_with_retry", new=AsyncMock()
    ) as mock_req, patch(
        "dataverse_mcp.tools.tables.parse_batch_response", return_value=[]
    ):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "multipart/mixed; boundary=resp"}
        resp.text = ""
        mock_req.return_value = resp

        result = json.loads(await dataverse_execute_batch(params, _make_ctx()))

    assert "error" not in result or result.get("error") is not True
    assert mock_req.await_count == 1


# ---------------------------------------------------------------------------
# Happy path: DELETE is NOT over-blocked when ALLOW_DELETE=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_allowed_passes_guard_and_executes(monkeypatch) -> None:
    """A batch DELETE proceeds to execution when ALLOW_DELETE=true."""
    monkeypatch.setenv("DATAVERSE_ALLOW_WRITE", "true")
    monkeypatch.setenv("DATAVERSE_ALLOW_DELETE", "true")

    params = ExecuteBatchInput(dataverse_url=_BASE_URL, operations=[_delete_op()])

    with patch(
        "dataverse_mcp.tools.tables.build_headers", new=AsyncMock(return_value={})
    ), patch(
        "dataverse_mcp.tools.tables.request_with_retry", new=AsyncMock()
    ) as mock_req, patch(
        "dataverse_mcp.tools.tables.parse_batch_response",
        return_value=[{"status_code": 204, "body": None}],
    ):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "multipart/mixed; boundary=resp"}
        resp.text = ""
        mock_req.return_value = resp

        result = json.loads(await dataverse_execute_batch(params, _make_ctx()))

    assert "error" not in result or result.get("error") is not True
    assert result["count"] == 1
    assert mock_req.await_count == 1


# ---------------------------------------------------------------------------
# GET-only batches need no safeguard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_only_batch_needs_no_safeguard(monkeypatch) -> None:
    """A read-only batch executes with neither write nor delete enabled."""
    monkeypatch.delenv("DATAVERSE_ALLOW_WRITE", raising=False)
    monkeypatch.delenv("DATAVERSE_ALLOW_DELETE", raising=False)

    params = ExecuteBatchInput(
        dataverse_url=_BASE_URL, operations=[{"method": "GET", "url": "/accounts"}]
    )

    with patch(
        "dataverse_mcp.tools.tables.build_headers", new=AsyncMock(return_value={})
    ), patch(
        "dataverse_mcp.tools.tables.request_with_retry", new=AsyncMock()
    ) as mock_req, patch(
        "dataverse_mcp.tools.tables.parse_batch_response", return_value=[]
    ):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "multipart/mixed; boundary=resp"}
        resp.text = ""
        mock_req.return_value = resp

        result = json.loads(await dataverse_execute_batch(params, _make_ctx()))

    assert "error" not in result or result.get("error") is not True
    assert mock_req.await_count == 1
