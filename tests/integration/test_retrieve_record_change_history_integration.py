"""Live integration coverage for dataverse_retrieve_record_change_history.

``RetrieveRecordChangeHistory`` is an unbound GET taking one ``crmbaseentity``
parameter as a ``Target`` alias holding an ``@odata.id``. Its ``$metadata`` return type
is ``RetrieveAttributeChangeHistoryResponse`` — the SAME type its column-scoped sibling
returns — so the payload nests TWO levels (``AuditDetailCollection`` -> ``AuditDetails``)
and every shape trap proven there applies here too.

What this suite establishes live, each point being a defect the tool used to have:

* THE CONFIGURATION ROWS: org-level audit-CONFIGURATION rows (no ``@odata.type``,
  ``AuditRecord`` and nothing else, and an all-zero ``AuditRecord._objectid_value``) MAY
  accompany a response, and counting them reports changes that did not happen. They are
  NOT unconditional: they arrive when an audit-configuration change falls inside the
  TARGET RECORD'S own history window, so their presence and count vary by target and
  ``audit_configuration_events_count: 0`` is a normal answer (live: a record created
  after the last audit-config change got 0; two older records got 4 each). They must be
  partitioned out by SHAPE — their position in the list is not a contract — and
  surfaced, not dropped;
* THE SUBTYPE MIX: a RECORD-scoped call is not restricted to ``AttributeAuditDetail`` —
  ``ShareAuditDetail`` is live-confirmed here. Whatever ``@odata.type`` values this org
  actually returns are printed and counted in the tool's own ``detail_types``, and an
  unrecognized one must be kept as a result;
* ``TotalRecordCount`` arrives as ``-1`` ("not counted"), which must be suppressed rather
  than handed to the caller as a number that reads like a count;
* ``top`` trims client-side because ``PagingInfo`` is not sent, so ``has_more`` has to
  report the trim and not merely the server's ``MoreRecords``;
* THE FAILURE MODES ARE NOT ONE MODE, and a 404 is read by its ERROR CODE, not assumed:
  a valid id with the WRONG (singular) entity set — or a table not provisioned on the
  org — is an HTTP 404 ``[0x80060888]`` naming the bad segment, while on MOST tables a
  NONEXISTENT record id is a plain HTTP 200 with zero genuine changes, because Dataverse
  does not check the target exists there (nor does it error when auditing is off). That
  is not universal: a bogus GUID swept across 15 entity sets on one org returned 200 for
  12 of them (``accounts`` included, which is what this suite exercises) but 404
  ``[0x80048d02]`` for ``audits``, where the row's absence really is what the 404 means.
  The ``audits`` case is NOT probed here — it depends on which tables an org has and how
  they behave, and pinning the suite to that would make it org-specific.

SECURITY: audit rows carry REAL BUSINESS VALUES in ``OldValue`` / ``NewValue``. Nothing
in this file prints an audit value. Entries are reported by ``@odata.type``, key NAMES,
value TYPES and lengths only; the org host and every GUID are redacted on every line that
is printed at all.

Nothing is hardcoded: the table and record are discovered live from the ``audits`` table,
and the suite skips rather than fails on an org where auditing is off or nothing has been
audited.

Read-only throughout: nothing is created, updated or published.

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

import httpx
import pytest

from dataverse_mcp.client import _DATAVERSE_API_VERSION, AppContext, resolve_base_url
from dataverse_mcp.models import RetrieveRecordChangeHistoryInput
from dataverse_mcp.tools.security import dataverse_retrieve_record_change_history

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

_AUDIT_PAGE_SIZE = 50

# A table every org has, used for the negative probes where no audit data is needed.
_FALLBACK_TABLE = "account"
_FALLBACK_ENTITY_SET = "accounts"

_ABSENT_RECORD_ID = "00000000-0000-0000-0000-0000000000ff"

# Every audit-CONFIGURATION row carries this as AuditRecord._objectid_value: the row is
# about auditing itself, so it points at no record. Live: 11 rows, zero divergence.
_ALL_ZERO_GUID = "00000000-0000-0000-0000-000000000000"

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

    ``httpx._client`` logs every request as ``HTTP Request: GET <full url> ...`` at
    INFO. Under pytest's log capture that lands in the console BELOW this module's
    ``_redact`` helper, leaking exactly what ``_redact`` exists to hide. Raising the
    logger's own level drops the records at source, so no handler or capture setting
    can resurrect them.
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


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the sandbox token."""
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


async def _call(**kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_retrieve_record_change_history(
                RetrieveRecordChangeHistoryInput(dataverse_url=_url(), **kwargs), ctx
            )
        )


# ---------------------------------------------------------------------------
# Redacting describers — NEVER print an audit value
# ---------------------------------------------------------------------------


def _describe_values(payload: Any) -> Any:
    """Reduce an OldValue/NewValue entity dict to column NAMES and value TYPES.

    The column logical names are schema, not business data, so they are safe to print;
    the values are real customer data and are replaced by their type and length. This is
    the only function that ever looks at OldValue/NewValue.
    """
    if not isinstance(payload, dict):
        return type(payload).__name__
    return {
        key: f"{type(value).__name__}[{len(value)}]"
        if isinstance(value, (str, list, dict))
        else type(value).__name__
        for key, value in sorted(payload.items())
        if not key.startswith("@odata.")
    }


def _describe_detail(entry: Any) -> dict:
    """Describe ONE AuditDetail by type and shape — never by value."""
    if not isinstance(entry, dict):
        return {"non_object_entry": type(entry).__name__}
    described: dict[str, Any] = {
        "@odata.type": entry.get("@odata.type", "<absent>"),
        "keys": sorted(k for k in entry if not k.startswith("@odata.")),
    }
    audit_record = entry.get("AuditRecord")
    if isinstance(audit_record, dict):
        described["AuditRecord_keys"] = sorted(
            k for k in audit_record if not k.startswith("@odata.")
        )
        # An operation code is metadata, not business data.
        described["AuditRecord_operation"] = audit_record.get("operation")
    for side in ("OldValue", "NewValue"):
        if side in entry:
            described[f"{side}_columns"] = _describe_values(entry[side])
    return described


def _describe_result(result: dict) -> dict:
    """Reduce a tool result to a printable, value-free summary."""
    summary = {
        key: result[key]
        for key in (
            "normalized",
            "count",
            "has_more",
            "total_record_count",
            # The tool's own census, added for parity with its column-scoped sibling:
            # this is the call that sees the wider subtype mix, so it needs it most.
            "detail_types",
            "unclassified_typeless_count",
            "audit_configuration_events_count",
        )
        if key in result
    }
    if "paging_cookie" in result:
        summary["paging_cookie_len"] = len(result["paging_cookie"])
    if "audit_details" in result:
        summary["audit_details"] = [
            _describe_detail(entry) for entry in result["audit_details"][:3]
        ]
    if "audit_configuration_events" in result:
        summary["audit_configuration_events"] = [
            _describe_detail(entry) for entry in result["audit_configuration_events"][:3]
        ]
    if "message" in result:
        summary["message"] = result["message"]
    if "raw_response" in result:
        summary["raw_response_top_level_keys"] = (
            sorted(result["raw_response"])
            if isinstance(result["raw_response"], dict)
            else type(result["raw_response"]).__name__
        )
    return summary


def _dump(label: str, payload: Any) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(f"\n=== {label} ===")
    print(_redact(text))


def _looks_like_configuration_row(entry: Any) -> bool:
    """The configuration shape, all four conditions.

    No ``@odata.type`` at all, ``AuditRecord`` and nothing else, and an ALL-ZERO
    ``AuditRecord._objectid_value`` — the row is about auditing itself, so it points at
    no record. Mirrors ``_is_audit_configuration_event`` in the tool so this suite can
    assert the partition from the outside rather than trusting the implementation.
    """
    if not isinstance(entry, dict) or "@odata.type" in entry:
        return False
    if {key for key in entry if "@odata." not in key} != {"AuditRecord"}:
        return False
    audit_record = entry.get("AuditRecord")
    if not isinstance(audit_record, dict):
        return False
    objectid = audit_record.get("_objectid_value")
    if not isinstance(objectid, str):
        return False
    return objectid.strip().strip("{}").lower() == _ALL_ZERO_GUID


# ---------------------------------------------------------------------------
# Live discovery — no table or record id is ever hardcoded
# ---------------------------------------------------------------------------


async def _entity_set_name(logical_name: str) -> str | None:
    """Resolve singular -> plural via the key predicate.

    ``$filter`` on the ROOT EntityDefinitions collection is refused with HTTP 400
    [0x80060888] on this platform, so the key predicate is the only route.
    """
    response = await _get(
        f"/EntityDefinitions(LogicalName='{logical_name}')?$select=EntitySetName"
    )
    if response.status_code != 200:
        return None
    value = response.json().get("EntitySetName")
    return value if isinstance(value, str) and value else None


async def _audited_record() -> tuple[str, str] | None:
    """Find a live (entity_set, record_id) that has audit history.

    Walks recent audit rows and resolves the plural for each candidate table. Returns
    None when the org has no usable audit data, so callers skip rather than fail.
    """
    response = await _get(
        "/audits?$select=objecttypecode,_objectid_value"
        f"&$orderby=createdon desc&$top={_AUDIT_PAGE_SIZE}"
    )
    if response.status_code != 200:
        print(
            "\n=== audit discovery unavailable: HTTP "
            f"{response.status_code} {_redact(response.text[:300])} ==="
        )
        return None

    rows = response.json().get("value", [])
    print(f"\n=== discovered {len(rows)} recent audit row(s) ===")
    seen: dict[str, str | None] = {}
    for row in rows:
        table = row.get("objecttypecode")
        record_id = row.get("_objectid_value")
        if not (isinstance(table, str) and isinstance(record_id, str)):
            continue
        if table not in seen:
            seen[table] = await _entity_set_name(table)
        entity_set = seen[table]
        if not entity_set:
            continue
        print(
            f"=== selected audited record: table='{table}' "
            f"entity_set='{entity_set}' (record id redacted) ==="
        )
        return entity_set, record_id
    return None


# ---------------------------------------------------------------------------
# The raw platform response — measured, not assumed
# ---------------------------------------------------------------------------


async def test_raw_call_records_the_nested_shape_and_the_configuration_rows() -> None:
    """Call the function with the exact URL the tool builds and pin what comes back.

    Prints the top-level keys, the container's keys, the partition and the sign of
    ``TotalRecordCount`` — never an audit value. If this fails, the tool's normalization
    is reading a payload the platform no longer sends.
    """
    target = await _audited_record()
    if target is None:
        pytest.skip("this org has no retrievable audit history")
    entity_set, record_id = target

    base_url = resolve_base_url(_url())
    # Exactly the alias spelling src/dataverse_mcp/tools/security.py builds: a
    # single-quoted, NON-urlencoded @odata.id. Live-tested against three alternatives,
    # all four byte-identical.
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveRecordChangeHistory(Target=@p1)"
        f"?@p1={{'@odata.id':'{entity_set}({record_id})'}}"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url, headers=_headers())

    print("\n=== RetrieveRecordChangeHistory raw HTTP call ===")
    print(f"url (org host + guids redacted): {_redact(url)}")
    print(f"status: {response.status_code}")
    print(f"RESPONSE SIZE: {len(response.content)} bytes")
    assert response.status_code == 200, (
        "the single-quoted Target alias was rejected — HTTP "
        f"{response.status_code}: {_redact(response.text[:1000])}"
    )

    body = response.json()
    top_level = sorted(k for k in body if not k.startswith("@odata."))
    print(f"top_level_keys (envelope stripped): {top_level}")
    assert "AuditDetailCollection" in top_level, (
        "the response did NOT nest under AuditDetailCollection — the tool's "
        f"normalization cannot find the entries. Got: {top_level}"
    )

    container = body["AuditDetailCollection"]
    assert isinstance(container, dict), (
        f"AuditDetailCollection is a {type(container).__name__}, not an object"
    )
    print(f"container_keys: {sorted(container)}")
    details = container.get("AuditDetails")
    assert isinstance(details, list), (
        f"AuditDetails is a {type(details).__name__}, not a list"
    )
    print(f"AuditDetails count: {len(details)}")
    _dump("first entries (VALUES REDACTED)", [_describe_detail(d) for d in details[:3]])

    # THE CONFIGURATION ROWS, pinned at the raw-HTTP level so the partition in the tool
    # is measured against the platform rather than against its own helper.
    configuration_rows = [d for d in details if _looks_like_configuration_row(d)]
    changes = [d for d in details if not _looks_like_configuration_row(d)]
    print(
        f"partition: {len(details)} raw entries = {len(changes)} change(s) + "
        f"{len(configuration_rows)} audit-configuration row(s)"
    )
    assert len(details) == len(changes) + len(configuration_rows)

    # A count, not customer data. Live-observed as -1 ("not counted"), which is why the
    # tool suppresses negatives instead of emitting them.
    total = container.get("TotalRecordCount")
    print(f"TotalRecordCount: {total!r} (a negative value is not a count)")
    print(f"MoreRecords: {container.get('MoreRecords')!r}")


# ---------------------------------------------------------------------------
# The tool itself
# ---------------------------------------------------------------------------


async def test_tool_returns_a_normalized_record_history() -> None:
    """Happy path: configuration rows are separated out and never counted as changes."""
    target = await _audited_record()
    if target is None:
        pytest.skip("this org has no retrievable audit history")
    entity_set, record_id = target

    result = await _call(entity_set_name=entity_set, record_id=record_id)
    _dump("tool result (VALUES REDACTED)", _describe_result(result))

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["normalized"] is True, (
        "the AuditDetailCollection container could not be located — read "
        "raw_response_top_level_keys printed above before trusting this tool"
    )
    assert isinstance(result["audit_details"], list)
    assert result["count"] == len(result["audit_details"])
    assert isinstance(result["has_more"], bool)

    # detail_types — parity with the column-scoped sibling. This is the call that sees
    # the WIDER subtype mix, so it is the one that needs the census most.
    assert isinstance(result["detail_types"], dict)
    assert sum(result["detail_types"].values()) == result["count"]

    # The tripwire on the classifier: 0 everywhere observed. A non-zero value is not a
    # tool defect — the entry was KEPT as a change — but it means Dataverse emitted a
    # typeless entry that is not the configuration shape, which no live call has yet
    # produced and which the classifier's structural assumption should be re-read for.
    assert result["unclassified_typeless_count"] == 0, (
        "a typeless entry was kept as a change: read detail_types above, then re-read "
        "_is_audit_configuration_event before trusting the partition"
    )

    # The audit-configuration rows, WHEN THEY ARRIVE, are separated out, never counted
    # as changes and never dropped. Zero of them is a normal answer: they show up only
    # when an audit-configuration change falls inside this record's history window.
    assert isinstance(result["audit_configuration_events"], list)
    assert result["audit_configuration_events_count"] == len(
        result["audit_configuration_events"]
    )
    assert all(
        _looks_like_configuration_row(entry)
        for entry in result["audit_configuration_events"]
    ), "an entry that is not the bare AuditRecord shape was misfiled as configuration"
    assert not any(
        _looks_like_configuration_row(entry) for entry in result["audit_details"]
    ), "an audit-configuration row leaked into audit_details and is counted as a change"
    if result["audit_configuration_events_count"]:
        assert "audit_configuration_events" in result["message"]

    # -1 means "not counted" and must never reach the caller as a count.
    if "total_record_count" in result:
        assert isinstance(result["total_record_count"], int)
        assert not isinstance(result["total_record_count"], bool)
        assert result["total_record_count"] >= 0, (
            "a negative TotalRecordCount means 'not counted' and must be suppressed, "
            "not handed to the caller as a count"
        )


async def test_trimming_reports_the_true_magnitude() -> None:
    """top trims client-side because PagingInfo is not sent; has_more must say so.

    Live, ``MoreRecords`` comes back ``false`` even when the trim has cut rows, so a
    ``has_more`` computed from the server flag alone tells the caller they have
    everything when they do not.
    """
    target = await _audited_record()
    if target is None:
        pytest.skip("this org has no retrievable audit history")
    entity_set, record_id = target

    full = await _call(entity_set_name=entity_set, record_id=record_id)
    if full.get("count", 0) < 2:
        pytest.skip("the discovered record has fewer than 2 genuine audited changes")

    trimmed = await _call(entity_set_name=entity_set, record_id=record_id, top=1)
    print(
        f"\n=== full count={full['count']} (has_more={full['has_more']}) "
        f"trimmed count={trimmed['count']} (has_more={trimmed['has_more']}) ==="
    )
    assert trimmed["count"] == 1
    assert trimmed["has_more"] is True, (
        "the trim dropped genuine changes but has_more reported only the server's "
        "MoreRecords, so the caller believes the page is complete"
    )


async def test_a_wrong_entity_set_404s_but_a_missing_record_returns_an_empty_200() -> None:
    """The two failure modes are NOT the same, contrary to the original docstring.

    * a VALID id with the SINGULAR (wrong) entity set is refused by the URL parser:
      HTTP 404 naming the bad SEGMENT, not the record;
    * a well-formed but NONEXISTENT record id with the CORRECT plural is a plain HTTP
      200 with zero genuine changes (plus any audit-configuration rows, which are not
      guaranteed to be present — their count varies by target) — on this entity set
      Dataverse does not validate the target, so an empty answer is not evidence the
      record is there. This is also what an auditing-disabled org returns: never an
      HTTP error.

    These are two OUTCOMES, not the two exhaustive failure modes: see the module
    docstring for the third (a 404 ``[0x80048d02]`` that really does mean the row is
    missing), which is deliberately not probed here.
    """
    entity_set = await _entity_set_name(_FALLBACK_TABLE) or _FALLBACK_ENTITY_SET
    row = await _get(f"/{entity_set}?$select={_FALLBACK_TABLE}id&$top=1")
    if row.status_code != 200 or not row.json().get("value"):
        pytest.skip(f"this org has no readable {_FALLBACK_TABLE} row")
    record_id = row.json()["value"][0][f"{_FALLBACK_TABLE}id"]

    # A VALID record id paired with the SINGULAR (wrong) entity set name.
    wrong_set = await _call(entity_set_name=_FALLBACK_TABLE, record_id=record_id)
    print(f"\n=== wrong entity set: {_redact(json.dumps(wrong_set))} ===")
    assert wrong_set["error"] is True, (
        "the singular entity set was accepted — the tool's guidance is now stale"
    )
    assert "404" in wrong_set["message"], (
        f"wrong entity set returned {wrong_set['message'][:200]!r}, not an HTTP 404"
    )
    assert _FALLBACK_TABLE in wrong_set["message"], (
        "the 404 is expected to NAME the bad segment, which is what distinguishes it "
        f"from a missing record: {wrong_set['message'][:200]!r}"
    )

    # The correct plural paired with a well-formed but nonexistent record id. This
    # holds for accounts and for 11 of the other 14 entity sets swept on one org, but
    # it is NOT a platform-wide rule: 'audits' answered 404 [0x80048d02] to the same
    # bogus GUID, meaning the row is genuinely missing. That is not asserted here — it
    # depends on the org's tables, and this suite stays org-agnostic.
    absent = await _call(entity_set_name=entity_set, record_id=_ABSENT_RECORD_ID)
    _dump("absent record (VALUES REDACTED)", _describe_result(absent))
    assert not absent.get("error"), (
        "a nonexistent record id is live-confirmed to return HTTP 200, not an error — "
        f"got: {absent.get('message')}"
    )
    assert absent["normalized"] is True
    assert absent["count"] == 0, (
        "a record that does not exist cannot have changes; anything counted here is "
        "an audit-configuration row being mistaken for a result"
    )
    assert absent["audit_details"] == []
    assert absent["has_more"] is False
    assert "total_record_count" not in absent or absent["total_record_count"] >= 0
