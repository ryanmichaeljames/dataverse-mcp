"""Live integration coverage for dataverse_list_languages.

Three unbound, zero-parameter GET functions are wrapped. Two answer under a
property named after the FUNCTION, the third under ``LocaleIds``, and all three
container names are now LIVE-CONFIRMED (the tool's preferred-key tier fired for
each):

    RetrieveProvisionedLanguages   -> {"RetrieveProvisionedLanguages": [...]}
    RetrieveAvailableLanguages     -> {"LocaleIds": [...]}
    RetrieveInstalledLanguagePacks -> {"RetrieveInstalledLanguagePacks": [...]}

What the live run overturned is the MEANING, not the shape. The prediction was
that installed packs would roughly equal available languages. In fact the three
sets were mutually disjoint — available=[1033], provisioned=[1033], and 44
installed packs with 1033 ABSENT — so nothing here may assume one set contains,
or overlaps, another. A fourth function, ``RetrieveDeprovisionedLanguages()``,
also answers 200 (returning ``[]`` on that org); it is deliberately not wrapped.

Every test prints the raw payload, its size and its top-level keys, so a shape
that differs can be corrected from the failure output alone — update
``_EXPECTED_CONTAINERS`` here and ``_LANGUAGE_FUNCTIONS`` in
``src/dataverse_mcp/tools/metadata.py`` together.

Nothing is hardcoded about the org: no LCID is assumed to be present, and counts
and sizes are printed rather than asserted exactly, so an org with a different
language set reports its numbers instead of failing.

Read-only throughout: nothing is created, updated, provisioned or published.

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
from dataverse_mcp.models import ListLanguagesInput
from dataverse_mcp.tools.metadata import _LANGUAGE_FUNCTIONS, dataverse_list_languages

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# The documented container name per function — what this suite is here to confirm.
_EXPECTED_CONTAINERS = {
    "RetrieveProvisionedLanguages": "RetrieveProvisionedLanguages",
    "RetrieveAvailableLanguages": "LocaleIds",
    "RetrieveInstalledLanguagePacks": "RetrieveInstalledLanguagePacks",
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


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the sandbox token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _call() -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_list_languages(
                ListLanguagesInput(dataverse_url=_url()), ctx
            )
        )


async def test_raw_calls_record_the_three_container_names() -> None:
    """Call each function with the exact URL the tool builds and record the shape.

    This is the test that pins the three DIFFERENT container names down. If a
    future platform version (or the documentation this tool was written from)
    disagrees, this is what fails first, and it prints enough to fix
    ``_LANGUAGE_FUNCTIONS`` from the failure output alone.
    """
    base_url = resolve_base_url(_url())
    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
    recorded: dict[str, str] = {}

    for _key, function_name, property_name in _LANGUAGE_FUNCTIONS:
        # Exactly the URL src/dataverse_mcp/tools/metadata.py builds: a
        # zero-parameter unbound function, bare parentheses, no query string.
        url = f"{api_root}/{function_name}()"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=_headers())

        print(f"\n=== {function_name} raw HTTP call ===")
        print(f"url (org host redacted): {_redact(url)}")
        print(f"status: {response.status_code}")
        print(f"RESPONSE SIZE: {len(response.content)} bytes")
        print(f"RAW BODY (first {_RAW_BODY_PREVIEW} chars):")
        print(_redact(response.text[:_RAW_BODY_PREVIEW]))

        assert response.status_code == 200, (
            f"{function_name}() was rejected — HTTP {response.status_code}: "
            f"{_redact(response.text[:1000])}"
        )

        body = response.json()
        top_level = sorted(k for k in body if not k.startswith("@odata."))
        print(f"top_level_keys (envelope stripped): {top_level}")
        print(
            "value_types: "
            + json.dumps({k: type(body[k]).__name__ for k in top_level}, sort_keys=True)
        )

        lists = [k for k in top_level if isinstance(body[k], list)]
        assert len(lists) == 1, (
            f"{function_name} no longer carries exactly one top-level list — got "
            f"{top_level}. The tool's by-shape fallback refuses ambiguity, so it "
            "would degrade to normalized: false; record the new shape."
        )
        recorded[function_name] = lists[0]

        entries = body[lists[0]]
        print(f"RECORDED container: {lists[0]!r} holding {len(entries)} entries")
        print(f"RECORDED entry types: {sorted({type(e).__name__ for e in entries})}")
        print(f"RECORDED values: {sorted(entries)}")
        assert all(isinstance(e, int) and not isinstance(e, bool) for e in entries), (
            f"{function_name} returned non-integer locale ids: {entries!r}. The tool "
            "requires real ints (bool is excluded deliberately) and would degrade to "
            "normalized: false; record the new type before relaxing this."
        )
        if lists[0] != property_name:
            print(
                f"!!! DOCUMENTED container for {function_name} was {property_name!r} "
                f"but the org answered under {lists[0]!r} — update _LANGUAGE_FUNCTIONS"
            )

    print(f"\n=== RECORDED container names: {json.dumps(recorded, sort_keys=True)} ===")
    assert recorded == _EXPECTED_CONTAINERS, (
        "the container names differ from the documented ones this tool was written "
        f"from. Recorded: {recorded}. Update _LANGUAGE_FUNCTIONS in "
        "src/dataverse_mcp/tools/metadata.py and _EXPECTED_CONTAINERS here together. "
        "(The tool still copes via its by-shape fallback, but `sources` is then the "
        "only place the real name appears.)"
    )


async def test_the_tool_reconciles_all_three_lists() -> None:
    """The whole contract, against a real org."""
    result = await _call()
    _dump("dataverse_list_languages: full tool response", result)

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["partial_errors"] == [], (
        "a language function failed on this org: "
        f"{_redact(json.dumps(result['partial_errors']))}. That is survivable by "
        "design, but record which one before treating it as normal."
    )
    assert result["normalized"] is True, (
        "at least one payload could not be read — see raw_responses in the dump "
        "above and correct _LANGUAGE_FUNCTIONS"
    )

    for key in ("provisioned", "available", "installed_packs"):
        assert isinstance(result[key], list), f"{key} is not a list"
        assert result[f"{key}_count"] == len(result[key])
        assert all(
            isinstance(v, int) and not isinstance(v, bool) for v in result[key]
        ), f"{key} carried a non-integer locale id"

    print(
        "RECORDED: provisioned="
        f"{sorted(result['provisioned'])} ({result['provisioned_count']}), "
        f"available={result['available_count']}, "
        f"installed_packs={result['installed_packs_count']}, "
        f"available_not_provisioned={result['available_not_provisioned_count']}, "
        f"installed_not_provisioned={result['installed_not_provisioned_count']}, "
        f"sources={json.dumps(result['sources'], sort_keys=True)}"
    )

    assert result["provisioned"], (
        "this org reports NO provisioned language, which cannot be true — a "
        "Dataverse environment always has a base language. The tool is reading the "
        "wrong property; inspect the dump above."
    )
    # Each derived diff must be exactly the subtraction its name claims, sorted.
    assert set(result["available_not_provisioned"]) == set(result["available"]) - set(
        result["provisioned"]
    )
    assert result["available_not_provisioned"] == sorted(
        result["available_not_provisioned"]
    )
    assert set(result["installed_not_provisioned"]) == set(
        result["installed_packs"]
    ) - set(result["provisioned"])
    assert result["installed_not_provisioned"] == sorted(
        result["installed_not_provisioned"]
    )

    # THE OVERTURNED PREDICTION, recorded rather than asserted: installed packs
    # were expected to approximate the available languages, and live they shared
    # NOTHING. Print the three-way relationship every run so a change is visible.
    available_set = set(result["available"])
    installed_set = set(result["installed_packs"])
    print(
        "RECORDED set relationships: "
        f"available_and_installed={sorted(available_set & installed_set)}, "
        f"installed_only={len(installed_set - available_set)}, "
        f"available_only={sorted(available_set - installed_set)}, "
        f"provisioned_not_available="
        f"{sorted(set(result['provisioned']) - available_set)}"
    )
    if not (available_set & installed_set):
        print(
            "!!! RECORDED: available and installed_packs are DISJOINT — the two "
            "functions describe different things entirely. This matched the live "
            "baseline (available=[1033] vs 44 installed packs without 1033); "
            "neither list may be inferred from the other."
        )


async def test_the_answer_is_small_enough_to_need_no_paging() -> None:
    """The design bet: ~40 small ints per list, so no top and no trimming."""
    result = await _call()
    serialized = json.dumps(result)
    print(f"\n=== TOOL RESPONSE SIZE: {len(serialized)} bytes ===")

    assert not result.get("error")
    total = (
        result["provisioned_count"]
        + result["available_count"]
        + result["installed_packs_count"]
    )
    print(f"RECORDED: {total} locale ids across all three lists")
    assert len(serialized) < 100_000, (
        f"the response grew to {len(serialized)} bytes. The no-paging design "
        "assumed a few hundred small integers; record the real magnitude and "
        "reconsider trimming."
    )


async def test_two_calls_agree() -> None:
    """Evidence the tool reads the environment rather than echoing a constant."""
    first = await _call()
    second = await _call()

    for key in ("provisioned", "available", "installed_packs"):
        assert first[key] == second[key], (
            f"{key} changed between two consecutive reads — these functions are "
            "supposed to be stable environment configuration"
        )
    print(
        "RECORDED: two consecutive calls returned identical lists "
        f"({first['provisioned_count']}/{first['available_count']}/"
        f"{first['installed_packs_count']})"
    )
