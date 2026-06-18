"""Unit tests for honest `published` flag in dataverse_create_view and
dataverse_set_app_sitemap.

Acceptance criteria:
- Success path: published == True in the JSON response.
- Publish raises httpx.HTTPStatusError: published == False, but the record
  was still created/updated (created/updated == True, view_id/sitemap_id present).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import CreateViewInput, SetAppSitemapInput
from dataverse_mcp.tools.apps import dataverse_set_app_sitemap
from dataverse_mcp.tools.views import dataverse_create_view

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://yourorg.crm.dynamics.com"
_TABLE = "account"
_VIEW_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_APP_ID = "11111111-2222-3333-4444-555555555555"
_SITEMAP_ID = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"

_ENTITY_INFO = {
    "otc": 1,
    "primary_id": "accountid",
    "primary_name": "name",
    "entity_set": "accounts",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_ctx() -> AppContext:
    """Return a minimal AppContext with a mock http_client."""
    return AppContext(
        credential=None,
        auth_type="azure_cli",
        fallback_dataverse_url=_BASE_URL,
        http_client=MagicMock(spec=httpx.AsyncClient),
    )


def _make_ctx(app_ctx: AppContext) -> MagicMock:
    """Return a mock FastMCP Context backed by *app_ctx*."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _http_status_error(status_code: int = 500) -> httpx.HTTPStatusError:
    """Build a minimal httpx.HTTPStatusError for mocking."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = "Internal Server Error"
    return httpx.HTTPStatusError(
        message=f"HTTP {status_code}",
        request=MagicMock(spec=httpx.Request),
        response=response,
    )


def _post_response_with_view_id(view_id: str) -> MagicMock:
    """Return a mock POST response carrying OData-EntityId with *view_id*."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 204
    resp.headers = httpx.Headers({
        "OData-EntityId": f"{_BASE_URL}/api/data/v9.2/savedqueries({view_id})",
    })
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# dataverse_create_view — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_view_published_true_on_success() -> None:
    """When publish succeeds, published must be True and created must be True."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = CreateViewInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        name="Test View",
        columns=["name", "accountid"],
    )

    with (
        patch(
            "dataverse_mcp.tools.views.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.views._resolve_entity_view_info",
            new=AsyncMock(return_value=_ENTITY_INFO),
        ),
        patch(
            "dataverse_mcp.tools.views._resolve_columns",
            new=AsyncMock(return_value={"name": "Name", "accountid": "Account ID"}),
        ),
        patch(
            "dataverse_mcp.tools.views.request_with_retry",
            new=AsyncMock(return_value=_post_response_with_view_id(_VIEW_ID)),
        ),
        patch(
            "dataverse_mcp.tools.views._publish_table",
            new=AsyncMock(return_value=None),
        ) as mock_publish,
    ):
        result_str = await dataverse_create_view(params, ctx)

    result = json.loads(result_str)
    assert result.get("created") is True
    assert result.get("published") is True
    assert result.get("view_id") == _VIEW_ID
    mock_publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# dataverse_create_view — publish failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_view_published_false_when_publish_raises() -> None:
    """When publish raises HTTPStatusError, published must be False but created True."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = CreateViewInput(
        dataverse_url=_BASE_URL,
        table_logical_name=_TABLE,
        name="Test View",
        columns=["name", "accountid"],
    )

    with (
        patch(
            "dataverse_mcp.tools.views.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.views._resolve_entity_view_info",
            new=AsyncMock(return_value=_ENTITY_INFO),
        ),
        patch(
            "dataverse_mcp.tools.views._resolve_columns",
            new=AsyncMock(return_value={"name": "Name", "accountid": "Account ID"}),
        ),
        patch(
            "dataverse_mcp.tools.views.request_with_retry",
            new=AsyncMock(return_value=_post_response_with_view_id(_VIEW_ID)),
        ),
        patch(
            "dataverse_mcp.tools.views._publish_table",
            new=AsyncMock(side_effect=_http_status_error(500)),
        ) as mock_publish,
    ):
        result_str = await dataverse_create_view(params, ctx)

    result = json.loads(result_str)
    assert result.get("created") is True, "View must still be marked created"
    assert result.get("published") is False, "published must be False when publish raises"
    assert result.get("view_id") == _VIEW_ID, "view_id must be present even if publish failed"
    mock_publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# dataverse_set_app_sitemap — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_app_sitemap_published_true_on_success() -> None:
    """When publish succeeds, published must be True and updated must be True."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = SetAppSitemapInput(
        dataverse_url=_BASE_URL,
        app_id=_APP_ID,
        tables=["account", "contact"],
    )

    with (
        patch(
            "dataverse_mcp.tools.apps.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.apps._fetch_app_sitemap",
            new=AsyncMock(return_value=(None, None)),
        ),
        patch(
            "dataverse_mcp.tools.apps._upsert_sitemap",
            new=AsyncMock(return_value=_SITEMAP_ID),
        ),
        patch(
            "dataverse_mcp.tools.apps._call_add_app_components",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "dataverse_mcp.tools.apps._publish_app",
            new=AsyncMock(return_value=None),
        ) as mock_publish,
    ):
        result_str = await dataverse_set_app_sitemap(params, ctx)

    result = json.loads(result_str)
    assert result.get("updated") is True
    assert result.get("published") is True
    assert result.get("sitemap_id") == _SITEMAP_ID
    mock_publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# dataverse_set_app_sitemap — publish failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_app_sitemap_published_false_when_publish_raises() -> None:
    """When publish raises HTTPStatusError, published must be False but updated True."""
    app_ctx = _make_app_ctx()
    ctx = _make_ctx(app_ctx)

    params = SetAppSitemapInput(
        dataverse_url=_BASE_URL,
        app_id=_APP_ID,
        tables=["account", "contact"],
    )

    with (
        patch(
            "dataverse_mcp.tools.apps.build_headers",
            new=AsyncMock(return_value={"Authorization": "Bearer token"}),
        ),
        patch(
            "dataverse_mcp.tools.apps._fetch_app_sitemap",
            new=AsyncMock(return_value=(None, None)),
        ),
        patch(
            "dataverse_mcp.tools.apps._upsert_sitemap",
            new=AsyncMock(return_value=_SITEMAP_ID),
        ),
        patch(
            "dataverse_mcp.tools.apps._call_add_app_components",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "dataverse_mcp.tools.apps._publish_app",
            new=AsyncMock(side_effect=_http_status_error(500)),
        ) as mock_publish,
    ):
        result_str = await dataverse_set_app_sitemap(params, ctx)

    result = json.loads(result_str)
    assert result.get("updated") is True, "Sitemap must still be marked updated"
    assert result.get("published") is False, "published must be False when publish raises"
    assert result.get("sitemap_id") == _SITEMAP_ID, "sitemap_id must be present even if publish failed"
    mock_publish.assert_awaited_once()
