"""Live integration coverage for dataverse_get_team_privileges.

``RetrieveTeamPrivileges`` is an ENTITY-BOUND GET on ``teams`` that takes no
function parameters — the team id is the OData key predicate. Microsoft Learn
documents the call and the return TYPE (``RetrieveTeamPrivilegesResponse``) but
**not its inner properties**, so this suite RECORDS the shape rather than
asserting one: every test prints the raw payload, its size in bytes, the
top-level property names, the per-entry keys and the distinct ``Depth`` values.
The only hard assertions are the ones the tool's contract depends on — that a
collection can be located at all, and that counts and trimming are
self-consistent.

Settled by a live run (2026-07-30):

* the collection arrives under **``RolePrivileges``**, not ``TeamPrivileges`` —
  the same key the sibling ``RetrieveUserPrivileges`` uses. ``normalized`` was
  true on every call and the by-shape fallback never fired;
* a well-formed but nonexistent team id is an **HTTP 404** ``[0x80040217]``, as
  the role function is — not an empty collection.

Still open, and **not answerable on an org whose teams have no directly-assigned
roles** — every team on the org tested returned ``"RolePrivileges": []``:

* whether entries carry the same six keys ``RetrieveRolePrivilegesRole`` returns,
  and in particular whether ``PrivilegeName`` is present — if it is not, the tool
  hands an LLM bare GUIDs and a name-resolution step becomes necessary;
* whether ``Depth`` is the enum MEMBER NAME string (as on the role function) or a
  numeric ``PrivilegeDepth`` code. **Do not map a numeric code to a label** — a
  wrong access-level label is worse than a raw one; record the finding first;
* the magnitude, which is why trimming exists: the role function was measured at
  4,132 privileges in ~1.04 MB.

**An all-empty result is an ORG-DATA limitation, not a tool failure.** A team with
no directly-assigned security roles legitimately has no privileges, so the tests
that need a populated list SKIP with that reason instead of failing. Assign a role
to a team and re-run to close the open questions above.

No team id is hardcoded: every id is discovered live. Sizes and counts are printed
rather than asserted exactly, so an org of any scale reports its numbers instead
of failing.

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
from dataverse_mcp.models import GetTeamPrivilegesInput
from dataverse_mcp.tools.security import dataverse_get_team_privileges

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Teams are fetched in one narrow page; 400 is a generous ceiling for an org.
_TEAM_PAGE_SIZE = 400

_RAW_BODY_PREVIEW = 4000

# The six per-entry keys RetrieveRolePrivilegesRole returns live. Used only as a
# COMPARISON baseline to print against — never asserted, because this function's
# entry shape is exactly what is unknown.
_ROLE_FUNCTION_ENTRY_KEYS = [
    "BusinessUnitId",
    "Depth",
    "PrivilegeId",
    "PrivilegeName",
    "RecordFilterId",
    "RecordFilterUniqueName",
]

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
    """Stop httpx's INFO logger printing the org host and team GUID unredacted.

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


async def _teams() -> list[dict]:
    """Discover teams live — no id is ever hardcoded.

    ``teams`` is a data-plane collection, so ``$select``/``$top`` behave normally
    here (unlike ``EntityDefinitions`` on the schema plane, which this org rejects
    any ``$filter`` on with HTTP 400 ``[0x80060888]``). One narrow page is enough:
    every org has at least the auto-created default business-unit team.
    """
    response = await _get(f"/teams?$select=name,teamid,teamtype&$top={_TEAM_PAGE_SIZE}")
    assert response.status_code == 200, (
        "team discovery failed — HTTP "
        f"{response.status_code}: {_redact(response.text[:500])}"
    )
    rows = [
        row
        for row in response.json().get("value", [])
        if isinstance(row.get("teamid"), str) and isinstance(row.get("name"), str)
    ]
    print(f"\n=== discovered {len(rows)} team(s) (names/ids redacted below) ===")
    return rows


async def _call(team_id: str, **kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_get_team_privileges(
                GetTeamPrivilegesInput(dataverse_url=_url(), team_id=team_id, **kwargs),
                ctx,
            )
        )


async def _first_team_with_privileges() -> tuple[dict, dict] | None:
    """Return the (team, tool result) pair with the LARGEST readable privilege list.

    A team with no directly-assigned security roles legitimately has no privileges,
    and an org where that is true of every team is common — it was true of all four
    teams on the org used to verify this tool. So "readable" here means normalized,
    NOT non-empty: the widest list found is returned even when it is empty, and
    callers that need entries check ``total_count`` and skip themselves. Up to 12
    teams are tried before giving up.
    """
    best: tuple[dict, dict] | None = None
    for team in (await _teams())[:12]:
        result = await _call(team["teamid"], top=5)
        if result.get("error") or not result.get("normalized"):
            continue
        if best is None or result["total_count"] > best[1]["total_count"]:
            best = (team, result)
    return best


async def test_raw_call_records_the_undocumented_response_shape_and_size() -> None:
    """Call the function with the exact URL the tool builds and RECORD the shape.

    This is the test that turns the guess into a record. It asserts almost
    nothing beyond "the call works and returns a collection"; everything else is
    printed so the tool's docstring, ``_TEAM_PRIVILEGES_KEYS`` and the README can
    be corrected from the output alone.
    """
    teams = await _teams()
    if not teams:
        pytest.skip("this org has no teams")

    base_url = resolve_base_url(_url())
    recorded: list[dict[str, Any]] = []

    for team in teams[:12]:
        # Exactly the URL src/dataverse_mcp/tools/security.py builds: an
        # entity-bound function with a BARE Guid key predicate and no parameters.
        url = (
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
            f"/teams({team['teamid']})"
            "/Microsoft.Dynamics.CRM.RetrieveTeamPrivileges()"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=_headers())

        print("\n=== RetrieveTeamPrivileges raw HTTP call ===")
        print(f"url (org host + guid redacted): {_redact(url)}")
        print(f"status: {response.status_code}")
        print(f"RESPONSE SIZE: {len(response.content)} bytes")
        if response.status_code != 200:
            print(f"body: {_redact(response.text[:1000])}")
            continue

        body = response.json()
        print(f"RAW BODY (first {_RAW_BODY_PREVIEW} chars):")
        print(_redact(response.text[:_RAW_BODY_PREVIEW]))

        top_level = sorted(k for k in body if not k.startswith("@odata."))
        print(f"RECORDED top_level_keys (envelope stripped): {top_level}")
        print(
            "RECORDED value_types: "
            + json.dumps({k: type(body[k]).__name__ for k in top_level}, sort_keys=True)
        )
        recorded.append({"top_level": top_level, "body": body})

        entries = next(
            (v for v in body.values() if isinstance(v, list) and v), None
        )
        if entries is None:
            print(
                "RECORDED: no non-empty list in the payload for this team — the "
                "team has no directly-assigned security roles. That is an ORG-DATA "
                "limitation, not a failure: the per-entry keys and the Depth "
                "representation stay unverified until some team on some org carries "
                "a role."
            )
            continue

        print(f"RECORDED entry count: {len(entries)}")
        print(f"RECORDED entry_keys: {sorted(entries[0])}")
        print(
            "COMPARISON — RetrieveRolePrivilegesRole entry keys: "
            f"{_ROLE_FUNCTION_ENTRY_KEYS}"
        )
        print(
            "RECORDED entry_value_types: "
            + json.dumps(
                {k: type(v).__name__ for k, v in entries[0].items()}, sort_keys=True
            )
        )
        print(f"RECORDED first entry: {_redact(json.dumps(entries[0], default=str))}")
        depths = {e.get("Depth") for e in entries if isinstance(e, dict)}
        print(f"RECORDED distinct Depth values: {sorted(map(str, depths))}")
        print(
            "RECORDED entries carrying a non-empty PrivilegeName: "
            + str(sum(1 for e in entries if e.get("PrivilegeName")))
            + f" of {len(entries)}"
        )
        break

    if not recorded:
        pytest.skip(
            "RetrieveTeamPrivileges returned no HTTP 200 for any of the first 12 "
            "teams — read the statuses printed above; the function may be "
            "unavailable or privilege-gated on this org"
        )

    assert any(r["top_level"] for r in recorded), (
        "every successful response was an empty envelope — nothing to record. "
        "Read the raw bodies printed above before trusting the tool."
    )


async def test_tool_normalizes_a_live_payload_and_reports_its_source() -> None:
    """The tool must locate the collection and stay self-consistent about counts.

    An EMPTY collection passes this test on purpose: ``total_count: 0`` with
    ``normalized: true`` is the correct answer for a team with no directly-assigned
    security roles, and the tool's contract is about locating the collection and
    counting it honestly, not about the org holding interesting data.
    """
    found = await _first_team_with_privileges()
    if found is None:
        pytest.skip(
            "no team in this org returned a readable privilege collection — read "
            "the raw-shape test's output; the property name may be one neither "
            "_TEAM_PRIVILEGES_KEYS nor the by-shape tiers recognize. Note this is "
            "NOT the all-empty case, which normalizes fine and does not skip"
        )

    team, _ = found
    result = await _call(team["teamid"])
    serialized = json.dumps(result)
    print(f"\n=== TOOL RESPONSE SIZE {len(serialized)} bytes ===")
    _dump("team privileges: full tool response", result)

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["team_id"] == team["teamid"]
    assert result["normalized"] is True
    assert isinstance(result["privileges"], list)
    assert result["count"] == len(result["privileges"])
    assert result["total_count"] >= result["count"]
    assert result["has_more"] is (result["total_count"] > result["count"])
    assert result["count"] <= 50, "the default top of 50 was not applied"

    print(
        f"RECORDED: privileges_source={result['privileges_source']!r}, "
        f"total_count={result['total_count']}, count={result['count']}, "
        f"depth_summary={result.get('depth_summary')}"
    )
    if result["privileges"]:
        print(f"RECORDED entry_keys: {sorted(result['privileges'][0])}")
        print(
            "RECORDED: entries carrying PrivilegeName: "
            + str(sum(1 for p in result["privileges"] if p.get("PrivilegeName")))
            + f" of {len(result['privileges'])}"
        )

    summary = result.get("depth_summary")
    if summary:
        assert sum(summary.values()) == result["total_count"], (
            "depth_summary must cover the full collection, not just the returned "
            "page"
        )
        print(f"RECORDED distinct Depth values: {sorted(summary)}")
        if any(key.isdigit() for key in summary):
            print(
                "RECORDED: Depth arrived as a NUMERIC PrivilegeDepth code, not the "
                "member-name string the role function returns. The tool reports it "
                "raw, which is correct — record this before anyone maps it."
            )


async def test_trimming_reports_the_true_magnitude() -> None:
    """top=1 against the broadest team proves total_count is not the page size."""
    found = await _first_team_with_privileges()
    if found is None:
        pytest.skip("no team in this org returned a readable privilege collection")

    team, sample = found
    if sample["total_count"] < 2:
        pytest.skip(
            f"the broadest team on this org carries only {sample['total_count']} "
            "privilege(s), so there is nothing to trim. This is an ORG-DATA "
            "limitation, not a tool failure: a team with no directly-assigned "
            "security roles legitimately has no privileges (every team on the org "
            "this tool was verified against returned an empty RolePrivileges list). "
            "Assign a role to a team and re-run to exercise trimming."
        )

    result = await _call(team["teamid"], top=1)
    _dump("team privileges: full tool response (top=1)", result)

    assert result["normalized"] is True
    assert result["count"] == 1
    assert result["total_count"] > 1
    assert result["has_more"] is True
    assert "message" in result
    summary = result.get("depth_summary")
    if summary:
        assert sum(summary.values()) == result["total_count"], (
            "depth_summary must be computed BEFORE trimming"
        )
    print(
        f"RECORDED: total_count={result['total_count']} with count=1 — the full "
        "magnitude survives trimming"
    )


async def test_unknown_team_id_is_recorded_as_error_or_empty() -> None:
    """A nonexistent team id: 404 like the role function, or an empty list?

    VERIFIED LIVE: it is the 404, so the tool's docstring — which used to claim
    that by analogy with ``RetrieveRolePrivilegesRole`` — is right. The outcome is
    still RECORDED rather than asserted, because this is the one place a platform
    change would show up, and the two answers to "does this team grant anything?"
    are materially different: an empty list must only ever mean a real team with no
    directly-assigned roles.
    """
    result = await _call("00000000-0000-0000-0000-000000000001")
    _dump("nonexistent team: full tool response", result)

    if result.get("error"):
        print(
            "RECORDED: a nonexistent team id returns the ERROR envelope — "
            f"{_redact(str(result['message']))}"
        )
        assert isinstance(result["message"], str) and result["message"]
        assert "privileges" not in result
    else:
        print(
            "RECORDED: a nonexistent team id returns a SUCCESSFUL response, "
            f"normalized={result.get('normalized')}, "
            f"total_count={result.get('total_count')}. This CONTRADICTS the live "
            "run behind the tool's docstring, which saw HTTP 404 [0x80040217] — "
            "the platform has changed, and an empty list is now indistinguishable "
            "from a real team that grants nothing. Correct the docstring."
        )
