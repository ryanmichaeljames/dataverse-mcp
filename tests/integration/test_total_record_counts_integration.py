"""Live integration coverage for dataverse_get_total_record_counts.

Three tests:

* ``test_retrieve_total_record_count_returns_200_and_reports_shape`` calls the
  unbound ``RetrieveTotalRecordCount`` function directly and *records the observed
  property names* of the response. The Microsoft Learn reference names only the
  response type (``RetrieveTotalRecordCountResponse``) and its collection type
  (``EntityRecordCountCollection``), never the inner property names — the
  normalization in the tool assumes Dataverse serializes the .NET dictionary as
  parallel ``Keys`` / ``Values`` arrays. A previous live run confirmed exactly
  that shape; this test keeps it honest. Property names and value *types* are
  printed; row counts are not, so no org data leaks into the transcript.
  Run with ``-s`` (or ``--capture=no``) to see the report on a passing run.
* ``test_get_total_record_counts_tool_returns_counts`` drives the real tool end to
  end against tables present in every org and asserts it normalized the payload.
  It deliberately does *not* assert the counts are non-zero: the counts come from
  a snapshot refreshed at most every 24 hours, and a live run against an org whose
  real counts were in the hundreds returned 0 for every table because the snapshot
  job had not run. The tool flags that with ``all_counts_zero``.
* ``test_unknown_entity_name_fails_the_whole_batch`` pins the all-or-nothing
  behaviour: mixing one bogus name into a batch of valid ones 400s the entire call
  ([0x80040203]), and the tool must turn that into the standard error envelope with
  an actionable message rather than raising or returning partial results.

Requires (else auto-skipped by tests/integration/conftest.py):
  DATAVERSE_INTEGRATION_URL   — base org URL
  DATAVERSE_INTEGRATION_TOKEN — bearer access token for that org

Read-only — no write/delete env flags needed.
"""

import json
import os
import time
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlencode

import httpx
import pytest

from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    AppContext,
    resolve_base_url,
)
from dataverse_mcp.models import GetTotalRecordCountsInput
from dataverse_mcp.tools.tables import dataverse_get_total_record_counts

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

# Present in every Dataverse org, so the call is org-independent.
_ENTITY_NAMES = ["systemuser", "solution"]

# Cannot exist: the publisher prefix grammar is satisfied but no such table ships
# and none can be created by this test.
_UNKNOWN_ENTITY_NAME = "zzz_not_a_real_table"


def _url() -> str:
    return os.environ[_INTEGRATION_URL_VAR]


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ[_INTEGRATION_TOKEN_VAR]}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }


def _function_url(base_url: str, entity_names: list[str]) -> str:
    """Return the exact URL the tool builds for *entity_names*."""
    alias_value = json.dumps(entity_names, separators=(",", ":"))
    return (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveTotalRecordCount(EntityNames=@p1)?"
        f"{urlencode({'@p1': alias_value}, safe='@')}"
    )


def _describe(value: Any, depth: int = 2) -> Any:
    """Describe a payload's shape — property names and value types, never values."""
    if isinstance(value, dict):
        if depth <= 0:
            return f"dict({len(value)} keys)"
        return {k: _describe(v, depth - 1) for k, v in value.items()}
    if isinstance(value, list):
        if depth <= 0 or not value:
            return f"list[{len(value)}]"
        return [f"list[{len(value)}] of", _describe(value[0], depth - 1)]
    return type(value).__name__


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the sandbox token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_retrieve_total_record_count_returns_200_and_reports_shape() -> None:
    """Call the unbound function directly and record its real response shape."""
    base_url = resolve_base_url(_url())
    url = _function_url(base_url, _ENTITY_NAMES)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=_headers())

    assert response.status_code == 200, (
        "the parameter-aliased collection URL was rejected — "
        f"HTTP {response.status_code}: {response.text[:500]}"
    )

    body = response.json()
    print("\n=== RetrieveTotalRecordCount observed response shape ===")
    print(f"url (org host redacted): {url.replace(base_url, 'https://<org>')}")
    print(f"top_level_keys: {sorted(body.keys())}")
    print(json.dumps(_describe(body, depth=3), indent=2, sort_keys=True))
    for key, value in body.items():
        if isinstance(value, dict):
            print(f"inner keys of {key!r}: {sorted(value.keys())}")

    assert sorted(body.keys()), "RetrieveTotalRecordCount returned an empty payload"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_get_total_record_counts_tool_returns_counts() -> None:
    """The tool returns a normalized count map against a live org."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = _make_live_ctx(client)
        result = json.loads(
            await dataverse_get_total_record_counts(
                GetTotalRecordCountsInput(
                    dataverse_url=_url(), entity_names=_ENTITY_NAMES
                ),
                ctx,
            )
        )

    print("\n=== dataverse_get_total_record_counts result shape ===")
    print(json.dumps(_describe(result, depth=3), indent=2, sort_keys=True))
    print(f"normalized: {result.get('normalized')}")
    print(f"all_counts_zero: {result.get('all_counts_zero')}")

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["approximate"] is True
    assert result["normalized"] is True, (
        "the Keys/Values normalization assumption did not hold; raw payload shape: "
        f"{_describe(result.get('raw_response'), depth=3)}"
    )
    assert sorted(result["counts"]) == sorted(_ENTITY_NAMES), (
        f"unexpected keys in the count map: {sorted(result['counts'])}"
    )
    assert "not_returned" not in result, (
        "not_returned was removed: an unrecognized name 400s the whole batch, so it "
        "could never be populated"
    )
    assert result["count"] == len(_ENTITY_NAMES)
    # Counts are snapshot-based and may legitimately all be 0 on an org where the
    # ≤24h snapshot job has not run, so only the type/sign is asserted here.
    assert all(isinstance(v, int) and v >= 0 for v in result["counts"].values())
    assert result["all_counts_zero"] is (not any(result["counts"].values()))
    if result["all_counts_zero"]:
        assert "snapshot" in result["message"], (
            "an all-zero snapshot must be explained, not returned as bare zeros"
        )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_unknown_entity_name_fails_the_whole_batch() -> None:
    """One unrecognized name 400s the entire call — no partial results, no raise."""
    entity_names = [*_ENTITY_NAMES, _UNKNOWN_ENTITY_NAME]

    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = _make_live_ctx(client)
        result = json.loads(
            await dataverse_get_total_record_counts(
                GetTotalRecordCountsInput(
                    dataverse_url=_url(), entity_names=entity_names
                ),
                ctx,
            )
        )

    print("\n=== unknown-name batch result ===")
    print(json.dumps(result, indent=2, sort_keys=True))

    assert result.get("error") is True, (
        "Dataverse rejects the whole batch on an unknown name; the tool must not "
        f"report success. Got: {json.dumps(_describe(result, depth=2))}"
    )
    assert "counts" not in result, "no partial results are returned on a 400"
    message = result["message"]
    # The message has to be actionable: which name, and that nothing came back.
    assert _UNKNOWN_ENTITY_NAME in message
    assert "all-or-nothing" in message
    assert "dataverse_list_tables" in message
