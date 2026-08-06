"""Live integration coverage for dataverse_get_role_privileges.

``RetrieveRolePrivilegesRole`` is an unbound GET taking one ``Edm.Guid``
parameter. Microsoft Learn documents the call and the return TYPE
(``RetrieveRolePrivilegesRoleResponse``) but **not its inner properties**, so the
tool locates the privilege collection by shape as well as by name. This suite is
what turned that guess into a record, and is now what defends the recorded facts:
every test prints the raw payload and its size in bytes.

What a live run established (and what these tests now assert):

* the response carries exactly ONE top-level property, ``RolePrivileges``, holding
  a JSON list with no wrapper — tier 1 of ``_extract_privileges`` fires;
* every entry carries all SIX of ``BusinessUnitId``, ``Depth``, ``PrivilegeId``,
  ``PrivilegeName``, ``RecordFilterId``, ``RecordFilterUniqueName``. Because
  ``PrivilegeName`` is always present, the tool needs no second request and makes
  none;
* ``Depth`` is serialized as the OData enum MEMBER NAME (``"Basic"``, ``"Global"``
  observed) — a string, never a numeric ``PrivilegeDepth`` code. The tool passes
  it through unchanged and never relabels it;
* the magnitude: a System Administrator role returned 4,132 privileges in a
  ~1.04 MB raw response, against ~14 KB from the tool at the default ``top`` of 50
  — which is why trimming exists and why ``raw_response`` is not echoed back on
  the normalized path;
* a broad role and a narrow role really do differ, so the tool answers a question
  rather than echoing a constant;
* a well-formed but nonexistent role id returns HTTP 404 ``[0x80040217]``, not an
  empty collection.

No role id is hardcoded: every id is discovered live. Sizes and counts are printed
rather than asserted exactly, so an org with a different scale reports its numbers
instead of failing.

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
from dataverse_mcp.models import GetRolePrivilegesInput
from dataverse_mcp.tools.security import dataverse_get_role_privileges

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# The broadest role every org has. Matched case-insensitively; the suite skips
# rather than fails if an org has renamed it.
_BROAD_ROLE_NAME = "system administrator"

# Roles are fetched in one narrow page and paired up client-side; 400 is a
# generous ceiling for an org's role count.
_ROLE_PAGE_SIZE = 400

_RAW_BODY_PREVIEW = 4000

# The six per-entry keys recorded live on every RolePrivileges entry, sorted.
_EXPECTED_ENTRY_KEYS = [
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
    """Stop httpx's INFO logger printing the org host and role GUID unredacted.

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


async def _roles() -> list[dict]:
    """Discover roles live — no id is ever hardcoded.

    ``roles`` is a data-plane collection, so ``$filter`` is expected to work here
    (unlike ``EntityDefinitions`` on the schema plane, which this org rejects any
    ``$filter`` on with HTTP 400 ``[0x80060888]``). One narrow page is fetched and
    paired up client-side anyway: it costs a single request, needs no filter at
    all, and gives every test the same candidate set.
    """
    response = await _get(f"/roles?$select=name,roleid&$top={_ROLE_PAGE_SIZE}")
    assert response.status_code == 200, (
        "role discovery failed — HTTP "
        f"{response.status_code}: {_redact(response.text[:500])}"
    )
    rows = [
        row
        for row in response.json().get("value", [])
        if isinstance(row.get("roleid"), str) and isinstance(row.get("name"), str)
    ]
    print(f"\n=== discovered {len(rows)} role(s) (names/ids redacted below) ===")
    return rows


async def _broad_role() -> dict | None:
    return next(
        (r for r in await _roles() if r["name"].strip().lower() == _BROAD_ROLE_NAME),
        None,
    )


async def _call(role_id: str, **kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_get_role_privileges(
                GetRolePrivilegesInput(dataverse_url=_url(), role_id=role_id, **kwargs),
                ctx,
            )
        )


def _assert_contract(result: dict, role_id: str) -> None:
    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["role_id"] == role_id
    assert result["normalized"] is True, (
        "no privilege collection could be located — the response shape has "
        "changed or was never what _extract_privileges expects; read raw_response "
        "printed above before trusting this tool"
    )
    assert isinstance(result["privileges"], list)
    assert result["count"] == len(result["privileges"])
    assert result["total_count"] >= result["count"]
    assert result["has_more"] is (result["total_count"] > result["count"])
    # Name resolution was removed because PrivilegeName always ships with the
    # payload; nothing may reintroduce a key nothing can populate.
    for dead_key in ("names_resolved", "name_resolution"):
        assert dead_key not in result, (
            f"{dead_key} is gone — privilege name resolution was deleted after live "
            "runs showed PrivilegeName present on every entry"
        )
    for entry in result["privileges"]:
        assert sorted(entry) == _EXPECTED_ENTRY_KEYS, (
            "an entry did not carry the six keys recorded live; read the payload "
            f"printed above — got {sorted(entry)}"
        )
        assert isinstance(entry["PrivilegeName"], str) and entry["PrivilegeName"], (
            "PrivilegeName is missing or empty — the tool assumes it is always "
            "present and does no lookup, so this must be investigated"
        )
        assert isinstance(entry["Depth"], str), (
            "Depth arrived as "
            f"{type(entry['Depth']).__name__} ({entry['Depth']!r}), not the enum "
            "MEMBER NAME string recorded live. The tool still passes it through "
            "unrelabelled, which is safe, but the docstring's claim that Depth is "
            "human-readable no longer holds — record the new value before relaxing "
            "this."
        )


async def test_raw_call_records_the_undocumented_response_shape_and_size() -> None:
    """Call the function with the exact URL the tool builds and check the record.

    This is the test that pins the undocumented payload down: one top-level
    ``RolePrivileges`` list, six keys per entry, ``Depth`` as a member-name string.
    If a future platform version changes any of that, this is what fails first —
    and it prints enough to fix the tool from the failure output alone.
    """
    role = await _broad_role()
    if role is None:
        pytest.skip(f"this org has no role named {_BROAD_ROLE_NAME!r}")

    base_url = resolve_base_url(_url())
    # Exactly the URL src/dataverse_mcp/tools/security.py builds: one parameter
    # alias holding a BARE Edm.Guid literal (no quotes, no guid'...' prefix).
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveRolePrivilegesRole(RoleId=@rid)?@rid={role['roleid']}"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url, headers=_headers())

    print("\n=== RetrieveRolePrivilegesRole raw HTTP call ===")
    print(f"url (org host + guid redacted): {_redact(url)}")
    print(f"status: {response.status_code}")
    print(f"RESPONSE SIZE: {len(response.content)} bytes")

    assert response.status_code == 200, (
        "the bare-Guid parameter-alias URL was rejected — "
        f"HTTP {response.status_code}: {_redact(response.text[:1000])}"
    )

    body = response.json()
    # The recorded raw response is ~1.04 MB for this role, so only a prefix is
    # printed; the size above is the number that matters.
    print(f"RAW BODY (first {_RAW_BODY_PREVIEW} chars):")
    print(_redact(response.text[:_RAW_BODY_PREVIEW]))

    top_level = sorted(k for k in body if not k.startswith("@odata."))
    print(f"top_level_keys (envelope stripped): {top_level}")
    print(
        "value_types: "
        + json.dumps({k: type(body[k]).__name__ for k in top_level}, sort_keys=True)
    )
    assert top_level == ["RolePrivileges"], (
        "the response no longer carries exactly one top-level 'RolePrivileges' "
        f"property — got {top_level}. The tool's by-shape fallbacks may still cope, "
        "but the docstring's recorded shape does not."
    )

    entries = body["RolePrivileges"]
    assert isinstance(entries, list), (
        f"'RolePrivileges' is a {type(entries).__name__}, not the list recorded live"
    )
    print(f"\n--- 'RolePrivileges': {len(entries)} entries ---")
    assert entries, (
        "System Administrator returned no privileges at all — that is not the broad "
        "role this test assumes"
    )

    print(f"entry_keys: {sorted(entries[0])}")
    print(
        "entry_value_types: "
        + json.dumps(
            {k: type(v).__name__ for k, v in entries[0].items()}, sort_keys=True
        )
    )
    print(f"first entry: {_redact(json.dumps(entries[0], default=str))}")

    depths = {e.get("Depth") for e in entries}
    print(f"RECORDED distinct Depth values: {sorted(map(str, depths))}")
    print(
        "RECORDED entries carrying a non-empty PrivilegeName: "
        + str(sum(1 for e in entries if e.get("PrivilegeName")))
        + f" of {len(entries)}"
    )

    for entry in entries:
        assert sorted(entry) == _EXPECTED_ENTRY_KEYS, (
            "an entry did not carry the six keys recorded live — got "
            f"{sorted(entry)}"
        )
    assert all(isinstance(d, str) for d in depths), (
        "a Depth arrived as something other than the OData enum MEMBER NAME string "
        f"recorded live: {sorted(map(repr, depths))}. Do NOT map a numeric "
        "PrivilegeDepth code to a label — record the finding first."
    )
    assert all(e.get("PrivilegeName") for e in entries), (
        "an entry carried no PrivilegeName. The tool relies on the platform always "
        "supplying it and makes no lookup; if this ever fails, the removed "
        "resolution step needs reconsidering rather than silently returning GUIDs."
    )


async def test_broad_role_is_trimmed_but_reports_its_true_magnitude() -> None:
    """A System Administrator role is the payload-hygiene case this tool exists for."""
    role = await _broad_role()
    if role is None:
        pytest.skip(f"this org has no role named {_BROAD_ROLE_NAME!r}")

    result = await _call(role["roleid"])
    serialized = json.dumps(result)
    print(f"\n=== broad role: TOOL RESPONSE SIZE {len(serialized)} bytes ===")
    _dump("broad role: full tool response", result)

    _assert_contract(result, role["roleid"])
    print(
        f"RECORDED: broad role carries total_count={result['total_count']} "
        f"privileges, {result['count']} returned, "
        f"depth_summary={result.get('depth_summary')}, "
        f"privileges_source={result['privileges_source']!r}"
    )

    assert result["count"] <= 50, "the default top of 50 was not applied"
    assert result["total_count"] > 50, (
        "a System Administrator role reporting 50 or fewer privileges is not the "
        "broad role this test assumes — record the real number before relaxing this"
    )
    assert result["has_more"] is True
    assert "message" in result
    # depth_summary spans EVERY entry, which is the whole point of computing it
    # before trimming.
    summary = result.get("depth_summary")
    assert summary, "no Depth was reported on any entry; record the payload above"
    assert sum(summary.values()) == result["total_count"], (
        "depth_summary must cover the full collection, not just the returned page"
    )
    # Depth keys are the stringified raw values; live runs recorded member names
    # such as "Basic" and "Global", never a numeric code.
    assert all(not key.isdigit() for key in summary), (
        f"a numeric PrivilegeDepth code appeared in depth_summary: {sorted(summary)}. "
        "The tool reports it raw, which is correct, but the recorded member-name "
        "serialization no longer holds — record the finding before mapping anything."
    )
    # Trimming is the point: the tool's own response must be a fraction of the raw
    # payload it was cut from.
    print(
        f"RECORDED: tool response {len(serialized)} bytes for {result['count']} of "
        f"{result['total_count']} privileges"
    )


async def test_narrow_role_differs_from_the_broad_one() -> None:
    """Two roles, opposite magnitudes — evidence the tool reads real data."""
    roles = await _roles()
    broad = next(
        (r for r in roles if r["name"].strip().lower() == _BROAD_ROLE_NAME), None
    )
    if broad is None:
        pytest.skip(f"this org has no role named {_BROAD_ROLE_NAME!r}")

    broad_result = await _call(broad["roleid"])
    _assert_contract(broad_result, broad["roleid"])

    # The narrowest OTHER role in the org, discovered by asking each candidate.
    candidates = [r for r in roles if r["roleid"] != broad["roleid"]][:12]
    if not candidates:
        pytest.skip("this org has only one security role; nothing to compare against")

    narrowest: tuple[dict, dict] | None = None
    for candidate in candidates:
        result = await _call(candidate["roleid"], top=1)
        if result.get("error") or not result.get("normalized"):
            continue
        if narrowest is None or result["total_count"] < narrowest[1]["total_count"]:
            narrowest = (candidate, result)
    if narrowest is None:
        pytest.skip("no other role returned a readable privilege collection")

    narrow_role, narrow_result = narrowest
    print(
        f"\n=== narrowest other role: total_count={narrow_result['total_count']} "
        f"vs broad total_count={broad_result['total_count']} ==="
    )
    _dump("narrow role: full tool response (top=1)", narrow_result)

    assert narrow_result["total_count"] < broad_result["total_count"], (
        "every role in this org reports at least as many privileges as System "
        "Administrator — the tool may be echoing the same collection regardless "
        "of role id; inspect the payloads above"
    )
    assert narrow_result["count"] == min(1, narrow_result["total_count"])
    _assert_contract(narrow_result, narrow_role["roleid"])


async def test_privilege_names_arrive_without_a_second_request() -> None:
    """The payload names its own privileges, so the tool never looks anything up.

    This is the assumption that let the /privileges name-resolution step be
    deleted. If it ever breaks, the tool starts handing an LLM bare GUIDs.
    """
    role = await _broad_role()
    if role is None:
        pytest.skip(f"this org has no role named {_BROAD_ROLE_NAME!r}")

    result = await _call(role["roleid"], top=5)
    _dump("privilege names: full tool response (top=5)", result)

    _assert_contract(result, role["roleid"])
    names = [p["PrivilegeName"] for p in result["privileges"]]
    print(f"RECORDED PrivilegeName values as sent by Dataverse: {names}")
    assert any(n.startswith("prv") for n in names), (
        "no PrivilegeName looks like a Dataverse privilege name (prv*) — the "
        "payload's naming convention has changed; record the values above"
    )


async def test_unknown_role_id_returns_the_error_envelope() -> None:
    """A well-formed but nonexistent GUID is a 404, not an empty privilege list."""
    result = await _call("00000000-0000-0000-0000-000000000001")
    _dump("nonexistent role: full tool response", result)

    assert result.get("error") is True, (
        "a nonexistent role id no longer returns an error. Live runs recorded HTTP "
        "404 [0x80040217] Entity 'role' With Id = ... Does Not Exist; an empty "
        "collection now means 'a real role that grants nothing', so this change "
        "would make the two indistinguishable — record it before relaxing this."
    )
    assert isinstance(result["message"], str) and result["message"]
    assert "privileges" not in result
    assert "raw_response" not in result
    print(f"RECORDED unknown-role error message: {_redact(result['message'])}")
