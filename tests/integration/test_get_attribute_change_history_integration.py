"""Live integration coverage for dataverse_get_attribute_change_history.

``RetrieveAttributeChangeHistory`` is an unbound GET carrying TWO parameters of two
different EDM types, each needing its own escaping regime in one URL:

* ``Target`` (``crmbaseentity``) — a JSON ``@odata.id`` parameter alias,
  percent-encoded ONLY (``encode_odata_literal`` would corrupt the JSON);
* ``AttributeLogicalName`` (``Edm.String``) — a hand-built single-quoted literal
  which must NOT be percent-encoded a second time.

What this suite is here to establish live, because every documented shape in this
batch has differed from the real one somewhere:

* the payload really does nest TWO levels (``AuditDetailCollection`` ->
  ``AuditDetails``), so the record-history extraction cannot be reused flat;
* ``@odata.type`` per entry — which AuditDetail subtypes a COLUMN-scoped call
  actually returns (``AttributeAuditDetail`` is expected, nothing guarantees it);
* whether ``TotalRecordCount`` / ``MoreRecords`` / ``PagingCookie`` arrive at all
  when ``PagingInfo`` is not sent;
* THE TWO SHAPE TRAPS: ``organizations.isauditenabled`` is a PLAIN ``Edm.Boolean``
  while ``IsAuditEnabled`` on EntityMetadata AND AttributeMetadata is a
  ``BooleanManagedProperty`` OBJECT — reading the object as a bool reports
  "auditing on" unconditionally;
* THE CONFIGURATION ROWS: org-level audit-CONFIGURATION rows (no ``@odata.type``,
  ``AuditRecord`` and nothing else, and an all-zero ``AuditRecord._objectid_value``)
  MAY accompany a response, and counting them reports changes that do not exist. They
  are NOT unconditional: they arrive when an audit-configuration change falls inside
  the TARGET RECORD'S own history window, so their presence and count vary by target
  and ``audit_configuration_events_count: 0`` is a normal answer (live: a record
  created after the last audit-config change got 0; two older records got 4 each).
  They must be partitioned out of the changes by SHAPE — their position in the list is
  not a contract — and surfaced, not dropped;
* the two failure modes, which are NOT the same: a valid id with the WRONG (singular)
  entity set is an HTTP 404 naming the bad segment, while a NONEXISTENT record id is
  a plain HTTP 200 with zero genuine changes — Dataverse never checks that the target
  record exists.

SECURITY: audit rows carry REAL BUSINESS VALUES in ``OldValue`` / ``NewValue``.
Nothing in this file prints an audit value. Entries are reported by ``@odata.type``,
key NAMES, value TYPES and lengths only; the org host and every GUID are redacted on
every line that is printed at all.

Nothing is hardcoded: the table, column and record are discovered live from the
``audits`` table, and the suite skips rather than fails on an org where auditing is
off or nothing has been audited.

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
from dataverse_mcp.models import GetAttributeChangeHistoryInput
from dataverse_mcp.tools.security import dataverse_get_attribute_change_history

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Update operations are the ones that carry attribute-level before/after values.
_UPDATE_OPERATION = 2
_AUDIT_PAGE_SIZE = 50

# A table every org has, used for the negative/ambiguity probes where no audit data
# is needed at all.
_FALLBACK_TABLE = "account"
_FALLBACK_ENTITY_SET = "accounts"
_FALLBACK_COLUMN = "name"

_ABSENT_RECORD_ID = "00000000-0000-0000-0000-0000000000ff"

# Every audit-CONFIGURATION row carries this as AuditRecord._objectid_value: the row is
# about auditing itself, so it points at no record. Live: 11 rows, zero divergence.
_ALL_ZERO_GUID = "00000000-0000-0000-0000-000000000000"

# The trimming test needs a column with >= 2 audited changes. Discovery verifies each
# candidate through the tool rather than taking the first usable hit, so this caps how
# many live verification calls that costs before the test skips.
_MAX_VERIFICATION_CALLS = 10

# The organization record's own audit toggle: switching auditing off is itself an
# audited attribute change, so this pair has genuine history on any org where auditing
# was ever turned on and then off. The record id is DISCOVERED at runtime — never
# hardcoded.
_ORG_TABLE = "organization"
_ORG_ENTITY_SET = "organizations"
_ORG_AUDIT_COLUMN = "isauditenabled"

_DIAGNOSES = {
    "auditing_off_at_organization",
    "auditing_off_at_table",
    "auditing_off_at_column",
    "auditing_enabled_no_changes_recorded",
    "undetermined",
}

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
            await dataverse_get_attribute_change_history(
                GetAttributeChangeHistoryInput(dataverse_url=_url(), **kwargs), ctx
            )
        )


# ---------------------------------------------------------------------------
# Redacting describers — NEVER print an audit value
# ---------------------------------------------------------------------------


def _describe_values(payload: Any) -> Any:
    """Reduce an OldValue/NewValue entity dict to column NAMES and value TYPES.

    The column logical names are schema, not business data, so they are safe to
    print; the values are real customer data and are replaced by their type and
    length. This is the only function that ever looks at OldValue/NewValue.
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
        # An operation code and a timestamp's TYPE are metadata, not business data.
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
            "detail_types",
            "unclassified_typeless_count",
            "audit_configuration_events_count",
            "audit_configuration",
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


# ---------------------------------------------------------------------------
# Live discovery — no table, column or record id is ever hardcoded
# ---------------------------------------------------------------------------


async def _entity_set_name(logical_name: str) -> str | None:
    """Resolve singular -> plural via the key predicate.

    $filter on the ROOT EntityDefinitions collection is refused with HTTP 400
    [0x80060888] on this platform, which is exactly why the tool takes both names
    instead of deriving one from the other.
    """
    response = await _get(
        f"/EntityDefinitions(LogicalName='{logical_name}')?$select=EntitySetName"
    )
    if response.status_code != 200:
        return None
    value = response.json().get("EntitySetName")
    return value if isinstance(value, str) and value else None


async def _organization_id() -> str | None:
    """Discover this org's own record id — never hardcoded, never printed."""
    response = await _get(f"/{_ORG_ENTITY_SET}?$select=organizationid&$top=1")
    if response.status_code != 200:
        return None
    rows = response.json().get("value", [])
    value = rows[0].get("organizationid") if rows and isinstance(rows[0], dict) else None
    return value if isinstance(value, str) and value else None


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


async def _audited_target(min_changes: int = 1) -> tuple[str, str, str, str] | None:
    """Find a live (entity_set, record_id, table, column) that has audit history.

    Walks recent UPDATE audit rows, resolves the plural for each candidate table,
    and asks RetrieveAuditDetails which column that row touched. Returns None when
    the org has no usable audit data, so callers skip rather than fail.

    ``min_changes >= 2`` makes the choice DETERMINISTIC IN INTENT instead of taking the
    first usable hit: every candidate column is verified through the tool and rejected
    unless it really carries that many genuine changes. Taking the first hit happened to
    land on a multi-change column on one org, so the trimming test passed by luck; as
    unrelated audit activity accrues the first hit drifts onto a 1-entry column and the
    test skips for a reason that has nothing to do with the code. The verification is
    bounded by ``_MAX_VERIFICATION_CALLS`` so a large org cannot make this walk forever.
    Nothing here is hardcoded — it works on any org, and returns None when the org
    genuinely has no such column.
    """
    response = await _get(
        f"/audits?$select=auditid,objecttypecode,_objectid_value"
        f"&$filter=operation eq {_UPDATE_OPERATION}"
        f"&$orderby=createdon desc&$top={_AUDIT_PAGE_SIZE}"
    )
    if response.status_code != 200:
        print(
            "\n=== audit discovery unavailable: HTTP "
            f"{response.status_code} {_redact(response.text[:300])} ==="
        )
        return None

    rows = response.json().get("value", [])
    print(f"\n=== discovered {len(rows)} recent update audit row(s) ===")
    seen: dict[str, str | None] = {}
    verified: set[tuple[str, str]] = set()
    for row in rows:
        table = row.get("objecttypecode")
        record_id = row.get("_objectid_value")
        audit_id = row.get("auditid")
        if not (
            isinstance(table, str) and isinstance(record_id, str)
            and isinstance(audit_id, str)
        ):
            continue
        if table not in seen:
            seen[table] = await _entity_set_name(table)
        entity_set = seen[table]
        if not entity_set:
            continue
        detail = await _get(
            f"/audits({audit_id})/Microsoft.Dynamics.CRM.RetrieveAuditDetails"
        )
        if detail.status_code != 200:
            continue
        changed = detail.json().get("AuditDetail")
        if not isinstance(changed, dict):
            continue
        # Column NAMES only — the values are never read here.
        columns = [
            key
            for side in ("NewValue", "OldValue")
            for key in (changed.get(side) or {})
            if isinstance(changed.get(side), dict) and not key.startswith("@odata.")
        ]
        if not columns:
            continue
        if min_changes <= 1:
            print(
                f"=== selected audited target: table='{table}' "
                f"entity_set='{entity_set}' column='{columns[0]}' (record redacted) ==="
            )
            return entity_set, record_id, table, columns[0]

        # Verify rather than assume: one audit row proves the column changed ONCE.
        for column in dict.fromkeys(columns):
            if (record_id, column) in verified:
                continue
            if len(verified) >= _MAX_VERIFICATION_CALLS:
                print(
                    f"=== gave up after {_MAX_VERIFICATION_CALLS} verification call(s) "
                    f"without finding a column with {min_changes}+ changes ==="
                )
                return None
            verified.add((record_id, column))
            probe = await _call(
                entity_set_name=entity_set,
                record_id=record_id,
                table_logical_name=table,
                column_logical_name=column,
            )
            # Column NAMES and a COUNT only — no audit value is read or printed.
            found = probe.get("count", 0) if not probe.get("error") else 0
            print(
                f"=== candidate table='{table}' column='{column}': {found} genuine "
                "change(s) (record redacted) ==="
            )
            if found >= min_changes:
                print(
                    f"=== selected audited target: table='{table}' "
                    f"entity_set='{entity_set}' column='{column}' with {found} "
                    "change(s) (record redacted) ==="
                )
                return entity_set, record_id, table, column
    return None


# ---------------------------------------------------------------------------
# The two shape traps — asserted against the platform, not the docs
# ---------------------------------------------------------------------------


async def test_the_three_audit_flags_are_not_the_same_shape() -> None:
    """org flag is a PLAIN bool; table/column flags are BooleanManagedProperty OBJECTS.

    This is the assertion the probe helpers stand on. If it ever fails, the
    diagnosis block is reading the wrong shape and will report "auditing on"
    unconditionally — read the printed shapes and fix _managed_property_bool /
    _plain_bool before trusting any diagnosis.
    """
    org = await _get("/organizations?$select=isauditenabled&$top=1")
    assert org.status_code == 200, (
        f"org probe failed — HTTP {org.status_code}: {_redact(org.text[:300])}"
    )
    org_flag = org.json()["value"][0].get("isauditenabled")
    print(
        f"\n=== organizations.isauditenabled: {type(org_flag).__name__} "
        f"(value printed because it is configuration, not customer data: {org_flag}) ==="
    )
    assert isinstance(org_flag, bool), (
        "organizations.isauditenabled arrived as "
        f"{type(org_flag).__name__} — the org probe reads it as a PLAIN Edm.Boolean"
    )

    table = await _get(
        f"/EntityDefinitions(LogicalName='{_FALLBACK_TABLE}')?$select=IsAuditEnabled"
    )
    assert table.status_code == 200, (
        f"table probe failed — HTTP {table.status_code}: {_redact(table.text[:300])}"
    )
    table_flag = table.json().get("IsAuditEnabled")
    _dump("EntityMetadata.IsAuditEnabled", table_flag)
    assert isinstance(table_flag, dict), (
        "EntityMetadata.IsAuditEnabled is documented as a BooleanManagedProperty "
        f"OBJECT but arrived as {type(table_flag).__name__}"
    )
    assert isinstance(table_flag.get("Value"), bool), (
        "BooleanManagedProperty.Value is not a bool — the tool would report "
        "undetermined for every table"
    )

    column = await _get(
        f"/EntityDefinitions(LogicalName='{_FALLBACK_TABLE}')/Attributes"
        f"?$filter=LogicalName eq '{_FALLBACK_COLUMN}'"
        "&$select=LogicalName,IsAuditEnabled"
    )
    assert column.status_code == 200, (
        "$filter on the NESTED /Attributes collection was refused — HTTP "
        f"{column.status_code}: {_redact(column.text[:300])}. Only the ROOT "
        "EntityDefinitions collection is supposed to reject it."
    )
    rows = column.json().get("value", [])
    assert rows, f"no metadata row for {_FALLBACK_TABLE}.{_FALLBACK_COLUMN}"
    column_flag = rows[0].get("IsAuditEnabled")
    _dump("AttributeMetadata.IsAuditEnabled", column_flag)
    assert isinstance(column_flag, dict), (
        "AttributeMetadata.IsAuditEnabled is documented as a BooleanManagedProperty "
        f"OBJECT but arrived as {type(column_flag).__name__}"
    )
    assert isinstance(column_flag.get("Value"), bool)


async def test_raw_call_records_the_nested_response_shape() -> None:
    """Call the function with the exact URL the tool builds and pin the nesting.

    Prints the top-level keys, the container's keys and the per-entry
    ``@odata.type`` values — never an audit value.
    """
    target = await _audited_target()
    if target is None:
        pytest.skip("this org has no retrievable attribute-level audit history")
    entity_set, record_id, _table, column = target

    base_url = resolve_base_url(_url())
    # Exactly the two alias encodings src/dataverse_mcp/tools/security.py builds:
    # Target as a percent-encoded JSON @odata.id, AttributeLogicalName as a
    # single-quoted literal that is NOT percent-encoded again.
    alias = (
        '%7B%22@odata.id%22%3A%22'
        + entity_set
        + "%28"
        + record_id
        + "%29%22%7D"
    )
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveAttributeChangeHistory(Target=@t,AttributeLogicalName=@a)"
        f"?@t={alias}&@a='{column}'"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url, headers=_headers())

    print("\n=== RetrieveAttributeChangeHistory raw HTTP call ===")
    print(f"url (org host + guids redacted): {_redact(url)}")
    print(f"status: {response.status_code}")
    print(f"RESPONSE SIZE: {len(response.content)} bytes")
    assert response.status_code == 200, (
        "the two-alias URL was rejected — HTTP "
        f"{response.status_code}: {_redact(response.text[:1000])}"
    )

    body = response.json()
    top_level = sorted(k for k in body if not k.startswith("@odata."))
    print(f"top_level_keys (envelope stripped): {top_level}")
    assert "AuditDetailCollection" in top_level, (
        "the response did NOT nest under AuditDetailCollection — the record-history "
        f"extraction cannot be reused. Got: {top_level}"
    )

    container = body["AuditDetailCollection"]
    assert isinstance(container, dict), (
        f"AuditDetailCollection is a {type(container).__name__}, not an object"
    )
    print(f"container_keys: {sorted(container)}")
    print(
        "container_value_types: "
        + json.dumps(
            {k: type(v).__name__ for k, v in sorted(container.items())}, sort_keys=True
        )
    )
    details = container.get("AuditDetails")
    assert isinstance(details, list), (
        f"AuditDetails is a {type(details).__name__}, not a list"
    )
    print(f"AuditDetails count: {len(details)}")
    _dump("first entries (VALUES REDACTED)", [_describe_detail(d) for d in details[:3]])

    # THE PREAMBLE, pinned at the raw-HTTP level so the partition in the tool is
    # measured against the platform rather than against its own helper.
    configuration_rows = [d for d in details if _looks_like_configuration_row(d)]
    changes = [d for d in details if not _looks_like_configuration_row(d)]
    print(
        f"partition: {len(details)} raw entries = {len(changes)} change(s) + "
        f"{len(configuration_rows)} audit-configuration row(s)"
    )
    assert len(details) == len(changes) + len(configuration_rows)

    # A count, not customer data. Live-observed as -1 ("not counted") with and without
    # PagingInfo, which is why the tool suppresses negatives instead of emitting them.
    total = container.get("TotalRecordCount")
    print(f"TotalRecordCount: {total!r} (a negative value is not a count)")


# ---------------------------------------------------------------------------
# The tool itself
# ---------------------------------------------------------------------------


async def test_tool_returns_a_normalized_column_history() -> None:
    """Happy path through the tool against a live audited column."""
    target = await _audited_target()
    if target is None:
        pytest.skip("this org has no retrievable attribute-level audit history")
    entity_set, record_id, table, column = target

    result = await _call(
        entity_set_name=entity_set,
        record_id=record_id,
        table_logical_name=table,
        column_logical_name=column,
    )
    _dump("tool result (VALUES REDACTED)", _describe_result(result))

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["normalized"] is True, (
        "the AuditDetailCollection container could not be located — read "
        "raw_response_top_level_keys printed above before trusting this tool"
    )
    assert isinstance(result["audit_details"], list)
    assert result["count"] == len(result["audit_details"])
    assert isinstance(result["has_more"], bool)
    assert isinstance(result["detail_types"], dict)
    if "total_record_count" in result:
        assert isinstance(result["total_record_count"], int)
        assert result["total_record_count"] >= 0, (
            "a negative TotalRecordCount means 'not counted' and must be suppressed, "
            "not handed to the caller as a count"
        )
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
    # Non-empty results must NOT carry the diagnosis block: it costs three requests
    # and only exists to explain an answer with no changes in it.
    if result["count"]:
        assert "audit_configuration" not in result
        assert result["count"] == sum(result["detail_types"].values())
    else:
        assert result["audit_configuration"]["diagnosis"] in _DIAGNOSES


async def test_trimming_reports_the_true_magnitude() -> None:
    """top trims client-side because PagingInfo is not sent; has_more must say so.

    Discovery asks for a column with 2+ genuine changes rather than taking the first
    usable audit row, so the assertion below is reached deterministically instead of
    depending on which record the most recent audit activity happened to surface. A
    skip here means the ORG has no multi-change audited column within the bounded scan,
    not that anything is wrong with the tool.
    """
    target = await _audited_target(min_changes=2)
    if target is None:
        pytest.skip(
            "this org has no audited column with 2+ genuine changes within the bounded "
            "candidate scan, so there is nothing for top to trim"
        )
    entity_set, record_id, table, column = target

    full = await _call(
        entity_set_name=entity_set,
        record_id=record_id,
        table_logical_name=table,
        column_logical_name=column,
    )
    assert full.get("count", 0) >= 2, (
        "discovery verified this column had 2+ changes moments ago; a lower count now "
        f"means the tool disagrees with its own earlier answer: {full.get('message')}"
    )

    trimmed = await _call(
        entity_set_name=entity_set,
        record_id=record_id,
        table_logical_name=table,
        column_logical_name=column,
        top=1,
    )
    print(f"\n=== full count={full['count']} trimmed count={trimmed['count']} ===")
    assert trimmed["count"] == 1
    assert trimmed["has_more"] is True


async def test_an_empty_result_is_diagnosed_not_just_returned() -> None:
    """An unaudited column must come back with a real explanation, not just [].

    Uses the table's own primary id column, which is never user-editable and so is
    the safest bet for "no attribute change history" on any org.

    This test used to SKIP here on "the primary id column unexpectedly has audit
    history on this org" — that "history" was the audit-configuration rows that
    accompany every response. Because they kept ``count`` non-zero, the
    diagnosis block below was never built and the whole probe feature was dead code.
    Zero genuine changes is now asserted, not skipped over.
    """
    entity_set = await _entity_set_name(_FALLBACK_TABLE) or _FALLBACK_ENTITY_SET
    row = await _get(f"/{entity_set}?$select={_FALLBACK_TABLE}id&$top=1")
    if row.status_code != 200 or not row.json().get("value"):
        pytest.skip(f"this org has no readable {_FALLBACK_TABLE} row")
    record_id = row.json()["value"][0][f"{_FALLBACK_TABLE}id"]

    result = await _call(
        entity_set_name=entity_set,
        record_id=record_id,
        table_logical_name=_FALLBACK_TABLE,
        column_logical_name=f"{_FALLBACK_TABLE}id",
    )
    _dump("empty-path result (VALUES REDACTED)", _describe_result(result))

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["count"] == 0, (
        "the primary id column is not user-editable, so any 'change' counted here is "
        "the audit-configuration preamble being mistaken for a result"
    )
    assert result["audit_details"] == []
    assert result["has_more"] is False
    # The rows that used to masquerade as results are still surfaced, with the
    # explanation of what they are.
    if result["audit_configuration_events_count"]:
        assert all(
            _looks_like_configuration_row(entry)
            for entry in result["audit_configuration_events"]
        )
        assert "audit_configuration_events" in result["message"]

    block = result["audit_configuration"]
    assert block["diagnosis"] in _DIAGNOSES
    assert block["message"]
    for level in (
        "organization_audit_enabled",
        "table_audit_enabled",
        "column_audit_enabled",
    ):
        assert block[level] is None or isinstance(block[level], bool), (
            f"{level} must be a bool or null, never a truthy object — got "
            f"{type(block[level]).__name__}"
        )
    if block["diagnosis"] == "undetermined":
        assert block["probe_errors"], (
            "undetermined must always carry the reason it could not decide"
        )
    else:
        # A determinate diagnosis must be backed by a definite flag.
        assert any(
            block[level] is False
            for level in (
                "organization_audit_enabled",
                "table_audit_enabled",
                "column_audit_enabled",
            )
        ) or all(
            block[level] is True
            for level in (
                "organization_audit_enabled",
                "table_audit_enabled",
                "column_audit_enabled",
            )
        )


async def test_a_wrong_entity_set_404s_but_a_missing_record_returns_an_empty_200() -> None:
    """The two failure modes are NOT the same, contrary to the original note.

    * a VALID id with the SINGULAR (wrong) entity set is refused by the URL parser:
      HTTP 404 naming the bad SEGMENT, not the record;
    * a well-formed but NONEXISTENT record id with the CORRECT plural is a plain
      HTTP 200 with zero genuine changes — Dataverse never validates that the target
      record exists, so an empty answer is not evidence the record is there.
    """
    entity_set = await _entity_set_name(_FALLBACK_TABLE) or _FALLBACK_ENTITY_SET
    row = await _get(f"/{entity_set}?$select={_FALLBACK_TABLE}id&$top=1")
    if row.status_code != 200 or not row.json().get("value"):
        pytest.skip(f"this org has no readable {_FALLBACK_TABLE} row")
    record_id = row.json()["value"][0][f"{_FALLBACK_TABLE}id"]

    # A VALID record id paired with the SINGULAR (wrong) entity set name.
    wrong_set = await _call(
        entity_set_name=_FALLBACK_TABLE,
        record_id=record_id,
        table_logical_name=_FALLBACK_TABLE,
        column_logical_name=_FALLBACK_COLUMN,
    )
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

    # The correct plural paired with a well-formed but nonexistent record id.
    absent = await _call(
        entity_set_name=entity_set,
        record_id=_ABSENT_RECORD_ID,
        table_logical_name=_FALLBACK_TABLE,
        column_logical_name=_FALLBACK_COLUMN,
    )
    _dump("absent record (VALUES REDACTED)", _describe_result(absent))
    assert not absent.get("error"), (
        "a nonexistent record id is live-confirmed to return HTTP 200, not an error — "
        f"got: {absent.get('message')}"
    )
    assert absent["normalized"] is True
    assert absent["count"] == 0, (
        "a record that does not exist cannot have changes; anything counted here is "
        "the audit-configuration preamble"
    )
    # ...and being an answer with no changes in it, it must be diagnosed.
    assert absent["audit_configuration"]["diagnosis"] in _DIAGNOSES


async def test_the_organization_audit_toggle_exercises_the_non_empty_path() -> None:
    """A genuine attribute change: switching auditing off is itself audited.

    ``organization.isauditenabled`` is the one pair guaranteed to have real history on
    an org where auditing was turned on and later off. The organization record id is
    DISCOVERED here and never printed.
    """
    org_id = await _organization_id()
    if org_id is None:
        pytest.skip("could not read the organization record")

    result = await _call(
        entity_set_name=_ORG_ENTITY_SET,
        record_id=org_id,
        table_logical_name=_ORG_TABLE,
        column_logical_name=_ORG_AUDIT_COLUMN,
    )
    _dump("organization.isauditenabled (VALUES REDACTED)", _describe_result(result))

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["normalized"] is True
    if not result["count"]:
        pytest.skip(
            "auditing has never been toggled on this org, so even this pair has no "
            "recorded change"
        )

    assert result["count"] == len(result["audit_details"])
    assert result["count"] == sum(result["detail_types"].values())
    # A real change is present, so the diagnosis block must NOT fire.
    assert "audit_configuration" not in result
    for entry in result["audit_details"]:
        assert not _looks_like_configuration_row(entry), (
            "a configuration row was counted as a change to "
            f"{_ORG_TABLE}.{_ORG_AUDIT_COLUMN}"
        )
        assert isinstance(entry, dict)
        # Key NAMES only — OldValue/NewValue hold real values and are never read here.
        if entry.get("@odata.type") == "#Microsoft.Dynamics.CRM.AttributeAuditDetail":
            assert "OldValue" in entry and "NewValue" in entry, (
                "an AttributeAuditDetail is expected to carry both sides; got keys "
                f"{sorted(k for k in entry if not k.startswith('@odata.'))}"
            )
    assert "total_record_count" not in result or result["total_record_count"] >= 0
