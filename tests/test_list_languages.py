"""Unit tests for dataverse_list_languages.

The fixtures below encode the response shapes, all three of which a live run has
since confirmed:

    RetrieveProvisionedLanguages   -> {"RetrieveProvisionedLanguages": [1033, ...]}
    RetrieveAvailableLanguages     -> {"LocaleIds": [1033, ...]}
    RetrieveInstalledLanguagePacks -> {"RetrieveInstalledLanguagePacks": [1033, ...]}

The three container names genuinely DIFFER — two repeat the function name, one
does not — so ``_CONTAINERS`` below stays the single place to correct if that
changes; every test builds its bodies through the helpers rather than inlining a
shape. A previous round in this codebase shipped a bug precisely because unit
fixtures encoded a shape no real org produces, so the by-shape fallback is tested
as hard as the documented path.

The three SETS, however, are not what the tool originally assumed. Live, they
were mutually disjoint: available=[1033], provisioned=[1033], and installed_packs
held 44 other LCIDs with 1033 absent. A single ``available - provisioned`` diff
therefore reported "nothing to see" beside 44 undiffed installed packs, so the
tool now emits two diffs named after the exact subtraction each performs. The
fixtures below use disjoint sets deliberately, to keep that failure reproducible.

Acceptance criteria:
- Happy path: all three lists are echoed under their own keys with counts, BOTH
  derived diffs are computed, and every URL is the bare zero-parameter form with
  no query string.
- The container names really are read per function: a payload answering under a
  different (but unambiguous) property is still found, and sources reports the
  name it was actually found under.
- One function failing lands in partial_errors while the others' data is
  returned; derived answers that depend on the missing list are OMITTED, never
  computed against an assumed empty set.
- All three failing yields the standard error envelope.
- An unreadable payload degrades to normalized: false with the raw body — no
  fabricated empty list.
- bool is a subclass of int: [True, False] must NOT be accepted as locale ids.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import ListLanguagesInput
from dataverse_mcp.tools.metadata import (
    _extract_named_list,
    _is_locale_id_list,
    dataverse_list_languages,
)

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"

# The one place a live run should be corrected: function name -> container key.
_CONTAINERS = {
    "RetrieveProvisionedLanguages": "RetrieveProvisionedLanguages",
    "RetrieveAvailableLanguages": "LocaleIds",
    "RetrieveInstalledLanguagePacks": "RetrieveInstalledLanguagePacks",
}

_EXPECTED_URLS = {
    function: f"{_API_ROOT}/{function}()" for function in _CONTAINERS
}


def _make_ctx() -> MagicMock:
    """Return a mock MCPServer Context backed by a minimal AppContext."""
    app_ctx = AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


def _body(function: str, locale_ids: list) -> dict:
    """The documented envelope for *function*, under its own container name."""
    return {
        "@odata.context": f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.{function}Response",
        _CONTAINERS[function]: locale_ids,
    }


async def _run(responses: dict[str, Any]) -> tuple[dict, list[str]]:
    """Run the tool with a per-function response; return (result, requested URLs).

    A value may be a body (dict/list), or an ``httpx.Response`` / ``Exception``
    to drive a failure path.
    """
    params = ListLanguagesInput(dataverse_url=_BASE_URL)

    async def _side_effect(_client, _method, url, **_kwargs):
        function = url.rsplit("/", 1)[-1].removesuffix("()")
        outcome = responses[function]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, httpx.Response):
            outcome.request = httpx.Request("GET", url)
            return outcome
        return httpx.Response(200, json=outcome, request=httpx.Request("GET", url))

    with patch(
        "dataverse_mcp.tools.metadata.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.metadata.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_list_languages(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


def _all_ok(
    provisioned: list, available: list, installed: list
) -> dict[str, Any]:
    return {
        "RetrieveProvisionedLanguages": _body(
            "RetrieveProvisionedLanguages", provisioned
        ),
        "RetrieveAvailableLanguages": _body("RetrieveAvailableLanguages", available),
        "RetrieveInstalledLanguagePacks": _body(
            "RetrieveInstalledLanguagePacks", installed
        ),
    }


async def test_happy_path_reads_all_three_containers_and_derives_both_diffs() -> None:
    """Each list comes back under its own key, with both subtractions computed."""
    result, urls = await _run(
        _all_ok(
            provisioned=[1033, 1036],
            available=[1033, 1036, 1031],
            installed=[3082, 2057],
        )
    )

    assert result["normalized"] is True
    assert result["partial_errors"] == []

    assert result["provisioned"] == [1033, 1036]
    assert result["provisioned_count"] == 2
    assert result["available"] == [1033, 1036, 1031]
    assert result["available_count"] == 3
    assert result["installed_packs"] == [3082, 2057]
    assert result["installed_packs_count"] == 2

    # Two diffs, each named after the exact subtraction it performs, both sorted.
    assert result["available_not_provisioned"] == [1031]
    assert result["available_not_provisioned_count"] == 1
    assert result["installed_not_provisioned"] == [2057, 3082]
    assert result["installed_not_provisioned_count"] == 2

    # Each list is credited to the container it was really found under, and the
    # three names differ — that is the highest-risk fact about this tool.
    assert result["sources"] == {
        "provisioned": "RetrieveProvisionedLanguages",
        "available": "LocaleIds",
        "installed_packs": "RetrieveInstalledLanguagePacks",
    }

    # Zero-parameter unbound calls: bare parentheses, no query string at all.
    assert sorted(urls) == sorted(_EXPECTED_URLS.values())
    for url in urls:
        assert url.endswith("()")
        assert "?" not in url and "@" not in url and "%" not in url


async def test_installed_packs_are_diffed_even_when_available_has_nothing_to_say() -> None:
    """The live shape that a single available-minus-provisioned diff hid entirely.

    On a real org, available == provisioned == [1033] while 44 OTHER packs were
    installed. The old single diff came out [] and read as "nothing an
    administrator could look at", with the largest list never subtracted at all.
    """
    installed = [1036, 1031, 3082]
    result, _urls = await _run(
        _all_ok(provisioned=[1033], available=[1033], installed=installed)
    )

    assert result["available_not_provisioned"] == []
    assert result["available_not_provisioned_count"] == 0
    assert result["installed_not_provisioned"] == sorted(installed), (
        "installed packs must be diffed against provisioned in their own right"
    )
    assert result["installed_not_provisioned_count"] == 3


async def test_a_different_container_name_is_still_found_by_shape() -> None:
    """The documented names may be wrong; a sole list of ints is unambiguous."""
    responses = _all_ok(provisioned=[1033], available=[1033], installed=[1033])
    # Pretend RetrieveAvailableLanguages answers under its own name rather than
    # the documented LocaleIds — tier 3 (sole list-valued property) must fire.
    responses["RetrieveAvailableLanguages"] = {
        "@odata.context": "ctx",
        "RetrieveAvailableLanguages": [1033, 1036],
    }

    result, _urls = await _run(responses)

    assert result["normalized"] is True
    assert result["available"] == [1033, 1036]
    assert result["sources"]["available"] == "RetrieveAvailableLanguages", (
        "the source must name where the list was really found, not the documented key"
    )


async def test_one_failing_function_is_partial_not_fatal() -> None:
    """The other two lists survive, and no derived answer is invented."""
    responses = _all_ok(provisioned=[1033], available=[1033, 1036], installed=[1033])
    responses["RetrieveAvailableLanguages"] = httpx.Response(
        403, json={"error": {"code": "0x80040220", "message": "Principal lacks access"}}
    )

    result, _urls = await _run(responses)

    assert not result.get("error"), "one failure must not fail the whole tool"
    assert result["provisioned"] == [1033]
    assert result["installed_packs"] == [1033]
    assert "available" not in result and "available_count" not in result

    assert [e["function"] for e in result["partial_errors"]] == [
        "RetrieveAvailableLanguages"
    ]
    assert "403" in result["partial_errors"][0]["message"]

    # available is missing, so its diff must be OMITTED, never computed against
    # an assumed-empty set. The installed diff does not need it and survives.
    assert "available_not_provisioned" not in result
    assert "available_not_provisioned_count" not in result
    assert result["installed_not_provisioned"] == [], (
        "provisioned and installed_packs were both read; that diff still stands"
    )


async def test_a_network_failure_is_recorded_the_same_way() -> None:
    """Non-HTTP failures land in partial_errors too, never propagating."""
    responses = _all_ok(provisioned=[1033], available=[1033], installed=[1033])
    responses["RetrieveInstalledLanguagePacks"] = httpx.ConnectTimeout("timed out")

    result, _urls = await _run(responses)

    assert not result.get("error")
    assert result["provisioned"] == [1033]
    assert result["available_not_provisioned"] == [], "both its inputs were read"
    assert result["partial_errors"][0]["function"] == "RetrieveInstalledLanguagePacks"
    assert "installed_not_provisioned" not in result
    assert "installed_not_provisioned_count" not in result


async def test_all_three_failing_returns_the_error_envelope() -> None:
    """Nothing was read, so there is nothing to report but the failure."""
    result, _urls = await _run({
        function: httpx.Response(500, json={"error": {"message": "boom"}})
        for function in _CONTAINERS
    })

    assert result["error"] is True
    assert "all three functions failed" in result["message"]
    for function in _CONTAINERS:
        assert function in result["message"]


async def test_unreadable_payload_omits_the_key_rather_than_faking_an_empty_list() -> None:
    """A missing container is NOT "no languages" — the raw body comes back instead."""
    responses = _all_ok(provisioned=[1033], available=[1033], installed=[1033])
    # Two top-level lists: ambiguous, so nothing may be picked.
    responses["RetrieveProvisionedLanguages"] = {
        "@odata.context": "ctx",
        "Alpha": [1033],
        "Beta": [1036],
    }

    result, _urls = await _run(responses)

    assert result["normalized"] is False
    assert "provisioned" not in result and "provisioned_count" not in result
    assert result["raw_responses"] == {
        "RetrieveProvisionedLanguages": {"Alpha": [1033], "Beta": [1036]}
    }
    assert "RetrieveProvisionedLanguages" in result["message"]
    # provisioned was not read, and it is the subtrahend of BOTH diffs, so
    # neither may be computed — an unread list is not an empty one.
    assert "available_not_provisioned" not in result
    assert "installed_not_provisioned" not in result
    assert result["partial_errors"] == [], "a readable HTTP 200 is not a partial error"


async def test_booleans_are_not_locale_ids() -> None:
    """bool subclasses int; [True, False] must not pass as a list of LCIDs."""
    responses = _all_ok(provisioned=[1033], available=[1033], installed=[1033])
    responses["RetrieveProvisionedLanguages"] = {
        "RetrieveProvisionedLanguages": [True, False]
    }

    result, _urls = await _run(responses)

    assert result["normalized"] is False
    assert "provisioned" not in result
    assert "RetrieveProvisionedLanguages" in result["raw_responses"]


async def test_an_empty_but_present_container_is_a_real_answer() -> None:
    """[] under the documented key means "none", and is not degraded to raw."""
    result, _urls = await _run(
        _all_ok(provisioned=[], available=[1033], installed=[1033])
    )

    assert result["normalized"] is True
    assert result["provisioned"] == []
    assert result["provisioned_count"] == 0
    assert result["available_not_provisioned"] == [1033]
    assert result["installed_not_provisioned"] == [1033]


def test_locale_id_list_predicate() -> None:
    """The bool exclusion is the load-bearing part of this predicate."""
    assert _is_locale_id_list([1033, 1036]) is True
    assert _is_locale_id_list([]) is True
    assert _is_locale_id_list([True]) is False
    assert _is_locale_id_list([1033, True]) is False
    assert _is_locale_id_list(["1033"]) is False
    assert _is_locale_id_list(1033) is False
    assert _is_locale_id_list({"LocaleIds": [1033]}) is False


def test_extractor_tiers() -> None:
    """Bare body, then the documented key, then a sole list — else None."""
    # Tier 1 — the body itself is the list.
    assert _extract_named_list([1033], "LocaleIds", "Fn", _is_locale_id_list) == (
        "<response body>",
        [1033],
    )
    # Tier 2 — the documented container.
    assert _extract_named_list(
        {"LocaleIds": [1033], "Other": "x"}, "LocaleIds", "Fn", _is_locale_id_list
    ) == ("LocaleIds", [1033])
    # Tier 3 — a sole differently-named list.
    assert _extract_named_list(
        {"Whatever": [1033]}, "LocaleIds", "Fn", _is_locale_id_list
    ) == ("Whatever", [1033])
    # Ambiguity is refused rather than guessed.
    assert _extract_named_list(
        {"A": [1033], "B": [1036]}, "LocaleIds", "Fn", _is_locale_id_list
    ) is None
    # A sole list of the wrong item type is not the list we were looking for.
    assert _extract_named_list(
        {"Whatever": [{"a": 1}]}, "LocaleIds", "Fn", _is_locale_id_list
    ) is None
    # No container at all — None, so the caller omits the key.
    assert _extract_named_list({"Count": 3}, "LocaleIds", "Fn", _is_locale_id_list) is None
    assert _extract_named_list(None, "LocaleIds", "Fn", _is_locale_id_list) is None


def test_input_model_takes_nothing_but_the_url() -> None:
    """extra='forbid' keeps a caller from inventing filters this tool cannot honour."""
    assert ListLanguagesInput(dataverse_url=_BASE_URL).dataverse_url == _BASE_URL
    with pytest.raises(Exception):
        ListLanguagesInput(dataverse_url=_BASE_URL, top=10)
