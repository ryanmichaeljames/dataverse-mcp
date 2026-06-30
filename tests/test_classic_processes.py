"""Unit tests for classic process tools and input models.

Coverage:
- ListProcessesInput / ActivateProcessInput / DeactivateProcessInput model validation.
- dataverse_list_processes: category/type/filter query construction.
- dataverse_activate_process: PATCH body sets statecode=1, statuscode=2.
- dataverse_deactivate_process: PATCH body sets statecode=0, statuscode=1.
- HTTPStatusError on activate/deactivate returns structured error (not raise).
- TimeoutException returns structured transient error.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import (
    ActivateProcessInput,
    DeactivateProcessInput,
    ListProcessesInput,
)
from dataverse_mcp.tools.solutions import (
    dataverse_activate_process,
    dataverse_deactivate_process,
    dataverse_list_processes,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_PROCESS_ID = "11111111-2222-3333-4444-555555555555"
_API_VERSION = "v9.2"


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


def _mock_response(
    status_code: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# Input model validation — ListProcessesInput
# ---------------------------------------------------------------------------


def test_list_processes_input_defaults() -> None:
    params = ListProcessesInput(dataverse_url=_BASE_URL)
    assert params.category is None
    assert params.type == 1
    assert params.filter is None
    assert params.select is None
    assert params.top == 50


def test_list_processes_input_category_filter() -> None:
    params = ListProcessesInput(dataverse_url=_BASE_URL, category=2)
    assert params.category == 2


def test_list_processes_input_category_bounds() -> None:
    # Valid boundary values
    ListProcessesInput(dataverse_url=_BASE_URL, category=0)
    ListProcessesInput(dataverse_url=_BASE_URL, category=5)
    # Out-of-bounds
    with pytest.raises(Exception):
        ListProcessesInput(dataverse_url=_BASE_URL, category=-1)
    with pytest.raises(Exception):
        ListProcessesInput(dataverse_url=_BASE_URL, category=6)


def test_list_processes_input_type_none() -> None:
    params = ListProcessesInput(dataverse_url=_BASE_URL, type=None)
    assert params.type is None


def test_list_processes_input_extra_forbidden() -> None:
    with pytest.raises(Exception):
        ListProcessesInput(dataverse_url=_BASE_URL, unknown_field="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Input model validation — ActivateProcessInput
# ---------------------------------------------------------------------------


def test_activate_process_input_valid() -> None:
    params = ActivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID)
    assert params.process_id == _PROCESS_ID


def test_activate_process_input_invalid_guid() -> None:
    # "not-a-guid" is too short for min_length=36 and fails the GUID pattern — either way invalid
    with pytest.raises(Exception):
        ActivateProcessInput(dataverse_url=_BASE_URL, process_id="not-a-guid")


def test_activate_process_input_invalid_guid_format() -> None:
    # 36 chars but not a valid GUID format — must fail GUID pattern check
    with pytest.raises(Exception, match="valid GUID"):
        ActivateProcessInput(dataverse_url=_BASE_URL, process_id="x" * 36)


def test_activate_process_input_required_fields() -> None:
    with pytest.raises(Exception):
        ActivateProcessInput(dataverse_url=_BASE_URL)  # type: ignore[call-arg]


def test_activate_process_input_extra_forbidden() -> None:
    with pytest.raises(Exception):
        ActivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID, extra="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Input model validation — DeactivateProcessInput
# ---------------------------------------------------------------------------


def test_deactivate_process_input_valid() -> None:
    params = DeactivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID)
    assert params.process_id == _PROCESS_ID


def test_deactivate_process_input_invalid_guid() -> None:
    # "bad-guid" is too short for min_length=36 and fails the GUID pattern — either way invalid
    with pytest.raises(Exception):
        DeactivateProcessInput(dataverse_url=_BASE_URL, process_id="bad-guid")


# ---------------------------------------------------------------------------
# dataverse_list_processes — query construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_processes_default_filter() -> None:
    """Default call: type=1, no category → category le 4 filter applied."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ListProcessesInput(dataverse_url=_BASE_URL)

    captured_urls: list[str] = []

    async def fake_paginate(url: str, headers: dict, top: int, client: object) -> list:
        captured_urls.append(url)
        return []

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.paginate_records", side_effect=fake_paginate),
    ):
        result_str = await dataverse_list_processes(params, ctx)

    result = json.loads(result_str)
    assert result["count"] == 0
    assert result["has_more"] is False

    assert captured_urls, "paginate_records was not called"
    url = captured_urls[0]
    assert "category+le+4" in url or "category%20le%204" in url or "category le 4" in url, (
        f"Expected 'category le 4' in URL, got: {url}"
    )
    assert "type+eq+1" in url or "type%20eq%201" in url or "type eq 1" in url, (
        f"Expected 'type eq 1' in URL, got: {url}"
    )


@pytest.mark.asyncio
async def test_list_processes_category_filter() -> None:
    """When category=3 (Action), only category eq 3 is used (not category le 4)."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ListProcessesInput(dataverse_url=_BASE_URL, category=3)

    captured_urls: list[str] = []

    async def fake_paginate(url: str, headers: dict, top: int, client: object) -> list:
        captured_urls.append(url)
        return []

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.paginate_records", side_effect=fake_paginate),
    ):
        await dataverse_list_processes(params, ctx)

    url = captured_urls[0]
    assert "category+eq+3" in url or "category%20eq%203" in url or "category eq 3" in url, (
        f"Expected 'category eq 3' in URL, got: {url}"
    )
    # Must NOT include the le-4 guard
    assert "le+4" not in url and "le%204" not in url and "le 4" not in url, (
        f"Unexpected 'le 4' filter in URL: {url}"
    )


@pytest.mark.asyncio
async def test_list_processes_type_none_omits_type_filter() -> None:
    """When type=None, no type filter is added to the query."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ListProcessesInput(dataverse_url=_BASE_URL, type=None)

    captured_urls: list[str] = []

    async def fake_paginate(url: str, headers: dict, top: int, client: object) -> list:
        captured_urls.append(url)
        return []

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.paginate_records", side_effect=fake_paginate),
    ):
        await dataverse_list_processes(params, ctx)

    url = captured_urls[0]
    assert "type+eq" not in url and "type%20eq" not in url and "type eq" not in url, (
        f"Unexpected type filter in URL: {url}"
    )


# ---------------------------------------------------------------------------
# dataverse_activate_process — PATCH body and success response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_process_patch_body_and_response() -> None:
    """activate_process sends statecode=1, statuscode=2 and returns updated=True."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ActivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID)

    captured_calls: list[dict] = []

    async def fake_request(client, method, url, *, json=None, headers=None, timeout=None):
        captured_calls.append({"method": method, "url": url, "json": json})
        return _mock_response(204)

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.request_with_retry", side_effect=fake_request),
    ):
        result_str = await dataverse_activate_process(params, ctx)

    result = json.loads(result_str)
    assert result["updated"] is True
    assert result["process_id"] == _PROCESS_ID
    assert result["statecode"] == 1
    assert result["statuscode"] == 2

    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["method"] == "PATCH"
    assert _PROCESS_ID in call["url"]
    assert call["json"] == {"statecode": 1, "statuscode": 2}


@pytest.mark.asyncio
async def test_activate_process_http_error_returns_structured_error() -> None:
    """HTTP 400 on activate returns {"error": True, "message": ...} without raising."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ActivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID)

    error_resp = _mock_response(400, json_body={"error": {"message": "Cannot activate"}})

    async def fake_request(client, method, url, *, json=None, headers=None, timeout=None):
        raise httpx.HTTPStatusError("400", request=MagicMock(), response=error_resp)

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.request_with_retry", side_effect=fake_request),
        patch(
            "dataverse_mcp.tools.solutions.extract_error_message",
            return_value="Cannot activate",
        ),
    ):
        result_str = await dataverse_activate_process(params, ctx)

    result = json.loads(result_str)
    assert result["error"] is True
    assert "Cannot activate" in result["message"]


@pytest.mark.asyncio
async def test_activate_process_timeout_returns_transient_error() -> None:
    """TimeoutException returns is_transient=True error, not an uncaught exception."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = ActivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID)

    async def fake_request(client, method, url, *, json=None, headers=None, timeout=None):
        raise httpx.TimeoutException("timed out")

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.request_with_retry", side_effect=fake_request),
    ):
        result_str = await dataverse_activate_process(params, ctx)

    result = json.loads(result_str)
    assert result["error"] is True
    assert result.get("is_transient") is True


# ---------------------------------------------------------------------------
# dataverse_deactivate_process — PATCH body and success response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_process_patch_body_and_response() -> None:
    """deactivate_process sends statecode=0, statuscode=1 and returns updated=True."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = DeactivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID)

    captured_calls: list[dict] = []

    async def fake_request(client, method, url, *, json=None, headers=None, timeout=None):
        captured_calls.append({"method": method, "url": url, "json": json})
        return _mock_response(204)

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.request_with_retry", side_effect=fake_request),
    ):
        result_str = await dataverse_deactivate_process(params, ctx)

    result = json.loads(result_str)
    assert result["updated"] is True
    assert result["process_id"] == _PROCESS_ID
    assert result["statecode"] == 0
    assert result["statuscode"] == 1

    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["method"] == "PATCH"
    assert _PROCESS_ID in call["url"]
    assert call["json"] == {"statecode": 0, "statuscode": 1}


@pytest.mark.asyncio
async def test_deactivate_process_http_error_returns_structured_error() -> None:
    """HTTP 400 on deactivate returns {"error": True, "message": ...} without raising."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = DeactivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID)

    error_resp = _mock_response(400, json_body={"error": {"message": "Cannot deactivate"}})

    async def fake_request(client, method, url, *, json=None, headers=None, timeout=None):
        raise httpx.HTTPStatusError("400", request=MagicMock(), response=error_resp)

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.request_with_retry", side_effect=fake_request),
        patch(
            "dataverse_mcp.tools.solutions.extract_error_message",
            return_value="Cannot deactivate",
        ),
    ):
        result_str = await dataverse_deactivate_process(params, ctx)

    result = json.loads(result_str)
    assert result["error"] is True
    assert "Cannot deactivate" in result["message"]


@pytest.mark.asyncio
async def test_deactivate_process_timeout_returns_transient_error() -> None:
    """TimeoutException returns is_transient=True error, not an uncaught exception."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)
    params = DeactivateProcessInput(dataverse_url=_BASE_URL, process_id=_PROCESS_ID)

    async def fake_request(client, method, url, *, json=None, headers=None, timeout=None):
        raise httpx.TimeoutException("timed out")

    with (
        patch("dataverse_mcp.tools.solutions.build_headers", new_callable=AsyncMock, return_value={}),
        patch("dataverse_mcp.tools.solutions.request_with_retry", side_effect=fake_request),
    ):
        result_str = await dataverse_deactivate_process(params, ctx)

    result = json.loads(result_str)
    assert result["error"] is True
    assert result.get("is_transient") is True
