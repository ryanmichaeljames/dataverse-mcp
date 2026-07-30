"""Live integration coverage for dataverse_validate_fetchxml.

``ValidateFetchXmlExpression``'s return type has no documented inner property
names on Microsoft Learn, but the shape has now been recorded live against a v9.2
org and these tests assert against it:

```
ValidationResults: {Helplink: str, Messages: [
    {TypeCode: int, Severity: int, LocalizedMessageText: str,
     OptionalPropertyBag: {Count: int, Keys: [str], Values: [str]}}]}
```

There is no ``Result`` / ``IsValid`` boolean anywhere in the payload, and — the
trap this suite exists to pin down — a semantically invalid query (unknown table
or attribute) comes back as **HTTP 200** carrying a ``Severity: 3`` message, not
as an HTTP 400. Severity 1 = warning and 3 = error are observed, not documented.

Four tests:

* ``test_validate_fetchxml_function_returns_200_and_reports_the_recorded_shape``
  calls the unbound function directly with the exact URL the tool builds, proving
  the parameter-alias + single-quoted-literal encoding is accepted, and checks the
  top-level property names.
* ``test_deliberately_inefficient_query_is_analyzed`` drives the tool with a query
  built to provoke warnings: no ``<attribute>`` restrictions (all columns), a
  leading-wildcard ``like`` filter (cannot use an index), and an ``order`` on an
  unindexed column. Expect warnings, not errors.
* ``test_clean_query_reports_no_findings`` runs a tight, well-behaved query.
* ``test_invalid_table_returns_http_200_with_an_error_severity_message`` is the
  regression guard for the trap above.

Queries target ``systemuser``, which exists in every Dataverse org, and no test
asserts on record data — validation never executes the query, so nothing is read.

Nothing org-identifying is printed: Dataverse error text can embed system-table
metadata GUIDs, so every printed payload goes through ``_redact`` (org host and
GUIDs removed) and no assertion depends on that text.

Requires (else auto-skipped by tests/integration/conftest.py):
  DATAVERSE_INTEGRATION_URL   — base org URL
  DATAVERSE_INTEGRATION_TOKEN — bearer access token for that org

Read-only — no write/delete env flags needed.
"""

import json
import os
import re
import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    AppContext,
    encode_odata_literal,
    resolve_base_url,
)
from dataverse_mcp.models import ValidateFetchXmlInput
from dataverse_mcp.tools.tables import dataverse_validate_fetchxml

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Deliberately expensive: no <attribute> elements (so every column is returned), a
# leading-wildcard 'like' (no index can be used), and an order on a non-indexed
# column. This is the shape of query an LLM writes by accident.
_INEFFICIENT_FETCH = (
    '<fetch>'
    '<entity name="systemuser">'
    '<filter type="and">'
    '<condition attribute="fullname" operator="like" value="%a%" />'
    '</filter>'
    '<order attribute="fullname" />'
    '</entity>'
    '</fetch>'
)

# Tight and index-friendly: explicit columns, a bounded page, an equality filter on
# a column Dataverse indexes.
_CLEAN_FETCH = (
    '<fetch top="10">'
    '<entity name="systemuser">'
    '<attribute name="systemuserid" />'
    '<attribute name="fullname" />'
    '<filter type="and">'
    '<condition attribute="isdisabled" operator="eq" value="0" />'
    '</filter>'
    '</entity>'
    '</fetch>'
)

# No such table. Dataverse answers HTTP 200 with a Severity-3 message rather than
# an HTTP 400 — the whole reason has_errors exists.
_UNKNOWN_TABLE_FETCH = (
    '<fetch top="1">'
    '<entity name="mcp_no_such_table_zzz">'
    '<attribute name="mcp_no_such_column_zzz" />'
    '</entity>'
    '</fetch>'
)


def _url() -> str:
    return os.environ[_INTEGRATION_URL_VAR]


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ[_INTEGRATION_TOKEN_VAR]}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }


def _redact(text: str) -> str:
    """Strip the org host and any GUID before anything is printed.

    Dataverse validation errors have been observed embedding a system-table
    metadata GUID, and the transcript of an integration run should never carry
    org-identifying data.
    """
    return _GUID_RE.sub(
        "<guid>", text.replace(resolve_base_url(_url()), "https://<org>")
    )


def _dump(label: str, payload: Any) -> None:
    print(f"\n=== {label} ===")
    print(_redact(json.dumps(payload, indent=2, sort_keys=True)))


def _function_url(base_url: str, fetch_xml: str) -> str:
    """Return the exact URL the tool builds for *fetch_xml*."""
    return (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/ValidateFetchXmlExpression(FetchXml=@p1)?@p1='{encode_odata_literal(fetch_xml)}'"
    )


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the sandbox token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _validate(fetch_xml: str) -> dict:
    """Run the tool live and return the parsed result."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_validate_fetchxml(
                ValidateFetchXmlInput(dataverse_url=_url(), fetch_xml=fetch_xml), ctx
            )
        )


def _assert_recorded_shape(result: dict, fetch_xml: str) -> list[dict]:
    """Assert the tool contract and the recorded payload shape; return Messages."""
    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["fetch_xml_length"] == len(fetch_xml)
    assert result["normalized"] is True, (
        "the payload no longer matches the recorded ValidationResults.Messages "
        "shape, so no counts were derived — inspect raw_response"
    )
    # Every message is bucketed exactly once; nothing is silently dropped.
    assert result["count"] == result["error_count"] + result["warning_count"]
    assert result["has_errors"] is (result["error_count"] > 0)
    assert len(result["errors"]) == result["error_count"]

    validation_results = result["raw_response"]["ValidationResults"]
    assert isinstance(validation_results["Helplink"], str)
    messages = validation_results["Messages"]
    assert isinstance(messages, list)
    assert len(messages) == result["count"]
    for message in messages:
        assert isinstance(message["Severity"], int)
        assert isinstance(message["TypeCode"], int)
        assert isinstance(message["LocalizedMessageText"], str)
        bag = message["OptionalPropertyBag"]
        assert isinstance(bag["Keys"], list) and isinstance(bag["Values"], list)
    return messages


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_validate_fetchxml_function_returns_200_and_reports_the_recorded_shape() -> None:
    """The alias + single-quoted-literal URL is accepted and the shape holds."""
    base_url = resolve_base_url(_url())
    url = _function_url(base_url, _INEFFICIENT_FETCH)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=_headers())

    print("\n=== ValidateFetchXmlExpression raw HTTP call ===")
    print(f"url (org host redacted): {_redact(url)}")
    print(f"status: {response.status_code}")
    print(f"body: {_redact(response.text)}")

    assert response.status_code == 200, (
        "the parameter-aliased, single-quoted OData literal was rejected — "
        f"HTTP {response.status_code}: {_redact(response.text[:1000])}"
    )

    body = response.json()
    top_level = sorted(k for k in body if not k.startswith("@odata."))
    print(f"top_level_keys (envelope stripped): {top_level}")
    assert top_level == ["ValidationResults"], (
        "the recorded payload has exactly one top-level property; anything else "
        "means the response shape changed"
    )
    assert isinstance(body["ValidationResults"]["Messages"], list)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_deliberately_inefficient_query_is_analyzed() -> None:
    """An unrestricted, leading-wildcard query — the case warnings exist for."""
    result = await _validate(_INEFFICIENT_FETCH)
    _dump("inefficient query: full tool response", result)

    messages = _assert_recorded_shape(result, _INEFFICIENT_FETCH)

    assert result["has_errors"] is False, (
        "a syntactically and semantically valid query must not report errors"
    )
    assert result["warning_count"] >= 1, (
        "an all-columns, leading-wildcard, unindexed-order query was recorded as "
        "producing performance warnings"
    )
    # Severity 1 = warning is observed, not documented: confirm it still holds.
    assert all(m["Severity"] < 3 for m in messages)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_clean_query_reports_no_findings() -> None:
    """A tight query was recorded as returning an empty Messages list."""
    result = await _validate(_CLEAN_FETCH)
    _dump("clean query: full tool response", result)

    _assert_recorded_shape(result, _CLEAN_FETCH)

    assert result["has_errors"] is False
    assert result["count"] == 0, (
        "a narrow, top-limited, indexed query was recorded as producing no "
        "messages at all"
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get(_INTEGRATION_URL_VAR),
    reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
)
async def test_invalid_table_returns_http_200_with_an_error_severity_message() -> None:
    """The trap: an invalid query succeeds at the HTTP layer and fails in the payload."""
    base_url = resolve_base_url(_url())

    # First the raw call, to pin the status code itself rather than the tool's view.
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            _function_url(base_url, _UNKNOWN_TABLE_FETCH), headers=_headers()
        )
    print(f"\n=== unknown table: raw status {response.status_code} ===")
    assert response.status_code == 200, (
        "a nonexistent table was recorded as returning HTTP 200 with an "
        "error-severity message; a different status means the contract changed"
    )

    result = await _validate(_UNKNOWN_TABLE_FETCH)
    # Error text can embed a system-table metadata GUID — redacted before printing.
    _dump("unknown table: full tool response", result)

    messages = _assert_recorded_shape(result, _UNKNOWN_TABLE_FETCH)

    assert result["has_errors"] is True, (
        "HTTP 200 with an unknown table must still be reported as invalid"
    )
    assert result["error_count"] >= 1
    assert any(m["Severity"] >= 3 for m in messages)
    # Assert on shape, never on the message text: it is org-specific.
    assert all(isinstance(text, str) and text for text in result["errors"])
