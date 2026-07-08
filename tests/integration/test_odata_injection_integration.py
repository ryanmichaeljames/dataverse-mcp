"""Live integration coverage for the OData literal-injection fix.

Proves, against a real Dataverse org, that a caller-supplied value can no longer
break out of an OData string-literal key predicate such as
``EntityDefinitions(LogicalName='...')`` and navigate to a foreign resource.

Two independent guards are exercised:

* ``test_encode_odata_literal_blocks_live_breakout`` verifies the *output
  encoding* against real server URL-decoding behaviour. It reproduces the
  vulnerability with the old naive percent-encoding (which the live server
  decodes and executes as a navigation) and then shows ``encode_odata_literal``
  neutralises the same payload — a before/after on the actual server, so the
  test would fail if the server semantics or the encoder ever regressed.
* ``test_get_column_rejects_injection_and_serves_legit`` verifies the *input
  validation* boundary and no-regression on genuine names by driving the real
  ``dataverse_get_column`` tool.

Requires (else auto-skipped by tests/integration/conftest.py):
  DATAVERSE_INTEGRATION_URL   — base org URL
  DATAVERSE_INTEGRATION_TOKEN — bearer access token for that org

Read-only — no write/delete env flags needed.
"""

import json
import os
import time
from unittest.mock import MagicMock
from urllib.parse import quote

import httpx
import pytest

from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    AppContext,
    encode_odata_literal,
    resolve_base_url,
)
from dataverse_mcp.models import GetColumnInput
from dataverse_mcp.tools.metadata import dataverse_get_column

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

# Payload that escaped EntityDefinitions(LogicalName='...') and navigated to the
# 'createdon' attribute metadata (HTTP 200) before the fix.
_BREAKOUT = "account')/Attributes(LogicalName='createdon"


def _url() -> str:
    return os.environ[_INTEGRATION_URL_VAR]


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """FastMCP-style ctx backed by an AppContext pre-seeded with the sandbox token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _get_entitydef(client: httpx.AsyncClient, base_url: str, encoded: str) -> httpx.Response:
    """GET EntityDefinitions(LogicalName='<encoded>') exactly as the tool builds it."""
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/"
        f"EntityDefinitions(LogicalName='{encoded}')?$select=LogicalName"
    )
    return await client.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_encode_odata_literal_blocks_live_breakout() -> None:
    """Old encoding navigates to a foreign resource; the fix confines it — live proof."""
    base_url = resolve_base_url(_url())
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Sanity: a legit name resolves normally.
        legit = await _get_entitydef(client, base_url, encode_odata_literal("account"))
        assert legit.status_code == 200, f"baseline account lookup failed: {legit.text}"

        # Negative control — the OLD naive encoding reproduces the breakout: the
        # server percent-decodes %27 to a bare quote, terminates the literal, and
        # executes the injected /Attributes navigation.
        vulnerable = await _get_entitydef(client, base_url, quote(_BREAKOUT, safe=""))
        assert vulnerable.status_code == 200, (
            "expected the naive-encoding breakout to still navigate on this server; "
            f"got HTTP {vulnerable.status_code}: {vulnerable.text[:300]}"
        )
        assert "/Attributes" in vulnerable.json().get("@odata.context", ""), (
            "negative control did not navigate to Attributes — test is no longer "
            f"meaningful: {vulnerable.text[:300]}"
        )

        # The fix: the same payload, encoded via encode_odata_literal, stays inside
        # the literal and does NOT reach a foreign resource.
        blocked = await _get_entitydef(client, base_url, encode_odata_literal(_BREAKOUT))
        assert blocked.status_code != 200, (
            f"breakout was NOT blocked — HTTP {blocked.status_code}: {blocked.text[:300]}"
        )
        assert "/Attributes" not in blocked.text, (
            f"encoded payload still reached Attributes navigation: {blocked.text[:300]}"
        )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_get_column_rejects_injection_and_serves_legit() -> None:
    """dataverse_get_column rejects the injection payload and serves genuine names."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = _make_live_ctx(client)

        # Input-validation boundary: the payload never builds a request.
        with pytest.raises(Exception):
            GetColumnInput(
                dataverse_url=_url(),
                table_logical_name=_BREAKOUT,
                column_logical_name="createdon",
            )

        # No regression: a real column resolves through the actual tool.
        got = json.loads(
            await dataverse_get_column(
                GetColumnInput(
                    dataverse_url=_url(),
                    table_logical_name="account",
                    column_logical_name="createdon",
                ),
                ctx,
            )
        )
        assert not got.get("error"), f"get_column failed for a legit name: {got}"
