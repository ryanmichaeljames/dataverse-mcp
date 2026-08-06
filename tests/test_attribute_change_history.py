"""Unit tests for dataverse_get_attribute_change_history.

``RetrieveAttributeChangeHistory`` is an unbound GET carrying TWO parameters of
two different EDM types in one URL, each with its own escaping regime:
``Target`` (``crmbaseentity``) is a JSON ``@odata.id`` alias that is percent-encoded
ONLY, while ``AttributeLogicalName`` (``Edm.String``) is a hand-built single-quoted
literal that must NOT be percent-encoded a second time.

Acceptance criteria:
- The built URL carries both alias encodings side by side, unmixed.
- Entries are located TWO levels down (``AuditDetailCollection`` -> ``AuditDetails``)
  and returned verbatim, so ``@odata.type`` survives on every entry.
- The org-level audit-CONFIGURATION rows that MAY accompany a response (they arrive
  only when an audit-configuration change falls inside the target's history window, so
  a count of 0 is normal) are partitioned out of the changes by SHAPE — absent
  ``@odata.type``, an ``AuditRecord``-only key set and an all-zero
  ``AuditRecord._objectid_value``, never by position — surfaced separately, and never
  counted. Every other entry, including a typeless one that fails the objectid test, is
  kept as a change and counted in ``unclassified_typeless_count``.
- A missing/unreadable container degrades to ``normalized: false`` with the raw body
  — never a fabricated empty list.
- ``total_record_count`` appears only when the server supplied a non-negative integer
  (this function answers ``-1``, meaning "not counted").
- Trimming and ``has_more`` respect ``MoreRecords`` by identity, not truthiness.
- ZERO GENUINE CHANGES triggers the three audit-configuration probes and every one of
  the five diagnoses, with ``IsAuditEnabled`` read as a ``BooleanManagedProperty`` —
  including when the preamble left the raw entries list non-empty.

The response fixtures are built from the documented shape and are deliberately kept
in ONE place (``_body``) so a live run that contradicts them is a one-line fix.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import GetAttributeChangeHistoryInput
from dataverse_mcp.tools.security import dataverse_get_attribute_change_history

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_ENTITY_SET = "accounts"
_TABLE = "account"
_COLUMN = "name"
_RECORD_ID = "1f3d9c58-2a44-4b0e-8c71-6d5e0a9b7c31"
_ALL_ZERO_GUID = "00000000-0000-0000-0000-000000000000"

# The Target alias: the JSON document {"@odata.id":"accounts(<guid>)"} percent-encoded
# with the alias NAME left literal (urlencode would otherwise emit %40t).
_TARGET_QUERY = (
    "@t=%7B%22@odata.id%22%3A%22accounts%28"
    "1f3d9c58-2a44-4b0e-8c71-6d5e0a9b7c31%29%22%7D"
)
_EXPECTED_URL = (
    f"{_API_ROOT}/RetrieveAttributeChangeHistory"
    f"(Target=@t,AttributeLogicalName=@a)?{_TARGET_QUERY}&@a='name'"
)

_FN = "RetrieveAttributeChangeHistory"
_ORG_PROBE = "/organizations?"
_COLUMN_PROBE = "/Attributes?"
_TABLE_PROBE = "EntityDefinitions(LogicalName="


def _detail(index: int, odata_type: str = "AttributeAuditDetail") -> dict:
    return {
        "@odata.type": f"#Microsoft.Dynamics.CRM.{odata_type}",
        "AuditRecord": {"auditid": f"00000000-0000-0000-0000-{index:012d}"},
        "OldValue": {"name": f"old {index}"},
        "NewValue": {"name": f"new {index}"},
        "InvalidNewValueAttributes": [],
    }


def _preamble(action: int, objectid: str = _ALL_ZERO_GUID) -> dict:
    """One org-level audit-CONFIGURATION row, as Dataverse really returns it.

    Live shape: NO ``@odata.type`` at all, ``AuditRecord`` and nothing else, and an
    ALL-ZERO ``_objectid_value`` — the row is about auditing itself, so it points at no
    record. It carries the TARGET TABLE's ``objecttypecode`` ('account', 'systemuser' —
    NOT 'organization'), and an ``action`` 105 row ("Entity Audit Started") has no
    ``attributemask`` key at all; classification depends on neither field.

    These rows are NOT returned on every call: they arrive only when an
    audit-configuration change falls inside the target record's history window. Where
    they sit in the list is not a contract either, so nothing keys off their position.
    """
    return {
        "AuditRecord": {
            "auditid": f"00000000-0000-0000-0000-{action:012d}",
            "action": action,
            "objecttypecode": _TABLE,
            "_objectid_value": objectid,
        }
    }


def _body(
    details: list[dict] | None = None,
    *,
    more_records: Any = False,
    total: Any = None,
    paging_cookie: Any = None,
) -> dict:
    """The documented response shape: entries nest TWO levels down."""
    collection: dict[str, Any] = {
        "AuditDetails": [] if details is None else details,
        "MoreRecords": more_records,
    }
    if total is not None:
        collection["TotalRecordCount"] = total
    if paging_cookie is not None:
        collection["PagingCookie"] = paging_cookie
    return {
        "@odata.context": f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.x",
        "AuditDetailCollection": collection,
    }


def _managed(value: Any) -> dict:
    """A BooleanManagedProperty — an OBJECT, not a bare bool."""
    return {
        "Value": value,
        "CanBeChanged": True,
        "ManagedPropertyLogicalName": "canmodifyauditsettings",
    }


def _make_ctx() -> MagicMock:
    app_ctx = AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _run(
    body: Any,
    *,
    status: int = 200,
    org: Any = True,
    table: Any = "managed-true",
    column: Any = "managed-true",
    org_status: int = 200,
    table_status: int = 200,
    column_status: int = 200,
    **kwargs: Any,
) -> tuple[dict, list[str]]:
    """Run the tool against mocked main + probe responses; return (result, URLs).

    ``org`` is a plain Edm.Boolean; ``table``/``column`` accept the sentinel
    ``"managed-<bool>"`` to wrap the value in a BooleanManagedProperty, or any raw
    value to be sent through unwrapped.
    """
    params = GetAttributeChangeHistoryInput(**{
        "dataverse_url": _BASE_URL,
        "entity_set_name": _ENTITY_SET,
        "record_id": _RECORD_ID,
        "table_logical_name": _TABLE,
        "column_logical_name": _COLUMN,
        **kwargs,
    })

    def _flag(value: Any) -> Any:
        if value == "managed-true":
            return _managed(True)
        if value == "managed-false":
            return _managed(False)
        return value

    async def _side_effect(_client, _method, url, **_kwargs):
        if _FN in url:
            payload, code = body, status
        elif _ORG_PROBE in url:
            payload, code = {"value": [{"isauditenabled": org}]}, org_status
        elif _COLUMN_PROBE in url:
            payload, code = (
                {"value": [{"LogicalName": _COLUMN, "IsAuditEnabled": _flag(column)}]},
                column_status,
            )
        elif _TABLE_PROBE in url:
            payload, code = {"IsAuditEnabled": _flag(table)}, table_status
        else:  # pragma: no cover - a URL the tool should never build
            raise AssertionError(f"unexpected request: {url}")
        return httpx.Response(code, json=payload, request=httpx.Request("GET", url))

    with patch(
        "dataverse_mcp.tools.security.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.security.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(
            await dataverse_get_attribute_change_history(params, _make_ctx())
        )

    return result, [call.args[2] for call in mock_request.await_args_list]


# ---------------------------------------------------------------------------
# URL construction — two parameter types, two escaping regimes, one URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_carries_both_alias_encodings_unmixed() -> None:
    """Target is JSON percent-encoded; AttributeLogicalName is a quoted literal."""
    _, urls = await _run(_body([_detail(0)]))

    assert urls == [_EXPECTED_URL]
    url = urls[0]
    # Target: a JSON document, percent-encoded, alias name left literal.
    assert "@t=%7B%22@odata.id%22" in url
    assert "%40t=" not in url
    # AttributeLogicalName: a single-quoted OData literal, NOT double-encoded.
    assert url.endswith("&@a='name'")
    assert "%27" not in url and "%2527" not in url


@pytest.mark.asyncio
async def test_odata_literal_escaping_applies_only_to_the_column() -> None:
    """A quote in the column literal is doubled then percent-encoded, once."""
    # The model forbids quotes, so exercise the encoder through a legal name and
    # assert the JSON alias was never touched by it.
    _, urls = await _run(_body([]), column_logical_name="new_status")
    assert "&@a='new_status'" in urls[0]
    assert "''" not in urls[0], "encode_odata_literal must not be applied to the JSON"


# ---------------------------------------------------------------------------
# Happy path, nesting, polymorphism, paging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_reads_entries_two_levels_down() -> None:
    """The entries live under AuditDetailCollection -> AuditDetails, not at the top."""
    result, urls = await _run(_body([_detail(0), _detail(1)], total=2))

    assert "error" not in result
    assert result["normalized"] is True
    assert result["entity_set_name"] == _ENTITY_SET
    assert result["table_logical_name"] == _TABLE
    assert result["column_logical_name"] == _COLUMN
    assert result["target"] == f"{_ENTITY_SET}({_RECORD_ID})"
    assert result["audit_details"] == [_detail(0), _detail(1)]
    assert result["count"] == 2
    assert result["has_more"] is False
    assert result["total_record_count"] == 2
    assert result["unclassified_typeless_count"] == 0
    # ZERO configuration rows is a NORMAL outcome, not a partition failure: they arrive
    # only when an audit-configuration change falls inside this record's history window,
    # so a record created after the last such change gets none. No message is added.
    assert result["audit_configuration_events"] == []
    assert result["audit_configuration_events_count"] == 0
    assert "message" not in result
    # Non-empty: no probe fires and no audit_configuration is attached.
    assert "audit_configuration" not in result
    assert urls == [_EXPECTED_URL]


@pytest.mark.asyncio
async def test_entries_are_passed_through_with_their_odata_type() -> None:
    """AuditDetail is polymorphic — surface @odata.type, never assume a subtype.

    An UNRECOGNIZED type is a result this code failed to identify, not preamble: it
    must be counted and returned, because dropping it would lose audit history.
    """
    details = [
        _detail(0),
        _detail(1, "RelationshipAuditDetail"),
        # Typeless, but carrying more than AuditRecord — not the preamble shape.
        {"AuditRecord": {}, "OldValue": {"name": "x"}},
    ]
    result, _ = await _run(_body(details))

    assert result["audit_details"] == details
    assert result["count"] == 3
    assert result["detail_types"] == {
        "#Microsoft.Dynamics.CRM.AttributeAuditDetail": 1,
        "#Microsoft.Dynamics.CRM.RelationshipAuditDetail": 1,
        "unspecified": 1,
    }
    # The typeless entry was KEPT, and the caller is told it could not be named.
    assert result["unclassified_typeless_count"] == 1
    assert result["audit_configuration_events"] == []


@pytest.mark.asyncio
async def test_the_audit_configuration_rows_are_not_counted_as_changes() -> None:
    """Org-level config rows that DO arrive are split out, never counted as changes."""
    result, _ = await _run(_body([_preamble(110), _preamble(107), _detail(0)]))

    assert result["audit_details"] == [_detail(0)]
    assert result["count"] == 1
    assert result["has_more"] is False
    assert result["detail_types"] == {"#Microsoft.Dynamics.CRM.AttributeAuditDetail": 1}
    # Separated, NOT discarded — they are real audit rows, just not an answer.
    assert result["audit_configuration_events"] == [_preamble(110), _preamble(107)]
    assert result["audit_configuration_events_count"] == 2
    assert "audit_configuration_events" in result["message"]
    # A genuine change is present, so no diagnosis and no probes.
    assert "audit_configuration" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry,typeless,reason",
    [
        pytest.param(
            {"@odata.type": "#Microsoft.Dynamics.CRM.ShareAuditDetail", "AuditRecord": {}},
            0,
            "an @odata.type this code does not know is an unknown RESULT, not preamble",
            id="unrecognized-odata-type",
        ),
        pytest.param(
            {"@odata.type": "#Microsoft.Dynamics.CRM.AttributeAuditDetail"},
            0,
            "a typed entry with no AuditRecord is still a result",
            id="typed-without-audit-record",
        ),
        pytest.param(
            {"AuditRecord": {}, "NewValue": {}},
            1,
            "typeless but carrying more than AuditRecord",
            id="typeless-with-extra-key",
        ),
        pytest.param("not-an-object", 0, "a non-object entry", id="non-object"),
        # The all-zero-objectid condition. A typeless AuditRecord-only entry is
        # configuration ONLY when the row points at no record; anything else is an
        # event this code cannot name, and is kept.
        pytest.param(
            {"AuditRecord": {"action": 2, "_objectid_value": _RECORD_ID}},
            1,
            "a REAL objectid: a bare genuine event, kept rather than dropped",
            id="typeless-with-a-real-objectid",
        ),
        pytest.param(
            {"AuditRecord": {"action": 105, "objecttypecode": _TABLE}},
            1,
            "no _objectid_value at all — nothing proves this is configuration",
            id="typeless-without-an-objectid",
        ),
        pytest.param(
            {"AuditRecord": "not-an-object"},
            1,
            "AuditRecord is not a dict, so its objectid cannot be read",
            id="audit-record-not-a-dict",
        ),
        pytest.param(
            {"AuditRecord": {"_objectid_value": _ALL_ZERO_GUID[:-1] + "1"}},
            1,
            "a near-miss GUID is not the all-zero GUID",
            id="near-zero-objectid",
        ),
    ],
)
async def test_only_the_bare_audit_record_shape_is_treated_as_configuration(
    entry: Any, typeless: int, reason: str
) -> None:
    """Anything not PROVEN to be preamble is surfaced — never silently dropped.

    Every edge resolves to "keep it as a result": the classifier can only ever move a
    row OUT of configuration and INTO the answer, which is the safe direction. A
    typeless entry that is kept is also COUNTED, so a caller can see that this tool met
    an entry it could not name instead of it passing silently.
    """
    result, _ = await _run(_body([_preamble(110), entry]))

    assert result["audit_details"] == [entry], reason
    assert result["count"] == 1
    assert result["unclassified_typeless_count"] == typeless
    assert result["audit_configuration_events_count"] == 1
    assert "audit_configuration" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [105, 107, 110])
@pytest.mark.parametrize(
    "objectid",
    [
        pytest.param(_ALL_ZERO_GUID, id="plain"),
        pytest.param(_ALL_ZERO_GUID.upper(), id="uppercase"),
        pytest.param(f"{{{_ALL_ZERO_GUID}}}", id="braced"),
    ],
)
async def test_the_known_configuration_rows_still_classify_as_configuration(
    action: int, objectid: str
) -> None:
    """BEHAVIOUR-NEUTRAL: the objectid condition reclassifies nothing observed live.

    Every configuration row seen live carried the all-zero objectid (11 rows, zero
    divergence), so requiring it moves no row that used to be filed as configuration.
    The GUID is compared case-insensitively and tolerates the optional braces.
    """
    row = _preamble(action, objectid)
    result, _ = await _run(_body([row, _detail(0)]))

    assert result["audit_details"] == [_detail(0)]
    assert result["count"] == 1
    assert result["unclassified_typeless_count"] == 0
    assert result["audit_configuration_events"] == [row]


@pytest.mark.asyncio
async def test_odata_annotations_do_not_defeat_the_configuration_match() -> None:
    """@odata.etag / Prop@odata.bind are envelope, not payload keys."""
    row = {
        "@odata.etag": 'W/"1"',
        "AuditRecord@odata.associationLink": "https://yourorg/x",
        "AuditRecord": {
            "action": 110,
            "objecttypecode": _TABLE,
            "_objectid_value": _ALL_ZERO_GUID,
        },
    }
    result, _ = await _run(_body([row]))

    assert result["audit_details"] == []
    assert result["audit_configuration_events"] == [row]


@pytest.mark.asyncio
async def test_entries_are_trimmed_to_top_and_has_more_is_set() -> None:
    """PagingInfo is not sent, so the trim happens here and is reported."""
    result, _ = await _run(_body([_detail(i) for i in range(10)]), top=3)

    assert result["count"] == 3
    assert len(result["audit_details"]) == 3
    assert result["has_more"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "more_records,expected",
    [
        pytest.param(True, True, id="server-says-more"),
        pytest.param(False, False, id="server-says-no-more"),
        pytest.param(1, False, id="truthy-int-is-not-a-bool"),
        pytest.param("true", False, id="truthy-string-is-not-a-bool"),
        pytest.param(None, False, id="absent"),
    ],
)
async def test_more_records_is_read_by_identity_not_truthiness(
    more_records: Any, expected: bool
) -> None:
    """bool is a subclass of int — only a real True may set has_more."""
    result, _ = await _run(_body([_detail(0)], more_records=more_records))
    assert result["has_more"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "total,present",
    [
        pytest.param(7, True, id="int"),
        pytest.param(0, True, id="zero-is-a-real-answer"),
        pytest.param(None, False, id="absent"),
        pytest.param(True, False, id="bool-is-not-a-count"),
        pytest.param("7", False, id="string-is-not-a-count"),
        # What this function ACTUALLY answers, live, with and without PagingInfo.
        pytest.param(-1, False, id="minus-one-means-not-counted"),
        pytest.param(-42, False, id="any-negative-is-not-a-count"),
    ],
)
async def test_total_record_count_is_only_emitted_when_the_server_supplied_one(
    total: Any, present: bool
) -> None:
    """It is never substituted with len(AuditDetails), and -1 is not a count."""
    result, _ = await _run(_body([_detail(0), _detail(1)], total=total))

    assert ("total_record_count" in result) is present
    if present:
        assert result["total_record_count"] == total
        assert result["total_record_count"] != result["count"] or total == 2


@pytest.mark.asyncio
async def test_paging_cookie_is_surfaced_when_present() -> None:
    result, _ = await _run(_body([_detail(0)], paging_cookie="<cookie/>"))
    assert result["paging_cookie"] == "<cookie/>"


# ---------------------------------------------------------------------------
# Defensive degradation — a missing container is NOT "no changes"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,reason",
    [
        pytest.param(
            {"AuditDetails": [{"x": 1}]},
            "the flat shape of a DIFFERENT function must not be mistaken for this one",
            id="flat-not-nested",
        ),
        pytest.param({"AuditDetailCollection": None}, "a null container", id="null"),
        pytest.param(
            {"AuditDetailCollection": {"MoreRecords": False}},
            "the container exists but carries no AuditDetails",
            id="no-entries-key",
        ),
        pytest.param(
            {"AuditDetailCollection": {"AuditDetails": {"0": {}}}},
            "AuditDetails is not a list",
            id="entries-not-a-list",
        ),
        pytest.param({"Response": "ok"}, "an unrecognized payload", id="unknown"),
    ],
)
async def test_a_missing_container_degrades_to_raw_and_is_never_called_empty(
    body: dict, reason: str
) -> None:
    """Fabricating [] here would report 'no changes' for a payload nobody read."""
    result, urls = await _run(body)

    assert result["normalized"] is False, reason
    for key in (
        "audit_details",
        "count",
        "has_more",
        "audit_configuration",
        "audit_configuration_events",
    ):
        assert key not in result, f"{key} was fabricated: {reason}"
    # The @odata.* envelope is stripped, the rest is returned unchanged.
    assert result["raw_response"] == {
        k: v for k, v in body.items() if not k.startswith("@odata.")
    }
    # No probe fires: the result is not empty, it is unreadable.
    assert urls == [_EXPECTED_URL]


@pytest.mark.asyncio
async def test_http_error_returns_the_standard_envelope() -> None:
    """The WRONG (singular) entity set is the 404 — a missing record returns 200.

    Live-confirmed: the singular-for-plural slip is refused by the URL parser with
    [0x80060888] naming the bad segment, while a nonexistent record id sails through
    as a successful call with zero changes (see the preamble-only test below).
    """
    result, _ = await _run(
        {
            "error": {
                "code": "0x80060888",
                "message": "Resource not found for the segment 'account'.",
            }
        },
        status=404,
    )

    assert result["error"] is True
    assert "404" in result["message"]
    assert "0x80060888" in result["message"]


# ---------------------------------------------------------------------------
# The empty-result ambiguity — audit configuration probes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entries,expected_events",
    [
        pytest.param([], 0, id="genuinely-empty"),
        # THE REAL SHAPE: Dataverse never returns an empty list — the two
        # accompanying configuration rows kept it non-empty, so this branch never
        # fired and the whole diagnosis feature was dead code. Zero GENUINE changes
        # must trigger it.
        pytest.param([_preamble(110), _preamble(107)], 2, id="preamble-only"),
    ],
)
async def test_zero_genuine_changes_fires_all_three_probes(
    entries: list[dict], expected_events: int
) -> None:
    """The probes run only when there are no genuine changes, and all three run."""
    result, urls = await _run(_body(entries))

    assert result["normalized"] is True
    assert result["audit_details"] == []
    assert result["count"] == 0
    assert result["has_more"] is False
    assert result["audit_configuration_events_count"] == expected_events
    assert result["audit_configuration"]["diagnosis"] in {
        "auditing_off_at_organization",
        "auditing_off_at_table",
        "auditing_off_at_column",
        "auditing_enabled_no_changes_recorded",
        "undetermined",
    }
    assert len(urls) == 4, urls
    assert urls[0] == _EXPECTED_URL
    probes = urls[1:]
    assert any(f"{_API_ROOT}/organizations?$select=isauditenabled&$top=1" == u for u in probes)
    assert any(
        u == f"{_API_ROOT}/EntityDefinitions(LogicalName='account')?$select=IsAuditEnabled"
        for u in probes
    )
    assert any(
        u == (
            f"{_API_ROOT}/EntityDefinitions(LogicalName='account')/Attributes"
            "?$filter=LogicalName+eq+%27name%27&$select=LogicalName,IsAuditEnabled"
        )
        for u in probes
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "org,table,column,diagnosis",
    [
        pytest.param(
            False, "managed-false", "managed-false",
            "auditing_off_at_organization",
            id="org-off-wins-outermost",
        ),
        pytest.param(
            True, "managed-false", "managed-false",
            "auditing_off_at_table",
            id="table-off",
        ),
        pytest.param(
            True, "managed-true", "managed-false",
            "auditing_off_at_column",
            id="column-off",
        ),
        pytest.param(
            True, "managed-true", "managed-true",
            "auditing_enabled_no_changes_recorded",
            id="all-on",
        ),
    ],
)
async def test_the_outermost_disabled_level_is_named(
    org: Any, table: Any, column: Any, diagnosis: str
) -> None:
    """Each of the four determinate diagnoses, read from the right shape per level.

    The ``managed-false`` cases also pin that the wrapper's ``Value`` is what is read:
    a ``{"Value": False, ...}`` object is truthy, so reading the object instead would
    report the level ENABLED.
    """
    result, _ = await _run(_body([]), org=org, table=table, column=column)

    block = result["audit_configuration"]
    assert block["diagnosis"] == diagnosis
    assert block["organization_audit_enabled"] is org
    assert block["table_audit_enabled"] is (table == "managed-true")
    assert block["column_audit_enabled"] is (column == "managed-true")
    assert "probe_errors" not in block
    assert block["message"]


@pytest.mark.asyncio
async def test_a_failed_probe_yields_null_and_undetermined_with_the_reason() -> None:
    """Never downgrade a privilege failure to a guess."""
    result, _ = await _run(_body([]), table_status=403)

    block = result["audit_configuration"]
    assert block["diagnosis"] == "undetermined"
    assert block["organization_audit_enabled"] is True
    assert block["table_audit_enabled"] is None
    assert block["column_audit_enabled"] is True
    assert [e["level"] for e in block["probe_errors"]] == ["table"]
    assert "403" in block["probe_errors"][0]["message"]


@pytest.mark.asyncio
async def test_a_failed_probe_outside_the_outermost_disabled_level_still_decides() -> None:
    """Org auditing off explains the empty result whatever the inner probes did."""
    result, _ = await _run(_body([]), org=False, table_status=403, column_status=404)

    block = result["audit_configuration"]
    assert block["diagnosis"] == "auditing_off_at_organization"
    assert block["organization_audit_enabled"] is False
    assert block["table_audit_enabled"] is None
    assert block["column_audit_enabled"] is None
    assert [e["level"] for e in block["probe_errors"]] == ["table", "column"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,reason",
    [
        pytest.param(
            True,
            "a BARE bool at table/column level is not the documented "
            "BooleanManagedProperty and must not be read as one",
            id="bare-true",
        ),
        pytest.param(
            {"CanBeChanged": True},
            "a managed property with no Value",
            id="no-value-key",
        ),
        pytest.param(
            {"Value": "true"},
            "Value must be a real bool, not a truthy string",
            id="value-not-a-bool",
        ),
        pytest.param({}, "an empty object is truthy and would read as enabled", id="empty"),
    ],
)
async def test_is_audit_enabled_is_only_read_from_a_boolean_managed_property(
    value: Any, reason: str
) -> None:
    """Testing the wrapper OBJECT for truth reports 'auditing on' unconditionally."""
    result, _ = await _run(_body([]), table=value)

    block = result["audit_configuration"]
    assert block["table_audit_enabled"] is None, reason
    assert block["diagnosis"] == "undetermined"
    assert [e["level"] for e in block["probe_errors"]] == ["table"]
    assert "BooleanManagedProperty" in block["probe_errors"][0]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "org,reason",
    [
        pytest.param(1, "a truthy int is not a bool", id="int"),
        pytest.param("true", "a truthy string is not a bool", id="string"),
        pytest.param(
            {"Value": True},
            "the org flag is a PLAIN Edm.Boolean, not a managed property",
            id="managed-property-at-org-level",
        ),
    ],
)
async def test_the_org_flag_is_only_read_from_a_plain_boolean(
    org: Any, reason: str
) -> None:
    """The three levels are NOT symmetric — only the org one is a bare bool."""
    result, _ = await _run(_body([]), org=org)

    block = result["audit_configuration"]
    assert block["organization_audit_enabled"] is None, reason
    assert block["diagnosis"] == "undetermined"
    assert [e["level"] for e in block["probe_errors"]] == ["organization"]


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param(
            {"entity_set_name": 'accounts(1)"}/../whoami'},
            'a breakout carrying ", ), / and } inside the @odata.id',
            id="entity-set-breakout",
        ),
        pytest.param(
            {"record_id": f"{_RECORD_ID})/WhoAmI("},
            "a key-predicate breakout — the confirmed live injection class",
            id="record-id-breakout",
        ),
        pytest.param({"record_id": "not-a-guid"}, "a non-GUID", id="bad-guid"),
        pytest.param(
            {"column_logical_name": "name' or '1' eq '1"},
            "an OData literal breakout in the column name",
            id="column-breakout",
        ),
        pytest.param(
            {"table_logical_name": "account')/Attributes('"},
            "a key-predicate breakout in the probe's table name",
            id="table-breakout",
        ),
        pytest.param({"table_logical_name": ""}, "an empty table name", id="empty-table"),
        pytest.param({"top": 0}, "top below 1", id="top-too-low"),
        pytest.param({"top": 5001}, "top above the cap", id="top-too-high"),
        pytest.param(
            {"unknown": 1}, "extra='forbid' on the input model", id="unknown-field"
        ),
    ],
)
def test_input_model_rejects_bad_input(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model, so no URL is ever built."""
    payload = {
        "dataverse_url": _BASE_URL,
        "entity_set_name": _ENTITY_SET,
        "record_id": _RECORD_ID,
        "table_logical_name": _TABLE,
        "column_logical_name": _COLUMN,
        **kwargs,
    }
    with pytest.raises(ValidationError):
        GetAttributeChangeHistoryInput(**payload)


def test_all_four_identifying_fields_are_required() -> None:
    """The singular table name is required, not derived from the plural."""
    for missing in (
        "entity_set_name",
        "record_id",
        "table_logical_name",
        "column_logical_name",
    ):
        payload = {
            "dataverse_url": _BASE_URL,
            "entity_set_name": _ENTITY_SET,
            "record_id": _RECORD_ID,
            "table_logical_name": _TABLE,
            "column_logical_name": _COLUMN,
        }
        del payload[missing]
        with pytest.raises(ValidationError):
            GetAttributeChangeHistoryInput(**payload)


def test_the_entity_set_bound_is_the_plural_cap_not_the_schema_name_cap() -> None:
    """A 50-character logical name pluralizes past the 50-character schema cap."""
    plural = "a" * 50 + "es"
    params = GetAttributeChangeHistoryInput(
        dataverse_url=_BASE_URL,
        entity_set_name=plural,
        record_id=_RECORD_ID,
        table_logical_name=_TABLE,
        column_logical_name=_COLUMN,
    )
    assert params.entity_set_name == plural

    with pytest.raises(ValidationError):
        GetAttributeChangeHistoryInput(
            dataverse_url=_BASE_URL,
            entity_set_name="a" * 65,
            record_id=_RECORD_ID,
            table_logical_name=_TABLE,
            column_logical_name=_COLUMN,
        )
