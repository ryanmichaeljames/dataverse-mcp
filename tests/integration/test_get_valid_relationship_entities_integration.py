"""Live integration coverage for dataverse_get_valid_relationship_entities.

Three unbound GET functions are wrapped and their parameter names are INVERTED
relative to the function names — you always name the OTHER side:

    GetValidReferencedEntities(ReferencingEntityName='account')   <- optional
    GetValidReferencingEntities(ReferencedEntityName='account')   <- optional
    GetValidManyToMany()                                          <- no parameter

That inversion reads like a bug and invites a "fix". It does not need one, and a
swap would NOT fail silently: these are overloads keyed on the parameter name, so
a swapped pair resolves to no function at all and answers HTTP 404 [0x80060888]
"Resource not found for the segment". The suite still checks the two 1:N roles
against each other, because that catches the different failure of both roles
resolving to the same function.

TWO LIVE FINDINGS ARE PINNED HERE, so that this suite fails the day either stops
being true:

* the optional ``…EntityName`` parameter DOES NOT NARROW the answer — scoped and
  unscoped calls returned byte-identical bodies — while still being validated
  server-side (an unknown table is HTTP 400 [0x80041102]);
* the magnitudes are referenced 575 / many_to_many 305 / referencing 166, so
  ``referenced`` is the biggest, not ``many_to_many``.

The response shape ``{"EntityNames": [...]}`` is live-confirmed for all three
functions; every test still prints the raw payload, its size and its top-level
keys so a change can be corrected from the failure output.

No table name is hardcoded except ``account``, which every org has; magnitudes
are printed rather than asserted exactly.

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
from dataverse_mcp.models import GetValidRelationshipEntitiesInput
from dataverse_mcp.tools.metadata import (
    _RELATIONSHIP_ROLE_FUNCTIONS,
    dataverse_get_valid_relationship_entities,
)

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# The documented container name — what this suite is here to confirm.
_EXPECTED_CONTAINER = "EntityNames"

# Present in every Dataverse org, so no discovery step is needed.
_PROBE_TABLE = "account"

_RAW_BODY_PREVIEW = 3000
_NAME_SAMPLE = 20

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
    """Stop httpx's INFO logger printing the org host unredacted.

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


def _summarize(label: str, result: dict) -> None:
    """Print a bounded summary — these lists run to hundreds of names."""
    names = result.get("table_logical_names", [])
    print(
        f"\n=== {label}: count={result.get('count')} "
        f"total_count={result.get('total_count')} has_more={result.get('has_more')} "
        f"source={result.get('source')!r} function={result.get('function')!r} "
        f"parameter={result.get('parameter')!r} ==="
    )
    print(f"first {_NAME_SAMPLE} names: {_redact(json.dumps(names[:_NAME_SAMPLE]))}")


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the sandbox token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _call(role: str, **kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_get_valid_relationship_entities(
                GetValidRelationshipEntitiesInput(
                    dataverse_url=_url(), role=role, **kwargs
                ),
                ctx,
            )
        )


def _assert_contract(result: dict, role: str) -> None:
    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["role"] == role
    assert result["function"] == _RELATIONSHIP_ROLE_FUNCTIONS[role][0]
    assert result["parameter"] == _RELATIONSHIP_ROLE_FUNCTIONS[role][1]
    assert result["normalized"] is True, (
        "no list of table names could be located — the response shape has changed "
        "or was never what the tool expects; read raw_response printed above before "
        "trusting this tool"
    )
    assert isinstance(result["table_logical_names"], list)
    assert all(isinstance(n, str) for n in result["table_logical_names"])
    assert result["count"] == len(result["table_logical_names"])
    assert result["total_count"] >= result["count"]
    assert result["has_more"] is (result["total_count"] > result["count"])


async def test_raw_calls_record_the_shape_of_all_five_url_forms() -> None:
    """Every URL the tool can build, issued verbatim, with its shape recorded.

    Five forms: each 1:N role with and without the optional parameter, plus
    GetValidManyToMany. All five are accepted, and the scoped/unscoped pairs come
    back the SAME SIZE — the parameter is a different REQUEST but not a different
    ANSWER (see test_the_optional_parameter_does_not_narrow_the_answer). The
    recorded magnitudes were referenced 575, many_to_many 305, referencing 166.
    """
    base_url = resolve_base_url(_url())
    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"

    forms = [
        (
            "referenced (scoped)",
            f"{api_root}/GetValidReferencedEntities(ReferencingEntityName=@p1)"
            f"?@p1='{_PROBE_TABLE}'",
        ),
        ("referenced (all)", f"{api_root}/GetValidReferencedEntities()"),
        (
            "referencing (scoped)",
            f"{api_root}/GetValidReferencingEntities(ReferencedEntityName=@p1)"
            f"?@p1='{_PROBE_TABLE}'",
        ),
        ("referencing (all)", f"{api_root}/GetValidReferencingEntities()"),
        ("many_to_many", f"{api_root}/GetValidManyToMany()"),
    ]

    recorded: dict[str, int] = {}
    for label, url in forms:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=_headers())

        print(f"\n=== {label}: raw HTTP call ===")
        print(f"url (org host redacted): {_redact(url)}")
        print(f"status: {response.status_code}")
        print(f"RESPONSE SIZE: {len(response.content)} bytes")
        print(f"RAW BODY (first {_RAW_BODY_PREVIEW} chars):")
        print(_redact(response.text[:_RAW_BODY_PREVIEW]))

        assert response.status_code == 200, (
            f"{label} was rejected — HTTP {response.status_code}: "
            f"{_redact(response.text[:1000])}. Check the INVERTED parameter names "
            "first: GetValidReferencedEntities takes ReferencingEntityName and vice "
            "versa."
        )

        body = response.json()
        top_level = sorted(k for k in body if not k.startswith("@odata."))
        print(f"top_level_keys (envelope stripped): {top_level}")
        assert top_level == [_EXPECTED_CONTAINER], (
            f"{label} no longer carries exactly one top-level "
            f"{_EXPECTED_CONTAINER!r} property — got {top_level}. The tool's "
            "by-shape fallback may still cope, but the recorded shape does not."
        )
        names = body[_EXPECTED_CONTAINER]
        assert isinstance(names, list) and all(isinstance(n, str) for n in names), (
            f"{label}: {_EXPECTED_CONTAINER} is not a list of strings"
        )
        recorded[label] = len(names)
        print(f"RECORDED: {len(names)} names, first {_NAME_SAMPLE}: {names[:_NAME_SAMPLE]}")

    print(f"\n=== RECORDED magnitudes: {json.dumps(recorded, sort_keys=True)} ===")
    assert recorded["many_to_many"] > 0, "GetValidManyToMany returned nothing at all"


async def test_the_optional_parameter_does_not_narrow_the_answer() -> None:
    """PINS THE KNOWN TRUTH: supplying table_logical_name changes NOTHING.

    Measured live, ``GetValidReferencedEntities()`` and
    ``GetValidReferencedEntities(ReferencingEntityName='account')`` returned
    byte-identical bodies (575 names, 12918 bytes, identical sha256), as did the
    referencing pair (166 names) — for every table tried. The parameter is
    validated server-side but has no effect on the result.

    So this asserts EQUALITY, not a subset. A subset assertion is vacuously true
    when the sets are identical and would have proved nothing. If Microsoft ever
    starts honouring the parameter, THIS TEST FAILS — which is the point: that is
    the day the tool's docstring, its ``table_logical_name_filtered: false`` and
    its schema description all become wrong together and must be revised.

    The evidence covers CUSTOM tables too, not just system ones: the org tested
    carries custom tables, and scoping by one returned the same byte-identical
    list that the system tables did.
    """
    for role in ("referenced", "referencing"):
        scoped = await _call(role, table_logical_name=_PROBE_TABLE, top=5000)
        every = await _call(role, top=5000)
        _summarize(f"{role} scoped to {_PROBE_TABLE}", scoped)
        _summarize(f"{role} unscoped", every)

        _assert_contract(scoped, role)
        _assert_contract(every, role)

        assert scoped["has_more"] is False and every["has_more"] is False, (
            "top=5000 did not cover the whole answer; the comparison below would be "
            "against a truncated set"
        )

        # The tool must not claim a narrowing, whichever way the call was made.
        assert scoped["table_logical_name_filtered"] is False
        assert every["table_logical_name_filtered"] is False
        assert "did NOT narrow" in scoped["table_logical_name_note"]
        assert "table_logical_name_note" not in every

        only_scoped = sorted(
            set(scoped["table_logical_names"]) - set(every["table_logical_names"])
        )
        only_unscoped = sorted(
            set(every["table_logical_names"]) - set(scoped["table_logical_names"])
        )
        print(
            f"RECORDED {role}: scoped={scoped['total_count']} "
            f"unscoped={every['total_count']} "
            f"only_scoped={len(only_scoped)} only_unscoped={len(only_unscoped)}"
        )

        assert scoped["table_logical_names"] == every["table_logical_names"], (
            f"{role}: the scoped and unscoped answers now DIFFER "
            f"(only_scoped={only_scoped[:20]}, only_unscoped={only_unscoped[:20]}). "
            "That contradicts the live finding this tool documents: "
            "table_logical_name was measured to have NO effect. If this is real, "
            "the parameter has started filtering — make table_logical_name_filtered "
            "a real computation, and correct the tool docstring and the "
            "GetValidRelationshipEntitiesInput field descriptions, which currently "
            "tell callers it does not narrow."
        )


async def test_an_unknown_table_name_is_still_rejected_server_side() -> None:
    """The parameter does not filter, but it IS validated — that is its only value.

    This is what makes supplying the name worth anything at all, and it is the
    reason the parameter is kept rather than dropped from the tool.
    """
    result = await _call("referenced", table_logical_name="zzz_no_such_table_xyz")
    _dump("nonexistent table: full tool response", result)

    assert result.get("error") is True, (
        "a nonexistent table was ACCEPTED. The parameter was already known not to "
        "filter; if it no longer validates either, it is purely decorative and the "
        "docstring's one remaining claim for it must go."
    )
    print(f"RECORDED unknown-table rejection: {_redact(result['message'])}")


async def test_the_two_one_to_many_roles_answer_different_questions() -> None:
    """referenced != referencing — evidence role really selects a different function.

    A swapped parameter pair fails loudly (HTTP 404), so this is not what guards
    the inversion. What it guards is both roles collapsing onto one function or
    one cached answer: live the two differ sharply, 575 names against 166.
    """
    referenced = await _call("referenced", table_logical_name=_PROBE_TABLE, top=5000)
    referencing = await _call("referencing", table_logical_name=_PROBE_TABLE, top=5000)
    _summarize("referenced", referenced)
    _summarize("referencing", referencing)

    _assert_contract(referenced, "referenced")
    _assert_contract(referencing, "referencing")

    left = set(referenced["table_logical_names"])
    right = set(referencing["table_logical_names"])
    print(
        f"RECORDED: referenced={len(left)}, referencing={len(right)}, "
        f"only_referenced={len(left - right)}, only_referencing={len(right - left)}"
    )
    assert left != right, (
        "both 1:N roles returned an identical list for the same table. Live they "
        "differed by hundreds of names (575 vs 166), so this points at the two "
        "roles resolving to the same function or the same cached answer — verify "
        "the URLs actually issued before accepting it."
    )


async def test_many_to_many_is_trimmed_but_reports_its_true_magnitude() -> None:
    """The payload-hygiene case: 305 names live (~7 KB), so top earns its keep."""
    trimmed = await _call("many_to_many", top=10)
    _summarize("many_to_many (top=10)", trimmed)
    _assert_contract(trimmed, "many_to_many")

    assert trimmed["count"] == 10
    assert trimmed["parameter"] is None
    assert trimmed["table_logical_name_filtered"] is False

    full = await _call("many_to_many", top=5000)
    _assert_contract(full, "many_to_many")
    serialized = json.dumps(full)
    print(
        f"\n=== RECORDED many_to_many: total_count={full['total_count']}, "
        f"untrimmed tool response {len(serialized)} bytes, "
        f"trimmed(top=10) {len(json.dumps(trimmed))} bytes ==="
    )

    assert trimmed["total_count"] == full["total_count"], (
        "total_count must describe the full set regardless of top"
    )
    assert trimmed["has_more"] is (full["total_count"] > 10)
    assert full["table_logical_names"][:10] == trimmed["table_logical_names"], (
        "trimming must take the first top entries, not a different slice"
    )
    if full["total_count"] < 100:
        print(
            f"!!! RECORDED: only {full['total_count']} N:N-capable tables on this "
            "org — well under the 305 measured live, so the 250 default now trims "
            "nothing here"
        )


async def test_the_default_top_bounds_the_biggest_answer() -> None:
    """A caller who passes no top must still get a bounded payload."""
    result = await _call("many_to_many")
    _summarize("many_to_many (default top)", result)
    _assert_contract(result, "many_to_many")

    assert result["count"] <= 250, "the default top of 250 was not applied"
    print(
        f"RECORDED: default call returned {result['count']} of "
        f"{result['total_count']} names, has_more={result['has_more']}, "
        f"{len(json.dumps(result))} bytes"
    )
    if result["has_more"]:
        assert "message" in result, "a truncated answer must say so"


