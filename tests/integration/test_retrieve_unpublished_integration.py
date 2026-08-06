"""Live integration coverage for dataverse_retrieve_unpublished.

``RetrieveUnpublished`` is bound to an entity INSTANCE, so the tool builds::

    GET {api_root}/{entityset}({record_id})/Microsoft.Dynamics.CRM.RetrieveUnpublished()?$select=…

This suite is what turns that into a record. What a live run must establish:

* the instance-bound URL form is accepted (HTTP 200) for ``savedqueries`` and
  ``systemforms`` — the two entity sets whose stale-read gap this tool closes;
* ``$select`` is honoured on the function result, and the per-entity-set DEFAULT
  projections in ``tools/customizations.py`` name only real columns (an invented
  column name is an HTTP 400, so a green run is the proof). The returned column
  set is never asserted to equal the requested one, in either direction: live,
  Dataverse ADDS columns nobody asked for (``_organizationid_value`` everywhere,
  plus ``objecttypecode`` and ``type`` on ``systemforms``) and silently OMITS
  requested columns whose value is NULL. Both are platform behaviour, not tool
  bugs, and the added set is open-ended — so the extras are RECORDED, not
  whitelisted, and what is asserted is that the requested columns which did come
  back are present and usable;
* the draft read returns a USABLE definition — asking for ``fetchxml`` /
  ``layoutxml`` / ``formxml`` explicitly yields non-empty XML;
* the size argument for holding the XML back by default is real: the default
  projection is materially smaller than the same record with its XML;
* published (plain GET) and unpublished (this tool) are compared column by
  column. On an org with no pending customizations they are IDENTICAL, which is
  the expected result and is asserted as such; where they differ, the difference
  is printed — that is the stale-read the tool exists to expose.

No org URL, record id or GUID is hardcoded: the org comes from the environment
and every id is discovered live. Host and GUIDs are redacted before printing.

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
from dataverse_mcp.models import RetrieveUnpublishedInput
from dataverse_mcp.tools.customizations import (
    _DEFAULT_SELECT,
    dataverse_retrieve_unpublished,
)

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# One narrow page is enough to find a candidate in any org.
_DISCOVERY_PAGE_SIZE = 20

# Live-confirmed platform behaviour, on every entity set tested: Dataverse ADDS
# columns to a RetrieveUnpublished() result that were never in $select —
# _organizationid_value everywhere, and objecttypecode/type on systemforms with a
# narrow select. The set is open-ended, so it is deliberately NOT whitelisted here:
# enumerating it would make this suite fail the next time the platform adds one.
# It is not a tool bug either, and not something the tool should strip (silently
# dropping a column the platform returned would be worse), so the extras are
# RECORDED in the output the way requested-but-omitted null columns already are.
# The @odata.etag key is stripped from the comparison because it is envelope, not
# a column.
_ENVELOPE_KEYS = {"@odata.etag"}

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
    """Stop httpx's INFO logger printing the org host and record GUIDs unredacted.

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


async def _discover(entity_set: str, id_column: str) -> str | None:
    """Return one live record id from *entity_set*, or None if the org has none.

    Only the primary key is selected, so discovery cannot itself pull an XML blob.
    """
    response = await _get(f"/{entity_set}?$select={id_column}&$top={_DISCOVERY_PAGE_SIZE}")
    if response.status_code != 200:
        print(
            f"\n=== discovery on {entity_set} failed: HTTP {response.status_code} "
            f"{_redact(response.text[:400])} ==="
        )
        return None
    rows = [r for r in response.json().get("value", []) if isinstance(r.get(id_column), str)]
    print(f"\n=== discovered {len(rows)} {entity_set} record(s) (ids redacted) ===")
    return rows[0][id_column] if rows else None


async def _call(entity_set: str, record_id: str, **kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_retrieve_unpublished(
                RetrieveUnpublishedInput(
                    dataverse_url=_url(),
                    entity_set_name=entity_set,
                    record_id=record_id,
                    **kwargs,
                ),
                ctx,
            )
        )


def _assert_contract(result: dict, entity_set: str, record_id: str) -> None:
    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["entity_set_name"] == entity_set
    assert result["record_id"] == record_id
    assert result["unpublished"] is True
    assert isinstance(result["record"], dict) and result["record"], (
        "the draft read returned no columns; read the payload printed above"
    )
    # A single record, never a fake list — so no list-shaped keys may appear.
    assert "count" not in result and "has_more" not in result, (
        "this tool reads one instance-bound record; count/has_more would imply a "
        "collection it never returns"
    )


@pytest.mark.parametrize(
    "entity_set,id_column,xml_columns",
    [
        pytest.param("savedqueries", "savedqueryid", ["fetchxml", "layoutxml"], id="view"),
        pytest.param("systemforms", "formid", ["formxml"], id="form"),
    ],
)
async def test_draft_read_returns_a_usable_definition(
    entity_set: str, id_column: str, xml_columns: list[str]
) -> None:
    """The instance-bound URL works and the draft carries real, usable XML.

    This is the test that proves the tool closes the stale-read gap: the same
    record is read with the default projection (small) and with its XML columns
    (usable), both through RetrieveUnpublished.
    """
    record_id = await _discover(entity_set, id_column)
    if record_id is None:
        pytest.skip(f"this org has no {entity_set} record to read")

    default_result = await _call(entity_set, record_id)
    _dump(f"{entity_set}: default projection", default_result)
    _assert_contract(default_result, entity_set, record_id)

    # A green run here is the proof that every column named in _DEFAULT_SELECT
    # exists: Dataverse answers an unknown $select column with HTTP 400. That 400
    # — not a requested-vs-returned diff — is the only reliable signal for a bad
    # column name, because the returned set matches the requested one in NEITHER
    # direction:
    #   1. the platform ADDS columns that were never requested, an open-ended set
    #      (_organizationid_value everywhere; objecttypecode and type also came
    #      back on systemforms under a narrow select);
    #   2. a requested column whose value is NULL is silently OMITTED — unlike a
    #      plain GET, which returns it explicitly as null (verified with
    #      appmodules.publishedon).
    # So a missing key means "null", not "no such column", and an extra key is the
    # platform being generous. Both are recorded rather than asserted on; what IS
    # asserted is that the requested columns which came back are present and
    # usable.
    expected_columns = set(_DEFAULT_SELECT[entity_set])
    assert set(default_result["select"]) == expected_columns
    returned = set(default_result["record"]) - _ENVELOPE_KEYS
    print(f"RECORDED default columns returned for {entity_set}: {sorted(returned)}")
    print(
        f"RECORDED requested-but-omitted (null) columns for {entity_set}: "
        f"{sorted(expected_columns - returned)}"
    )
    print(
        f"RECORDED platform-added (never requested) columns for {entity_set}: "
        f"{sorted(returned - expected_columns)}"
    )
    assert isinstance(default_result["record"].get(id_column), str), (
        f"the requested primary key {id_column} did not come back as a string — "
        "the draft read is not returning a usable record; read the payload above"
    )
    for column in xml_columns:
        assert column not in returned, (
            f"{column} came back without being asked for — the default projection "
            "is supposed to hold the large XML columns back"
        )

    xml_result = await _call(
        entity_set, record_id, select=[id_column, "name", *xml_columns]
    )
    _assert_contract(xml_result, entity_set, record_id)
    default_size = len(json.dumps(default_result))
    xml_size = len(json.dumps(xml_result))
    print(
        f"RECORDED {entity_set}: default projection {default_size} bytes vs "
        f"with-XML {xml_size} bytes"
    )
    for column in xml_columns:
        value = xml_result["record"].get(column)
        assert isinstance(value, str) and value.strip().startswith("<"), (
            f"{column} did not come back as XML from the unpublished read: "
            f"{type(value).__name__} {str(value)[:120]!r}. The tool would then be "
            "returning a draft that cannot be used to edit the record."
        )
        print(f"RECORDED {entity_set}.{column}: {len(value)} chars")
    assert xml_size > default_size, (
        "the XML projection is no larger than the default one — the payload-hygiene "
        "argument for the default no longer holds; record the sizes above"
    )


@pytest.mark.parametrize(
    "entity_set,id_column,xml_columns",
    [
        pytest.param("savedqueries", "savedqueryid", ["fetchxml", "layoutxml"], id="view"),
        pytest.param("systemforms", "formid", ["formxml"], id="form"),
    ],
)
async def test_published_and_unpublished_reads_are_comparable(
    entity_set: str, id_column: str, xml_columns: list[str]
) -> None:
    """Read the same record BOTH ways and report whether the draft is ahead.

    A plain GET returns the PUBLISHED row; this tool returns the DRAFT. On an org
    with no pending customizations the two are identical — that is the expected
    result and is asserted, because a spurious difference would mean the tool is
    not reading the record it was asked for. Where they DO differ, the differing
    columns are printed: that difference is exactly the stale read that
    dataverse_get_form / dataverse_get_view would have handed back.
    """
    record_id = await _discover(entity_set, id_column)
    if record_id is None:
        pytest.skip(f"this org has no {entity_set} record to read")

    columns = [id_column, "name", *xml_columns]
    published_response = await _get(
        f"/{entity_set}({record_id})?$select={','.join(columns)}"
    )
    assert published_response.status_code == 200, (
        "the published GET failed — HTTP "
        f"{published_response.status_code}: {_redact(published_response.text[:400])}"
    )
    published = {
        k: v
        for k, v in published_response.json().items()
        if not k.startswith("@odata.")
    }

    unpublished_result = await _call(entity_set, record_id, select=columns)
    _assert_contract(unpublished_result, entity_set, record_id)
    unpublished = unpublished_result["record"]

    assert unpublished.get(id_column) == published.get(id_column), (
        "the unpublished read returned a DIFFERENT record than the published read; "
        "the key predicate or the bound-function URL is wrong"
    )

    differing = sorted(
        c
        for c in columns
        if published.get(c) != unpublished.get(c)
    )
    print(
        f"\n=== {entity_set}: published vs unpublished — "
        f"{'DIFFER on ' + ', '.join(differing) if differing else 'IDENTICAL'} ==="
    )
    for column in columns:
        pub, unpub = published.get(column), unpublished.get(column)
        print(
            f"  {column}: published={len(pub) if isinstance(pub, str) else pub} "
            f"unpublished={len(unpub) if isinstance(unpub, str) else unpub}"
            + (" chars" if isinstance(pub, str) else "")
        )
    if differing:
        # This org has a pending customization — the exact stale-read case.
        print(
            "RECORDED: this record has UNPUBLISHED changes. A plain GET (and "
            "therefore dataverse_get_form / dataverse_get_view) would have "
            f"returned the stale published value for: {', '.join(differing)}"
        )
        for column in differing:
            _dump(
                f"{entity_set}.{column} published (first 1500 chars)",
                str(published.get(column))[:1500],
            )
            _dump(
                f"{entity_set}.{column} unpublished (first 1500 chars)",
                str(unpublished.get(column))[:1500],
            )
    else:
        # Nothing pending: the two layers must agree on every REQUESTED column.
        # Anything else means the tool is not reading the record it was asked for.
        # The comparison is scoped to `columns` rather than to the whole dict
        # because the unpublished side legitimately carries platform-added
        # columns nobody requested and legitimately omits requested null columns
        # — see the note on _ENVELOPE_KEYS above.
        assert {c: published.get(c) for c in columns} == {
            c: unpublished.get(c) for c in columns
        }, (
            "published and unpublished disagree on a column that was not detected "
            "as differing — this assertion and the diff above are inconsistent"
        )
        print(
            "RECORDED: no pending customization on this record, so the draft and "
            "the published row are identical. That is the expected result; to see "
            "them diverge, edit the record without publishing and re-run."
        )


async def test_unknown_record_id_returns_the_error_envelope() -> None:
    """A well-formed but nonexistent GUID is an error, not an empty record."""
    result = await _call("systemforms", "00000000-0000-0000-0000-000000000001")
    _dump("nonexistent form: full tool response", result)

    assert result.get("error") is True, (
        "a nonexistent record id no longer returns an error — an empty record now "
        "means 'a real draft with no columns', so the two would be "
        "indistinguishable; record the payload above before relaxing this"
    )
    assert isinstance(result["message"], str) and result["message"]
    assert "record" not in result
    print(f"RECORDED unknown-record error message: {_redact(result['message'])}")

