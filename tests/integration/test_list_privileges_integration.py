"""Live integration coverage for dataverse_list_privileges.

Three live-proven facts drive this suite, and each gets a test that would catch
the platform changing under it:

* ``@odata.count`` **caps at 5,000 and lies** on the ``privileges`` collection —
  ``?$count=true`` and ``/privileges/$count`` both answer 5,000 where the true
  catalogue is ~7,346. The tool takes its ``total_count`` from
  ``$apply=aggregate($count as c)``, which bypasses the cap. The raw comparison
  is made here so the discrepancy is RECORDED, not assumed.
* ``accessright`` has **no option set anywhere** (the PicklistAttributeMetadata
  cast and ``GlobalOptionSetDefinitions(Name='accessrights')`` both 404), so the
  decode map is hand-rolled. This suite prints every distinct ``accessright``
  the org carries so a value outside the map shows up as a recorded fact rather
  than a wrong label.
* table scoping goes through the private, undocumented
  ``privilegeobjecttypecodesset`` join table. ``endswith(name,…)`` is the wrong
  route and this suite shows why: it is run side by side with the join for a
  name that spans several tables.
* an **unknown table name is an HTTP 400** ``[0x80041102]``, **not** an empty
  list. The two are opposite answers — an empty list means a valid table with no
  privileges — so each gets its own test and neither skips on its own subject.

``$skip`` is unsupported on this collection (HTTP 400 "Skip Clause is not
supported in CRM") and is asserted absent from every URL the tool builds.

No GUID is hardcoded and nothing environment-specific is either: the only
literals are the standard ``account`` and ``privilege`` tables, which every org
has, and every test that depends on their CONTENTS skips itself if this org
differs. Counts are printed rather than asserted exactly, so an org of any scale
reports its numbers instead of failing. A skip is only ever for a genuinely
org-dependent precondition, never for the thing a test is asserting.

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
from collections import Counter
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from dataverse_mcp.client import _DATAVERSE_API_VERSION, AppContext, resolve_base_url
from dataverse_mcp.models import ListPrivilegesInput
from dataverse_mcp.tools.security import _ACCESS_RIGHT_NAMES, dataverse_list_privileges

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

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
    """Stop httpx's INFO logger printing the org host and GUIDs unredacted.

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
    """Strip the org host and every GUID before anything is printed.

    Privilege NAMES are schema, not data, so they are printed as-is.
    """
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


async def _call(**kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_list_privileges(
                ListPrivilegesInput(dataverse_url=_url(), **kwargs), ctx
            )
        )


async def test_catalogue_normalizes_and_trims_while_reporting_the_true_total() -> None:
    """The default call: a small page, an honest total, one decoded shape."""
    result = await _call()
    _dump("privilege catalogue (default top)", {**result, "privileges": result.get("privileges", [])[:3]})

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["normalized"] is True
    assert result["source"] == "privileges"
    assert result["count"] == len(result["privileges"])
    assert result["count"] <= 50, "the default top of 50 was not applied"

    total = result.get("total_count")
    if total is None:
        pytest.skip(
            "no trustworthy total could be aggregated on this org — the tool "
            "correctly OMITTED total_count rather than reporting the capped "
            f"@odata.count. message: {result.get('message')}"
        )
    print(f"RECORDED total_count (from $apply=aggregate): {total}")
    assert total >= result["count"]
    assert result["has_more"] is (total > result["count"])
    assert total > 5000, (
        f"total_count is {total}: at or below the 5,000 @odata.count cap. Either "
        "this org genuinely has fewer privileges (verified live at 7,346 on the "
        "reference org) or the aggregation has started to cap too — check before "
        "trusting the number."
    )

    entry = result["privileges"][0]
    print(f"RECORDED first entry: {_redact(json.dumps(entry, sort_keys=True))}")
    assert set(entry) <= {
        "name",
        "privilege_id",
        "access_right",
        "access_right_name",
        "depths",
        "can_be_entity_reference",
        "can_be_parent_entity_reference",
    }, "an unexpected key appeared in the projection"
    assert "privilegetype" not in entry, "privilegetype does not exist on this entity"


async def test_odata_count_really_does_cap_and_the_aggregation_does_not() -> None:
    """RECORD the discrepancy the tool exists to route around."""
    counted = await _get("/privileges?$count=true&$top=1")
    aggregated = await _get("/privileges?$apply=aggregate($count as c)")
    print(f"$count=true  -> HTTP {counted.status_code}")
    print(f"$apply       -> HTTP {aggregated.status_code}")
    if counted.status_code != 200 or aggregated.status_code != 200:
        pytest.skip(
            "one of the two count routes was unavailable: "
            f"{_redact(counted.text[:300])} / {_redact(aggregated.text[:300])}"
        )

    odata_count = counted.json().get("@odata.count")
    rows = aggregated.json().get("value") or [{}]
    true_count = rows[0].get("c")
    print(f"RECORDED @odata.count: {odata_count}")
    print(f"RECORDED $apply aggregate($count as c): {true_count}")

    assert isinstance(true_count, int)
    if odata_count == true_count:
        print(
            "RECORDED: the two agree on this org, so the 5,000 cap did not bite "
            "here. Keep the aggregation anyway — it was verified capped live "
            "(5,000 reported against a true 7,346)."
        )
    else:
        assert odata_count < true_count, (
            "@odata.count exceeded the aggregation — the assumption that it "
            "UNDER-reports no longer holds; re-verify before trusting either."
        )
        print(
            f"RECORDED: @odata.count UNDER-reports by {true_count - odata_count} "
            "rows. This is the trap the tool routes around."
        )


async def test_skip_is_rejected_so_the_tool_must_never_build_it() -> None:
    """RECORD that $skip is unsupported — the reason paging is top-only here."""
    response = await _get("/privileges?$select=name&$top=1&$skip=1")
    print(f"RECORDED /privileges?$skip=1 -> HTTP {response.status_code}")
    print(f"RECORDED body: {_redact(response.text[:400])}")
    if response.status_code == 200:
        print(
            "RECORDED: $skip was ACCEPTED on this org, contradicting the live "
            "HTTP 400 'Skip Clause is not supported in CRM'. The tool still does "
            "not build it; record this before anyone adds it."
        )
    else:
        assert response.status_code == 400


async def test_every_access_right_on_this_org_is_covered_by_the_decode_map() -> None:
    """The map is hand-rolled: an uncovered value must FAIL, not be labelled wrong.

    Read by GROUPBY, not by a ``$top=5000`` page. A page caps at 5,000 of the
    ~7,346 rows, so an unmapped value living only in the ~2,346-row tail would be
    invisible — the sweep would pass while missing exactly what it exists to
    catch. ``$apply=groupby(...)`` covers every row in one call and bypasses the
    cap, the same reason ``total_count`` is aggregated.

    Reference distribution (live, 2026-08-06): 0:48, 1:1026, 2:997, 4:953,
    16:962, 32:996, 65536:989, 262144:676, 524288:699 — 7,346 rows over exactly
    the 9 map keys, with no unmapped value anywhere.
    """
    response = await _get(
        "/privileges?$apply=groupby((accessright),aggregate($count as c))"
    )
    assert response.status_code == 200, _redact(response.text[:500])
    values = Counter()
    for row in response.json().get("value", []):
        if isinstance(row, dict):
            values[row.get("accessright")] = row.get("c")

    print(
        "RECORDED accessright distribution (groupby, WHOLE collection): "
        f"{dict(sorted(values.items(), key=lambda kv: str(kv[0])))}"
    )
    counts = [c for c in values.values() if isinstance(c, int)]
    print(f"RECORDED total rows across all groups: {sum(counts)}")
    print(f"COMPARISON — decode map: {dict(_ACCESS_RIGHT_NAMES)}")

    # SUBSET, not equality: an org need not use every access right, but a value
    # the map does not know is a platform change that must fail loudly here
    # rather than surface as an unlabelled entry nobody reads.
    unmapped = sorted(
        (v for v in values if v not in _ACCESS_RIGHT_NAMES), key=str
    )
    assert not unmapped, (
        f"accessright value(s) {unmapped} are NOT in the decode map. The tool "
        "reports them raw with no access_right_name, which is the correct "
        "runtime behaviour — but the map is now incomplete. Extend it from live "
        "evidence only, NEVER by shifting bits (8 and 16-32768 are genuinely "
        f"unused). Distribution: {dict(values)}"
    )


async def test_access_right_filter_returns_only_that_right() -> None:
    """The Literal is translated to its integer; nothing caller-supplied is echoed."""
    result = await _call(access_right="ReadAccess", top=25)
    assert not result.get("error"), result.get("message")
    assert result["normalized"] is True
    assert result["access_right"] == "ReadAccess"
    print(
        f"RECORDED ReadAccess: count={result['count']}, "
        f"total_count={result.get('total_count')}"
    )
    for entry in result["privileges"]:
        assert entry["access_right"] == 1
        assert entry["access_right_name"] == "ReadAccess"


async def test_table_scoping_uses_the_join_table_and_beats_name_matching() -> None:
    """The join route is exact; endswith(name,…) spans unrelated tables.

    ``account`` is the one literal here — a standard table present on every org,
    carrying 8 privileges on the reference org — and the test skips itself if this
    org exposes none. The two routes are printed side by side so the difference is
    on the record.
    """
    result = await _call(table_logical_name="account", top=50)
    _dump("privileges for table 'account'", result)
    if result.get("error"):
        pytest.skip(
            "the privilegeobjecttypecodesset join route is unavailable on this "
            f"org: {_redact(str(result['message']))}. That entity set is private "
            "and undocumented, so this is a real possibility — the tool reports "
            "it by name rather than falling back to name matching, which is the "
            "behaviour under test."
        )

    assert result["normalized"] is True
    assert result["source"] == "privilegeobjecttypecodesset"
    assert result["count"] == len(result["privileges"])
    print(
        f"RECORDED account privileges: {sorted(p['name'] for p in result['privileges'])}"
    )
    if result["count"] == 0:
        pytest.skip("this org exposes no privileges for the account table")

    # The name-matching route, side by side. 'Role' is the live counter-example:
    # it spans role, connectionrole, relationshiprole and mspp_webrole.
    by_name = await _get("/privileges?$select=name&$filter=endswith(name,'Role')&$top=100")
    if by_name.status_code == 200:
        names = sorted(row.get("name", "") for row in by_name.json().get("value", []))
        print(f"COMPARISON — endswith(name,'Role') returned {len(names)}: {names}")
        print(
            "RECORDED: those names span SEVERAL tables (role, connectionrole, "
            "relationshiprole, mspp_webrole on the reference org), which is why "
            "table scoping never matches on the name."
        )

    scoped = await _call(table_logical_name="account", name_startswith="prvRead")
    print(
        f"RECORDED account + prvRead: count={scoped['count']}, "
        f"total_count={scoped.get('total_count')}"
    )
    assert scoped["total_count"] <= result["total_count"]
    for entry in scoped["privileges"]:
        assert entry["name"].lower().startswith("prvread")


async def test_unknown_table_is_an_error_naming_the_table_not_an_empty_list() -> None:
    """objecttypecode is validated: a bad logical name 400s, it never answers empty.

    Live-confirmed on ``zzz_no_such_table``, ``acount``, ``accounts`` and
    ``new_notatable`` — all four returned
    ``[0x80041102] The entity with a name = '…' with namemapping = 'Logical' was
    not found in the MetadataCache``. There is deliberately NO skip here: the
    error envelope IS the assertion's subject, so skipping on it would make this
    test incapable of failing.
    """
    bad_name = "no_such_table_xyz"
    result = await _call(table_logical_name=bad_name)
    _dump("privileges for a nonexistent table", result)

    assert result.get("error") is True, (
        "an unknown logical name answered without an error. It must NOT come "
        "back as an empty list — an empty list means a valid table with no "
        f"privileges. Got: {_redact(json.dumps(result, sort_keys=True))}"
    )
    message = str(result["message"])
    assert bad_name in message, "the message must name the offending table"
    assert message.startswith(f"'{bad_name}' is not a table logical name"), (
        f"the bad table name must LEAD the message; got: {_redact(message)}"
    )
    assert "0x80041102" in message, (
        "the platform code that identifies a bad entity name is missing — the "
        "failure may now be something else entirely. Re-verify before trusting "
        f"the diagnosis. Got: {_redact(message)}"
    )
    assert "privileges" not in result, "no list is fabricated for a failed lookup"
    print(f"RECORDED unknown-table error: {_redact(message)}")


async def test_a_valid_table_with_no_privileges_answers_empty_not_an_error() -> None:
    """The other half of the pair: ``privilege`` itself maps to 0 privileges."""
    result = await _call(table_logical_name="privilege")
    _dump("privileges for table 'privilege'", result)
    if result.get("error"):
        pytest.skip(
            "the privilegeobjecttypecodesset join route is unavailable on this "
            f"org: {_redact(str(result['message']))}. That is a genuinely "
            "org-dependent precondition — the entity set is private and "
            "undocumented — and is NOT the behaviour under test here."
        )

    assert result["normalized"] is True
    print(f"RECORDED 'privilege' table privileges: count={result['count']}")
    if result["count"]:
        pytest.skip(
            f"this org maps {result['count']} privilege(s) to the 'privilege' "
            "table, so it is not an empty-set example here"
        )
    assert result["privileges"] == []
    assert "EXISTS and has no privileges" in result["message"]
    assert "spelling" not in result["message"].lower(), (
        "the name was CORRECT — spelling advice belongs on the 400 path"
    )


async def test_table_logical_name_casing_does_not_change_the_answer() -> None:
    """'Account' and 'ACCOUNT' both 400 on the wire; the model lowercases them."""
    raw = await _get(
        "/privilegeobjecttypecodesset?$select=objecttypecode"
        "&$filter=objecttypecode eq 'Account'&$top=1"
    )
    print(f"RECORDED raw objecttypecode eq 'Account' -> HTTP {raw.status_code}")
    if raw.status_code != 400:
        print(
            "RECORDED: the raw wire call ACCEPTED mixed case, contradicting the "
            "live HTTP 400 [0x80041102]. The tool still lowercases, which is "
            "harmless either way — record this before anyone removes it."
        )

    lower = await _call(table_logical_name="account", top=50)
    upper = await _call(table_logical_name="ACCOUNT", top=50)
    if lower.get("error"):
        pytest.skip(
            "the privilegeobjecttypecodesset join route is unavailable on this "
            f"org: {_redact(str(lower['message']))}"
        )
    assert upper == lower, (
        "casing changed the answer — table_logical_name is meant to be "
        "normalized to lowercase at the model boundary"
    )
    assert upper["table_logical_name"] == "account"
    print("RECORDED: 'ACCOUNT' and 'account' return byte-identical results")
