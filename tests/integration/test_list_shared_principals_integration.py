"""Live integration coverage for dataverse_list_shared_principals.

The tool merges two unbound functions — ``RetrieveSharedPrincipalsAndAccess`` and
``RetrieveSharedLinks`` — that both take a ``Target`` typed ``crmbaseentity``.
Two things this suite was written to settle are now settled (live, 2026-07-30):

1. **The ``@odata.id`` parameter-alias encoding works.** ``Target`` is an
   EntityReference, so the alias value is a JSON OBJECT
   (``?@t={"@odata.id":"accounts(<guid>)"}``) that is json-encoded and then
   percent-encoded — a FOURTH escaping regime, distinct from the bare
   ``Edm.Guid``, the single-quoted ``Edm.String`` and the JSON-array
   ``Collection(Edm.String)`` this codebase already proves live. Both functions
   answered HTTP 200. The tool uses the RELATIVE form (``accounts(<guid>)``) while
   ``dataverse_retrieve_principal_access`` builds the same reference with an
   ABSOLUTE URL: **both are accepted and returned byte-identical bodies**, so the
   relative form is a free choice that keeps the org host out of a query string.
2. **Both response shapes are confirmed.** Microsoft Learn documents
   ``RetrieveSharedPrincipalsAndAccessResponse`` but not its inner properties;
   ``PrincipalAccesses`` — a hint taken from the organization-service response
   class — is what really arrives, and ``RetrieveSharedLinks`` returns
   ``Collection(team)`` under the ordinary OData ``value``. Those two names are now
   ASSERTED via ``source`` rather than merely recorded. Every test still prints the
   raw payload, its size and its top-level keys, so a platform change is diagnosed
   from the output rather than guessed at.

Also under test: the independent fault tolerance. ``RetrieveSharedLinks`` is in
principle the likelier of the two to be unavailable or privilege-gated (it WAS
available on the org tested), and its failure must land in ``partial_errors``
while the other's data still returns.

One live finding worth keeping in view while reading failures here: **a
nonexistent record id and a valid id paired with the WRONG entity set are
indistinguishable** — both give HTTP 404 ``[0x80040217] Entity '<Type>' With Id =
<guid> Does Not Exist`` from both functions.

**A clean org may have no shared records at all.** That is expected, not a
failure: the share-specific test SKIPS with an explicit reason rather than
failing, and the shape/contract tests work off any record because an empty share
list is still a valid payload to record.

No org URL and no record id is hardcoded: the target table is discovered from the
service document and the record ids are queried live. Everything printed goes
through ``_redact``.

Read-only throughout: nothing is created, updated, shared or published.

Requires (else auto-skipped by tests/integration/conftest.py):
  DATAVERSE_INTEGRATION_URL   — base org URL
  DATAVERSE_INTEGRATION_TOKEN — bearer access token for that org
"""

import json
import logging
import os
import re
import time
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlencode

import httpx
import pytest

from dataverse_mcp.client import _DATAVERSE_API_VERSION, AppContext, resolve_base_url
from dataverse_mcp.models import ListSharedPrincipalsInput
from dataverse_mcp.tools.security import dataverse_list_shared_principals

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Candidate target tables, most-likely-to-be-shared first. Each is (entity set,
# primary key attribute). Shares are a user-owned-table concept, so the ordinary
# business tables come before the guaranteed-to-exist platform ones. The first
# with at least one row wins; nothing is hardcoded beyond these standard names.
_CANDIDATE_TABLES = [
    ("accounts", "accountid"),
    ("contacts", "contactid"),
    ("incidents", "incidentid"),
    ("opportunities", "opportunityid"),
    ("teams", "teamid"),
    ("systemusers", "systemuserid"),
]

# How many records to scan when hunting for one that is actually shared. Each
# costs two round trips, so keep it modest.
_SHARE_SCAN_LIMIT = 25

# The property each block's collection is LIVE-CONFIRMED to arrive under, keyed by
# the block name the tool returns. Asserted, not merely printed: these are the
# names the tool's _SHARED_PRINCIPALS_KEYS / _SHARED_LINKS_KEYS try first, so a
# by-shape fallback firing here is a platform change worth failing on.
_EXPECTED_SOURCES = {
    "shared_principals": "PrincipalAccesses",
    "shared_links": "value",
}

# The same two names as they appear at the top level of each function's RAW body.
_EXPECTED_RAW_KEYS = {
    "RetrieveSharedPrincipalsAndAccess": "PrincipalAccesses",
    "RetrieveSharedLinks": "value",
}

_RAW_BODY_PREVIEW = 4000

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get(_INTEGRATION_URL_VAR),
        reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
    ),
]


@pytest.fixture(autouse=True)
def _quiet_httpx_request_logging():
    """Stop httpx's INFO logger printing the org host and record GUID unredacted.

    ``httpx._client`` logs every request as ``HTTP Request: GET <full url> ...``
    at INFO. Under pytest's log capture that lands in the console BELOW this
    module's ``_redact`` helper, leaking exactly what ``_redact`` exists to hide.
    Raising the logger's own level drops the records at source, so no handler or
    capture setting can resurrect them.
    """
    logger = logging.getLogger("httpx")
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(previous)


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
    """Strip the org host and every GUID before anything is printed."""
    return _GUID_RE.sub(
        "<guid>", text.replace(resolve_base_url(_url()), "https://<org>")
    )


def _dump(label: str, payload: Any) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(f"\n=== {label} ({len(text)} bytes as printed) ===")
    print(_redact(text))


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _get(path: str) -> httpx.Response:
    base_url = resolve_base_url(_url())
    async with httpx.AsyncClient(timeout=60.0) as client:
        return await client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}{path}", headers=_headers()
        )


async def _target_records() -> tuple[str, list[str]]:
    """Find a populated table and return ``(entity_set_name, [record ids])``.

    Data-plane ``$select``/``$top`` are used, which this org handles normally —
    unlike ``$filter`` on ``EntityDefinitions`` (schema plane), which it rejects
    with HTTP 400 ``[0x80060888]``. A table that is absent from the org answers
    404 and is simply skipped.
    """
    for entity_set, key in _CANDIDATE_TABLES:
        response = await _get(f"/{entity_set}?$select={key}&$top={_SHARE_SCAN_LIMIT}")
        if response.status_code != 200:
            print(
                f"=== {entity_set}: HTTP {response.status_code}, skipping as a "
                "target table ==="
            )
            continue
        ids = [
            row[key]
            for row in response.json().get("value", [])
            if isinstance(row.get(key), str)
        ]
        if ids:
            print(f"\n=== target table {entity_set!r}: {len(ids)} record(s) found ===")
            return entity_set, ids
        print(f"=== {entity_set}: 0 rows, trying the next candidate ===")
    return "", []


async def _call(entity_set: str, record_id: str, **kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_list_shared_principals(
                ListSharedPrincipalsInput(
                    dataverse_url=_url(),
                    entity_set_name=entity_set,
                    record_id=record_id,
                    **kwargs,
                ),
                ctx,
            )
        )


def _alias_query(entity_set: str, record_id: str) -> str:
    """Rebuild the exact query string tools/security.py builds for Target."""
    alias_value = json.dumps(
        {"@odata.id": f"{entity_set}({record_id})"}, separators=(",", ":")
    )
    return urlencode({"@t": alias_value}, safe="@")


async def test_raw_calls_record_the_target_encoding_and_both_response_shapes() -> None:
    """Prove the ``@odata.id`` alias round-trips and check what comes back.

    HTTP 200 here is the whole point: it is the live evidence that a JSON
    EntityReference alias, percent-encoded and NOT run through
    ``encode_odata_literal``, is what Dataverse accepts for a ``crmbaseentity``
    parameter — and that the relative ``accounts(<guid>)`` form works as well as
    the absolute URL ``dataverse_retrieve_principal_access`` uses (both forms were
    tried live and returned byte-identical bodies).
    """
    entity_set, ids = await _target_records()
    if not ids:
        pytest.skip("no candidate table in this org holds a single record")

    base_url = resolve_base_url(_url())
    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
    query = _alias_query(entity_set, ids[0])
    statuses: dict[str, int] = {}

    for function_name in ("RetrieveSharedPrincipalsAndAccess", "RetrieveSharedLinks"):
        url = f"{api_root}/{function_name}(Target=@t)?{query}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=_headers())

        print(f"\n=== {function_name} raw HTTP call ===")
        print(f"url (org host + guid redacted): {_redact(url)}")
        print(f"status: {response.status_code}")
        print(f"RESPONSE SIZE: {len(response.content)} bytes")
        print(f"RAW BODY (first {_RAW_BODY_PREVIEW} chars):")
        print(_redact(response.text[:_RAW_BODY_PREVIEW]))
        statuses[function_name] = response.status_code

        if response.status_code != 200:
            print(
                f"RECORDED: {function_name} is NOT available to this caller on this "
                "org — the tool must report it in partial_errors, not fail"
            )
            continue

        body = response.json()
        top_level = sorted(k for k in body if not k.startswith("@odata."))
        print(f"RECORDED top_level_keys (envelope stripped): {top_level}")
        assert _EXPECTED_RAW_KEYS[function_name] in top_level, (
            f"{function_name} no longer carries its collection under "
            f"{_EXPECTED_RAW_KEYS[function_name]!r} — top-level keys were "
            f"{top_level}. That is a platform change: update the tool's key "
            "constants and its docstring."
        )
        print(
            "RECORDED value_types: "
            + json.dumps({k: type(body[k]).__name__ for k in top_level}, sort_keys=True)
        )
        entries = next((v for v in body.values() if isinstance(v, list)), None)
        if entries is None:
            print("RECORDED: no list property in the payload at all")
        else:
            print(f"RECORDED entry count: {len(entries)}")
            if entries and isinstance(entries[0], dict):
                print(f"RECORDED entry_keys: {sorted(entries[0])}")
                print(
                    "RECORDED first entry: "
                    + _redact(json.dumps(entries[0], default=str))
                )

    assert 200 in statuses.values(), (
        "NEITHER function accepted the JSON @odata.id parameter alias — statuses "
        f"{statuses}. Read the bodies printed above: either the encoding is wrong "
        "(check that the alias value is percent-encoded JSON and that "
        "encode_odata_literal was NOT applied), or both functions are gated for "
        "this caller."
    )


async def test_tool_merges_both_functions_and_tolerates_one_failing() -> None:
    """The tool contract on a live record, whichever functions are available."""
    entity_set, ids = await _target_records()
    if not ids:
        pytest.skip("no candidate table in this org holds a single record")

    result = await _call(entity_set, ids[0])
    _dump("shared principals: full tool response", result)

    assert not result.get("error"), (
        "BOTH functions failed on this org, so the tool correctly returned the "
        f"error envelope: {_redact(str(result.get('message')))}"
    )
    assert result["entity_set_name"] == entity_set
    assert result["record_id"] == ids[0]
    assert result["target"] == f"{entity_set}({ids[0]})"
    assert isinstance(result["partial_errors"], list)

    present = [k for k in ("shared_principals", "shared_links") if k in result]
    assert present, "neither block was returned yet no error was raised"
    print(
        f"RECORDED: blocks returned {present}, partial_errors="
        f"{_redact(json.dumps(result['partial_errors']))}"
    )

    for key, entries_key in (
        ("shared_principals", "principals"),
        ("shared_links", "links"),
    ):
        block = result.get(key)
        if block is None:
            print(f"RECORDED: {key} absent — its function is in partial_errors")
            continue
        if not block["normalized"]:
            print(
                f"RECORDED: {key} could NOT be normalized — the collection sits "
                "somewhere the by-name and by-shape tiers do not recognize. Read "
                "raw_response above and correct the tool's key constants."
            )
            assert "raw_response" in block
            continue
        assert block["count"] == len(block[entries_key])
        assert block["total_count"] >= block["count"]
        assert block["has_more"] is (block["total_count"] > block["count"])
        assert block["source"] == _EXPECTED_SOURCES[key], (
            f"{key} normalized from {block['source']!r}, not the live-confirmed "
            f"{_EXPECTED_SOURCES[key]!r} — the by-shape fallback fired, which means "
            "the platform moved the collection. Read the raw bodies printed by the "
            "raw-call test and update the tool's key constants."
        )
        print(
            f"RECORDED: {key} source={block['source']!r} "
            f"total_count={block['total_count']}"
        )

    # A failing function must degrade, never fail the tool.
    for entry in result["partial_errors"]:
        assert set(entry) == {"function", "message"}
        print(
            f"RECORDED partial error from {entry['function']}: "
            f"{_redact(entry['message'])}"
        )


async def test_a_shared_record_lists_the_principals_it_was_shared_with() -> None:
    """Find a record that really is shared — or SKIP saying so.

    A clean org routinely has no shares at all. That is a legitimate state, not a
    tool defect, so this test scans a bounded number of records and skips with an
    explicit reason when none of them is shared.
    """
    entity_set, ids = await _target_records()
    if not ids:
        pytest.skip("no candidate table in this org holds a single record")

    scanned = 0
    for record_id in ids[:_SHARE_SCAN_LIMIT]:
        result = await _call(entity_set, record_id)
        scanned += 1
        block = result.get("shared_principals")
        if not isinstance(block, dict) or not block.get("normalized"):
            continue
        if block["total_count"] == 0:
            continue

        _dump("SHARED record: full tool response", result)
        assert block["count"] >= 1
        assert block["principals"], "total_count > 0 but no entries were returned"
        print(
            f"RECORDED: a shared {entity_set} record has "
            f"{block['total_count']} principal(s); "
            f"entry_keys={sorted(block['principals'][0])}"
        )
        print(
            "RECORDED first principal entry: "
            + _redact(json.dumps(block["principals"][0], default=str))
        )
        return

    pytest.skip(
        f"no shared record found: {scanned} {entity_set} record(s) were checked and "
        "RetrieveSharedPrincipalsAndAccess reported zero principals for every one. "
        "A clean org has no explicit shares, which is a valid state — share a "
        "record manually and re-run to exercise this path."
    )


async def test_a_nonexistent_record_id_is_recorded_not_assumed() -> None:
    """Does a well-formed but unknown record id 404, or answer empty?

    Both are plausible and they mean very different things, so this RECORDS the
    outcome rather than asserting one. What it does assert is that the tool never
    raises and never fabricates counts.

    It also records the caller trap the docstring warns about: a REAL record id
    paired with the WRONG entity set produces the same HTTP 404 ``[0x80040217]
    Does Not Exist`` as a record that is simply absent, so the two are
    indistinguishable from the error message alone.
    """
    entity_set, ids = await _target_records()
    if not ids:
        pytest.skip("no candidate table in this org holds a single record")

    result = await _call(entity_set, "00000000-0000-0000-0000-000000000001")
    _dump("nonexistent record: full tool response", result)

    if result.get("error"):
        print(
            "RECORDED: a nonexistent record id returns the ERROR envelope — "
            f"{_redact(str(result['message']))}"
        )
        assert isinstance(result["message"], str) and result["message"]
        assert "shared_principals" not in result
    else:
        block = result.get("shared_principals")
        print(
            "RECORDED: a nonexistent record id returns a SUCCESSFUL response, "
            f"shared_principals={_redact(json.dumps(block))}. An empty share list "
            "is therefore indistinguishable from a record that does not exist — "
            "say so in the tool docstring."
        )

    # Same id, wrong table. Any candidate other than the one the id came from will
    # do; every entity set here is a standard name, so nothing org-specific is
    # hardcoded.
    wrong_set = next(
        (name for name, _ in _CANDIDATE_TABLES if name != entity_set), None
    )
    if wrong_set is None:
        return
    mismatched = await _call(wrong_set, ids[0])
    _dump("real record id + WRONG entity set: full tool response", mismatched)
    print(
        f"RECORDED: a real {entity_set} id queried as {wrong_set!r} returned "
        f"error={bool(mismatched.get('error'))} — "
        f"{_redact(str(mismatched.get('message')))}. Compare it with the "
        "nonexistent-record message above: if the two are alike, a caller cannot "
        "tell 'no such record' from 'right id, wrong table', which is why the "
        "docstring tells them to check the plural entity set name first."
    )
