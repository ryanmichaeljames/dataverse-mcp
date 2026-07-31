"""Unit tests for dataverse_get_setting.

Every fixture below uses the LIVE-CONFIRMED response shape,
``{"SettingDetail": {"Name": ..., "Value": "false", "DataType": 2}}`` — the value
is NESTED. An earlier round of these tests asserted a flat ``{"Value": ...}``
envelope that no real org returns, which let a tool that never normalized once
against a live org pass its whole suite. A fixture that encodes a shape the
platform does not produce is worse than no fixture.

Acceptance criteria:
- Happy path: the value is lifted out of SettingDetail, the source is reported as
  SettingDetail.Value, and the Name / DataType siblings ride along (DataType
  unmapped — it is an integer code with no verified meaning).
- BOTH URL forms: with app_unique_name both parameters and both aliases are sent
  as single-quoted OData literals; without it, that parameter and its alias are
  omitted entirely rather than sent empty.
- Unknown setting name: HTTP 200 with SettingDetail: null is a SUCCESSFUL call
  meaning "no such setting" — setting_found=false, no setting_value.
- Falsy values ("", 0, False, and the string "false") are real answers, must
  normalize, and must be distinguishable from the unknown-name case.
- Unrecognized payload shape: nothing is fabricated — setting_value is omitted,
  normalized=false, and the raw body comes back.
- Input validation: a setting name carrying an OData breakout payload is rejected
  by the model, so it never reaches a URL.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import GetSettingInput
from dataverse_mcp.tools.environments import (
    _extract_setting_detail_metadata,
    _extract_setting_value,
    _setting_is_absent,
    dataverse_get_setting,
)

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"

# Form 1 — organization scope: AppUniqueName absent from the signature AND the
# query string.
_EXPECTED_ORG_URL = (
    f"{_API_ROOT}/RetrieveSetting(SettingName=@s)?@s='Sales.EnableForecasting'"
)

# Form 2 — app scope: both parameters, each a single-quoted OData literal.
_EXPECTED_APP_URL = (
    f"{_API_ROOT}/RetrieveSetting(SettingName=@s,AppUniqueName=@a)"
    "?@s='Sales.EnableForecasting'&@a='prefix_SalesHub'"
)


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


async def _run(body: Any, **kwargs: Any) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = GetSettingInput(dataverse_url=_BASE_URL, **kwargs)

    async def _side_effect(_client, _method, url, **_kwargs):
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    with patch(
        "dataverse_mcp.tools.environments.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.environments.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_get_setting(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


def _detail(value: Any, name: str = "Sales.EnableForecasting", data_type: int = 2) -> dict:
    """The live-confirmed envelope: the value NESTED inside SettingDetail."""
    return {
        "@odata.context": (
            f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.RetrieveSettingResponse"
        ),
        "SettingDetail": {"Name": name, "Value": value, "DataType": data_type},
    }


@pytest.mark.asyncio
async def test_organization_scope_omits_the_app_parameter_and_its_alias() -> None:
    """No app_unique_name means RetrieveSetting(SettingName=@s) and nothing more."""
    result, urls = await _run(
        _detail("false"), setting_name="Sales.EnableForecasting"
    )

    assert result["normalized"] is True
    assert result["setting_found"] is True
    # Live, Value is the STRING "false" — not a JSON boolean. It passes through as sent.
    assert result["setting_value"] == "false"
    assert result["setting_value_source"] == "SettingDetail.Value"
    assert result["scope"] == "organization"
    assert result["app_unique_name"] is None
    assert result["raw_response"] == {
        "SettingDetail": {
            "Name": "Sales.EnableForecasting",
            "Value": "false",
            "DataType": 2,
        }
    }
    assert urls == [_EXPECTED_ORG_URL]
    assert "AppUniqueName" not in urls[0] and "@a" not in urls[0]


@pytest.mark.asyncio
async def test_the_detail_siblings_are_surfaced_and_datatype_stays_a_raw_code() -> None:
    """Name and DataType ride along; DataType is NOT relabelled to a type name."""
    result, _urls = await _run(
        _detail("false", name="Sales.EnableForecasting", data_type=2),
        setting_name="Sales.EnableForecasting",
    )

    assert result["setting_detail_name"] == "Sales.EnableForecasting"
    assert result["setting_data_type"] == 2, "the integer code passes through as-is"
    assert not any(
        isinstance(v, str) and v.lower() in {"boolean", "string", "number"}
        for v in result.values()
    ), "DataType must not be mapped to an unverified type name"


@pytest.mark.asyncio
async def test_app_scope_sends_both_single_quoted_literals() -> None:
    """Both Edm.String parameters travel as quoted, percent-encoded literals."""
    result, urls = await _run(
        _detail(42),
        setting_name="Sales.EnableForecasting",
        app_unique_name="prefix_SalesHub",
    )

    assert result["scope"] == "app"
    assert result["setting_value"] == 42
    assert urls == [_EXPECTED_APP_URL]


@pytest.mark.asyncio
async def test_falsy_values_are_real_answers() -> None:
    """"", 0, False and the string "false" normalize — a config diff needs exactly these."""
    for raw in ("", 0, False, "false"):
        result, _urls = await _run(_detail(raw), setting_name="Some.Setting")
        assert result["normalized"] is True, f"{raw!r} fell through to raw"
        assert result["setting_found"] is True, (
            f"{raw!r} is a real value and must not read as 'no such setting'"
        )
        assert result["setting_value"] == raw


@pytest.mark.asyncio
async def test_unknown_setting_name_is_a_successful_empty_answer() -> None:
    """Live: an unknown name is HTTP 200 with SettingDetail: null, not an error."""
    body = {"@odata.context": "ctx", "SettingDetail": None}

    result, _urls = await _run(body, setting_name="zzz.no.such.setting")

    assert not result.get("error"), "Dataverse succeeded; this is not an error envelope"
    assert result["normalized"] is True
    assert result["setting_found"] is False
    assert "setting_value" not in result, (
        "no value may be reported for a setting that does not exist"
    )
    assert "zzz.no.such.setting" in result["message"]
    assert result["raw_response"] == {"SettingDetail": None}


@pytest.mark.asyncio
async def test_absent_and_falsy_are_distinguishable_by_setting_found() -> None:
    """The whole point: 'false' and 'no such setting' must not collapse together."""
    exists, _ = await _run(_detail("false"), setting_name="Some.Setting")
    missing, _ = await _run({"SettingDetail": None}, setting_name="Some.Setting")

    assert exists["setting_found"] is True and missing["setting_found"] is False
    assert "setting_value" in exists and "setting_value" not in missing


@pytest.mark.asyncio
async def test_unexpected_shape_omits_the_value_rather_than_guessing() -> None:
    """Two unnamed scalars are ambiguous: no value is reported, the body is."""
    body = {"@odata.context": "ctx", "Alpha": "one", "Beta": "two"}

    result, _urls = await _run(body, setting_name="Some.Setting")

    assert result["normalized"] is False
    assert "setting_value" not in result
    assert result["raw_response"] == {"Alpha": "one", "Beta": "two"}
    assert "unset" in result["message"]


def test_extractor_tiers() -> None:
    """Confirmed container first, then the defensive top-level fallback."""
    # Tier 1 — the live shape.
    assert _extract_setting_value(
        {"SettingDetail": {"Name": "n", "Value": "false", "DataType": 2}}
    ) == ("SettingDetail.Value", "false")
    # Tier 1 is authoritative once the container is present: an unrecognized
    # container must NOT let a stray top-level scalar be reported as the value.
    assert _extract_setting_value(
        {"SettingDetail": {"Name": "n"}, "Value": "stray"}
    ) is None
    # An explicitly null container is absence, not a value.
    assert _extract_setting_value({"SettingDetail": None}) is None
    assert _setting_is_absent({"SettingDetail": None}) is True
    assert _setting_is_absent({"SettingDetail": {"Value": "x"}}) is False
    assert _setting_is_absent({"Value": "x"}) is False
    # Tier 2 — defensive fallback for a body with no container at all.
    assert _extract_setting_value({"SettingValue": "x"}) == ("SettingValue", "x")
    # A lone differently-named scalar is unambiguous and is accepted with its source.
    assert _extract_setting_value({"Whatever": 7}) == ("Whatever", 7)
    assert _extract_setting_value({"Value": None}) is None
    assert _extract_setting_value(["not", "an", "object"]) is None


def test_detail_metadata_omits_what_it_cannot_vouch_for() -> None:
    """Missing or wrongly typed siblings are omitted, never reported as null."""
    assert _extract_setting_detail_metadata(
        {"SettingDetail": {"Name": "n", "Value": "v", "DataType": 2}}
    ) == {"setting_detail_name": "n", "setting_data_type": 2}
    assert _extract_setting_detail_metadata(
        {"SettingDetail": {"Value": "v", "DataType": "two"}}
    ) == {}
    assert _extract_setting_detail_metadata({"SettingDetail": None}) == {}
    assert _extract_setting_detail_metadata("not-a-dict") == {}


@pytest.mark.parametrize(
    "bad_name",
    [
        "Some.Setting')/WhoAmI(",   # OData literal / navigation breakout
        "Some.Setting'",            # bare quote
        "Some.Setting?$select=x",   # inject a query option
        "Some.Setting/../whoami",   # path traversal
        "Some.Setting\r\nX: y",     # header injection
        "",
        "s" * 101,
    ],
)
def test_breakout_setting_names_are_rejected_before_any_url_is_built(
    bad_name: str,
) -> None:
    """Hostile names are refused by the model; encode_odata_literal is layer two."""
    with pytest.raises(ValidationError):
        GetSettingInput(dataverse_url=_BASE_URL, setting_name=bad_name)
    with pytest.raises(ValidationError):
        GetSettingInput(
            dataverse_url=_BASE_URL,
            setting_name="Some.Setting",
            app_unique_name=bad_name,
        )
