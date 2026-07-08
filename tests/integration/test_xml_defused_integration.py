"""Live integration coverage for the defusedxml migration (issue #119).

Drives the actual tool code paths that were switched from stdlib
``xml.etree.ElementTree.fromstring`` to ``defusedxml.ElementTree.fromstring``
against real Dataverse view/form XML, proving the hardened parser still reads
genuine server content without regression.

Requires (else auto-skipped by tests/integration/conftest.py):
  DATAVERSE_INTEGRATION_URL   — base org URL
  DATAVERSE_INTEGRATION_TOKEN — bearer access token for that org

All tests here are read-only — no write/delete env flags needed.
"""

import json
import os
import time
from unittest.mock import MagicMock

import httpx
import pytest

from dataverse_mcp.client import AppContext, resolve_base_url
from dataverse_mcp.models import (
    GetFormInput,
    GetViewInput,
    ListFormsInput,
    ListViewsInput,
    ValidateViewInput,
)
from dataverse_mcp.tools.forms import dataverse_get_form, dataverse_list_forms
from dataverse_mcp.tools.views import (
    dataverse_get_view,
    dataverse_list_views,
    dataverse_validate_view,
)

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """Build a FastMCP-style ctx backed by an AppContext seeded with the sandbox token.

    Pre-seeding ``_token_cache`` for the resolved scope means ``build_headers``
    returns the supplied bearer token without exercising any credential flow.
    """
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    # Far-future expiry so the cache entry is treated as fresh.
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _url() -> str:
    return os.environ[_INTEGRATION_URL_VAR]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_get_and_validate_view_parse_live_xml() -> None:
    """get_view + validate_view parse real FetchXml/LayoutXml via defusedxml without error."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = _make_live_ctx(client)

        listed = json.loads(
            await dataverse_list_views(
                ListViewsInput(dataverse_url=_url(), table_logical_name="account", top=10),
                ctx,
            )
        )
        assert not listed.get("error"), f"list_views failed: {listed}"
        assert listed["count"] > 0, "expected at least one account view in the sandbox"
        view_id = listed["views"][0]["view_id"]

        got = json.loads(
            await dataverse_get_view(GetViewInput(dataverse_url=_url(), view_id=view_id), ctx)
        )
        assert not got.get("error"), f"get_view failed (defused parse regression?): {got}"
        # Structured output comes straight out of the defusedxml parse path.
        assert "fetch" in got and "layout" in got, f"expected parsed fetch/layout keys, got: {got}"
        assert got["fetch"]["columns"], "expected defusedxml to extract FetchXml columns"

        validated = json.loads(
            await dataverse_validate_view(
                ValidateViewInput(dataverse_url=_url(), view_id=view_id), ctx
            )
        )
        assert not validated.get("error"), f"validate_view failed: {validated}"
        assert "valid" in validated, f"expected 'valid' key, got: {validated}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_get_form_parses_live_formxml() -> None:
    """get_form parses real FormXml via defusedxml without error."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = _make_live_ctx(client)

        listed = json.loads(
            await dataverse_list_forms(
                ListFormsInput(dataverse_url=_url(), table_logical_name="account", top=10),
                ctx,
            )
        )
        assert not listed.get("error"), f"list_forms failed: {listed}"
        assert listed["count"] > 0, "expected at least one account form in the sandbox"
        form_id = listed["forms"][0]["form_id"]

        got = json.loads(
            await dataverse_get_form(GetFormInput(dataverse_url=_url(), form_id=form_id), ctx)
        )
        assert not got.get("error"), f"get_form failed (defused parse regression?): {got}"
        assert "layout" in got, f"expected parsed 'layout' key, got: {got}"
