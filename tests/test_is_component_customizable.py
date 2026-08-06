"""Unit tests for dataverse_is_component_customizable.

``IsComponentCustomizable``'s response shape was verified live against a real org:
it is **flat**, with exactly one property named after the function itself —
``{"IsComponentCustomizable": true}`` — observed for component types 1 (Entity),
2 (Attribute) and 61 (Web Resource). The tool reads that property directly and
keeps a defensive fallback (a lone boolean anywhere in the payload) for a
differently-shaped future payload.

Acceptance criteria:
- Happy path: the confirmed ``IsComponentCustomizable`` property is read
  directly; ``ComponentId``/``ComponentType`` ride parameter aliases; the GUID is
  written bare (no quotes, no ``guid'...'`` prefix, nothing percent-encoded) and
  the ``@odata`` envelope is stripped.
- The confirmed property wins even when other booleans sit beside it.
- Fallback: an unrecognized shape carrying a lone boolean still yields a verdict,
  with ``is_customizable_source`` naming where it came from.
- ``bool``/``int`` discrimination: an ``int`` is never promoted to a verdict —
  not under the confirmed property name, and not as a neighbour of a real bool.
- An unresolvable shape degrades safely: ``is_customizable`` is OMITTED (not
  null, not guessed), ``normalized`` is false, the payload still comes back raw,
  and nothing crashes.
- An invalid GUID or an out-of-range ``component_type`` is refused at the input
  model, so no URL is ever built.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import IsComponentCustomizableInput
from dataverse_mcp.tools.metadata import dataverse_is_component_customizable

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_COMPONENT_ID = "0d1b71c1-6a13-4c4a-9c8c-2c1f5c5f0a11"
_ENTITY_TYPE = 1
_PROPERTY = "IsComponentCustomizable"

# The exact URL the tool is expected to build: two parameter aliases, a bare Guid
# literal, and a bare integer.
_EXPECTED_URL = (
    f"{_API_ROOT}/IsComponentCustomizable(ComponentId=@cid,ComponentType=@ct)"
    f"?@cid={_COMPONENT_ID}&@ct={_ENTITY_TYPE}"
)


def _envelope(body: Any) -> Any:
    """Add the @odata.context annotation the tool is expected to strip."""
    if not isinstance(body, dict):
        return body
    return {
        "@odata.context": (
            f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM."
            "IsComponentCustomizableResponse"
        ),
        **body,
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


async def _run(
    body: Any, component_type: int = _ENTITY_TYPE, status_code: int = 200
) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = IsComponentCustomizableInput(
        dataverse_url=_BASE_URL,
        component_id=_COMPONENT_ID,
        component_type=component_type,
    )

    async def _side_effect(_client, _method, url, **_kwargs):
        return httpx.Response(status_code, json=body, request=httpx.Request("GET", url))

    with patch(
        "dataverse_mcp.tools.metadata.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.metadata.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_is_component_customizable(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_happy_path_reads_the_confirmed_property_and_builds_a_bare_guid_url() -> None:
    """The live-verified flat shape is read directly; the Guid takes no quoting."""
    result, urls = await _run(_envelope({_PROPERTY: True}))

    assert "error" not in result
    assert result["normalized"] is True
    assert result["is_customizable"] is True
    assert result["is_customizable_source"] == _PROPERTY
    assert result["component_id"] == _COMPONENT_ID
    assert result["component_type"] == _ENTITY_TYPE
    assert result["component_type_name"] == "Entity"
    # Envelope stripped, nothing else touched or renamed.
    assert result["raw_response"] == {_PROPERTY: True}

    assert urls == [_EXPECTED_URL]
    # An Edm.Guid literal is bare: no single quotes, no guid'...' prefix, and
    # nothing percent-encoded — the model's GUID pattern is the whole defence.
    assert "'" not in urls[0]
    assert "guid" not in urls[0]
    assert "%" not in urls[0]


@pytest.mark.asyncio
async def test_confirmed_property_wins_over_neighbouring_booleans() -> None:
    """A false verdict is reported even when the payload gains extra booleans.

    The fallback alone would call this payload ambiguous and omit the verdict;
    the direct lookup is what keeps it readable.
    """
    body = {_PROPERTY: False, "CanBeChanged": True, "ComponentType": 1}
    result, _ = await _run(_envelope(body))

    assert result["normalized"] is True
    assert result["is_customizable"] is False
    assert result["is_customizable_source"] == _PROPERTY
    assert "NOT customizable" in result["message"]
    assert result["raw_response"] == body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected_source,expected_value",
    [
        pytest.param(
            {"Result": False, "ComponentType": 1, "Detail": "managed"},
            "Result",
            False,
            id="lone-bool-beside-an-int",
        ),
        pytest.param(True, "<response body>", True, id="bare-boolean-body"),
    ],
)
async def test_fallback_accepts_a_lone_boolean_in_an_unrecognized_shape(
    body: Any, expected_source: str, expected_value: bool
) -> None:
    """Tier two: no confirmed property, but one unambiguous boolean.

    ``isinstance(v, bool)`` matches booleans only, which is the safe direction of
    the subclass trap — the reverse (``isinstance(v, int)``) would have counted
    ``ComponentType`` too and lost the verdict.
    """
    result, _ = await _run(_envelope(body))

    assert result["normalized"] is True
    assert result["is_customizable"] is expected_value
    assert result["is_customizable_source"] == expected_source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,reason",
    [
        pytest.param(
            {_PROPERTY: 1},
            "an int under the confirmed name must not be promoted to a verdict",
            id="confirmed-property-is-an-int",
        ),
        pytest.param(
            {_PROPERTY: "true"},
            "a stringified boolean under the confirmed name",
            id="confirmed-property-is-a-string",
        ),
        pytest.param(
            {"Result": True, "CanBeChanged": False},
            "two booleans, no confirmed property — which one is the verdict?",
            id="two-booleans-no-confirmed-property",
        ),
        pytest.param(
            {_PROPERTY: {"Value": True, "CanBeChanged": False}},
            "a nested BooleanManagedProperty — never seen live; guessing the path "
            "is exactly what is banned",
            id="nested",
        ),
        pytest.param({}, "an empty payload", id="empty"),
        pytest.param("OK", "a bare string payload", id="scalar"),
        pytest.param(None, "an empty body that is not JSON at all", id="empty-body"),
    ],
)
async def test_unresolvable_payloads_omit_the_verdict_without_crashing(
    body: Any, reason: str
) -> None:
    """No verdict is better than a guessed one — the key is omitted, not nulled."""
    result, _ = await _run(_envelope(body))

    assert "error" not in result, reason
    assert result["normalized"] is False, reason
    assert "is_customizable" not in result, f"a verdict was fabricated: {reason}"
    assert "is_customizable_source" not in result, reason
    assert "OMITTED" in result["message"]
    # Type name and the raw payload survive so the caller can still act.
    assert result["component_type_name"] == "Entity"
    expected = (
        {k: v for k, v in body.items() if not k.startswith("@odata.")}
        if isinstance(body, dict)
        else body
    )
    assert result["raw_response"] == expected


@pytest.mark.asyncio
async def test_unknown_component_type_still_resolves_to_a_readable_label() -> None:
    """An unmapped code must not break the response or read as a real name."""
    result, urls = await _run(_envelope({_PROPERTY: True}), component_type=99999)

    assert result["component_type_name"] == "ComponentType(99999)"
    assert urls[0].endswith("&@ct=99999")


@pytest.mark.asyncio
async def test_http_errors_use_the_standard_error_envelope() -> None:
    """A nonexistent component id is an HTTP 400, not a 'not customizable' verdict.

    Live-observed: a well-formed but unknown GUID returns
    ``[0x80040216] There should be at least one metadata entity returned for
    EntityName: ...``.
    """
    result, _ = await _run(
        {
            "error": {
                "code": "0x80040216",
                "message": (
                    "There should be at least one metadata entity returned for "
                    "EntityName: <name>."
                ),
            }
        },
        status_code=400,
    )

    assert result["error"] is True
    assert "400" in result["message"]
    assert "raw_response" not in result


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param(
            {"component_id": "not-a-guid", "component_type": 1},
            "a non-GUID component_id",
            id="bad-guid",
        ),
        pytest.param(
            {"component_id": _COMPONENT_ID[:-1], "component_type": 1},
            "a truncated GUID",
            id="short-guid",
        ),
        pytest.param(
            {"component_id": f"{_COMPONENT_ID})/WhoAmI(", "component_type": 1},
            "a GUID with a URL-breakout suffix — the pattern is the only defence",
            id="guid-breakout",
        ),
        pytest.param(
            {"component_id": _COMPONENT_ID, "component_type": 0},
            "component_type below the minimum",
            id="type-too-low",
        ),
        pytest.param(
            {"component_id": _COMPONENT_ID, "component_type": 10**9},
            "component_type above the URL-length bound",
            id="type-too-high",
        ),
        pytest.param(
            {"component_id": _COMPONENT_ID, "component_type": "1; DROP"},
            "a non-integer component_type",
            id="type-not-an-int",
        ),
    ],
)
def test_input_model_rejects_bad_identifiers(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    with pytest.raises(ValidationError):
        IsComponentCustomizableInput(dataverse_url=_BASE_URL, **kwargs)
