"""Live integration coverage for dataverse_get_setting.

``RetrieveSetting`` is an unbound GET taking a required ``SettingName``
(``Edm.String``) and an OPTIONAL ``AppUniqueName`` (``Edm.String``).

These shapes are now LIVE-CONFIRMED and are ASSERTED here, not merely recorded —
an earlier round of this file only printed them, which let a tool that never once
normalized against a real org pass its whole suite:

* both URL forms return HTTP 200 — the ``SettingName``-only form that omits
  ``AppUniqueName`` from the signature AND its alias entirely, and the
  two-parameter form with both single-quoted aliases;
* the value is NESTED: ``{"SettingDetail": {"Name": ..., "Value": "false",
  "DataType": 2}}``. ``Value`` is a STRING (``"false"``, not a JSON boolean) and
  ``DataType`` is an integer code the tool passes through unmapped;
* an unknown setting name is NOT an error — HTTP 200 with ``SettingDetail: null``,
  which the tool reports as ``setting_found: false``.

No setting name or app name is hardcoded: both are discovered live from
``settingdefinitions`` and ``appmodules``. An org whose settings framework is not
provisioned — the collection may not exist at all — SKIPS with a clear reason
rather than failing, as does an org with no matching setting.

A setting is configuration rather than a secret store, so setting values ARE
printed — but only through ``_scrub``, which strips the org host, every GUID,
every URL, every ``key=secret`` pair and every long opaque token first. A setting
``Value`` is a free-form string and nothing stops an administrator storing a
credential in one.

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

from dataverse_mcp.client import (
    _DATAVERSE_API_VERSION,
    AppContext,
    encode_odata_literal,
    resolve_base_url,
)
from dataverse_mcp.models import GetSettingInput
from dataverse_mcp.tools.environments import dataverse_get_setting

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Any absolute URL, not just this org's — a setting value can name an endpoint.
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# key=secret pairs, the shape a connection string leaks through.
_SECRET_PAIR_RE = re.compile(
    r"((?:password|pwd|secret|accountkey|sharedaccesskey|apikey|api_key|token|"
    r"clientsecret|client_secret|sig)\s*[=:]\s*)[^\s;,\"']+",
    re.IGNORECASE,
)

# A long run of opaque token characters — base64/hex keys and bearer tokens.
_OPAQUE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9+/_-]{32,}={0,2}\b")

_PAGE_SIZE = 50
_RAW_BODY_PREVIEW = 2000

# The confirmed RetrieveSetting container and the property the value sits under.
_DETAIL_KEY = "SettingDetail"
_VALUE_KEY = "Value"

# A name no org will define, used to exercise the unknown-name path.
_UNKNOWN_SETTING_NAME = "zzz.no.such.setting.zzz"

# The model's grammar for a setting / app unique name. Discovered names that do
# not match are skipped rather than being fed to a model that will reject them —
# a discovery artefact must not read as a tool failure.
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")

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


def _scrub(text: str) -> str:
    """``_redact`` plus anything that reads like a credential or an endpoint.

    A setting ``Value`` is a free-form string, so the org host and GUIDs are not
    the only things worth keeping off the console: URLs, ``key=secret`` pairs and
    long opaque tokens all go too. Order matters — the secret pairs and URLs are
    collapsed BEFORE the opaque-token sweep, so a scrubbed placeholder cannot be
    scrubbed twice into something unreadable.
    """
    text = _redact(text)
    text = _SECRET_PAIR_RE.sub(r"\1<redacted-secret>", text)
    text = _URL_RE.sub("<redacted-url>", text)
    return _OPAQUE_TOKEN_RE.sub("<redacted-token>", text)


def _dump(label: str, payload: Any) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(f"\n=== {label} ({len(text)} bytes as printed) ===")
    print(_scrub(text))


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


async def _setting_names() -> list[str]:
    """Discover setting unique names live — no name is ever hardcoded.

    ``settingdefinitions`` is a data-plane collection, so ``$select`` / ``$top``
    behave normally (unlike ``EntityDefinitions`` on the schema plane, which this
    org rejects any ``$filter`` on with HTTP 400 ``[0x80060888]``). The collection
    may not exist on every org — the settings framework is not universally
    provisioned — so a non-200 is reported and treated as "nothing to test",
    never as a failure.
    """
    response = await _get(f"/settingdefinitions?$select=uniquename&$top={_PAGE_SIZE}")
    if response.status_code != 200:
        print(
            "\n=== settingdefinitions discovery returned HTTP "
            f"{response.status_code}: {_redact(response.text[:300])} ==="
        )
        return []
    rows = response.json().get("value", [])
    names = [
        row["uniquename"]
        for row in rows
        if isinstance(row.get("uniquename"), str) and _NAME_RE.match(row["uniquename"])
    ]
    # RECORDED live: a stock org exposes 112 settingdefinitions rows, so the
    # settings surface this tool reads is real and not a vestigial table. The page
    # size caps what is fetched here, so this is a floor, not the total.
    print(
        f"\n=== discovered {len(rows)} settingdefinitions row(s) in one page of "
        f"{_PAGE_SIZE}; {len(names)} match the tool's name grammar ==="
    )
    print(f"unique names: {sorted(names)[:20]}")
    return sorted(names)


async def _require_setting_name() -> str:
    names = await _setting_names()
    if not names:
        pytest.skip(
            "this org exposes no settingdefinitions rows whose uniquename matches "
            "the tool's grammar, so there is no setting to read"
        )
    return names[0]


async def _app_unique_name() -> str | None:
    """Discover a model-driven app unique name live, or None when there is none."""
    response = await _get(f"/appmodules?$select=uniquename,name&$top={_PAGE_SIZE}")
    if response.status_code != 200:
        print(
            f"\n=== appmodules discovery returned HTTP {response.status_code} ==="
        )
        return None
    names = [
        row["uniquename"]
        for row in response.json().get("value", [])
        if isinstance(row.get("uniquename"), str) and _NAME_RE.match(row["uniquename"])
    ]
    print(f"\n=== discovered {len(names)} model-driven app(s) ===")
    print(f"unique names: {sorted(names)[:20]}")
    return sorted(names)[0] if names else None


async def _call(**kwargs: Any) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_get_setting(
                GetSettingInput(dataverse_url=_url(), **kwargs), ctx
            )
        )


async def test_both_url_forms_return_the_setting_detail_container() -> None:
    """Call BOTH URL forms with the exact URLs the tool builds and ASSERT the shape.

    Live-confirmed and therefore asserted, not merely printed: both forms answer
    HTTP 200, and the payload is a single ``SettingDetail`` container — the value
    is nested inside it, never a top-level property. A regression here is exactly
    the defect that made the tool return ``normalized: false`` on every call.
    """
    setting_name = await _require_setting_name()
    app_name = await _app_unique_name()

    base_url = resolve_base_url(_url())
    api_root = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
    forms = {
        "organization (AppUniqueName and its alias omitted)": (
            f"{api_root}/RetrieveSetting(SettingName=@s)"
            f"?@s='{encode_odata_literal(setting_name)}'"
        ),
    }
    if app_name is not None:
        forms["app (both parameters, both quoted literals)"] = (
            f"{api_root}/RetrieveSetting(SettingName=@s,AppUniqueName=@a)"
            f"?@s='{encode_odata_literal(setting_name)}'"
            f"&@a='{encode_odata_literal(app_name)}'"
        )
    else:
        print(
            "\n=== this org has no model-driven app, so the two-parameter URL form "
            "stays unverified ==="
        )

    for label, url in forms.items():
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=_headers())

        print(f"\n=== RetrieveSetting raw HTTP call — {label} ===")
        print(f"url (org host redacted): {_redact(url)}")
        print(f"status: {response.status_code}")
        print(f"RESPONSE SIZE: {len(response.content)} bytes")
        print(f"RAW BODY (first {_RAW_BODY_PREVIEW} chars, scrubbed):")
        print(_scrub(response.text[:_RAW_BODY_PREVIEW]))

        assert response.status_code == 200, (
            f"the {label} URL form was rejected — HTTP {response.status_code}: "
            f"{_scrub(response.text[:1000])}. Both forms are live-confirmed to "
            "return 200, so this is a regression in the URL build or a privilege "
            "change, not an expected outcome."
        )

        body = response.json()
        top_level = sorted(k for k in body if not k.startswith("@odata."))
        print(f"top_level_keys (envelope stripped): {top_level}")
        assert top_level == [_DETAIL_KEY], (
            f"RetrieveSetting no longer answers with a single {_DETAIL_KEY} "
            f"container — top-level keys were {top_level}. _extract_setting_value's "
            "tier 1 reads SettingDetail.Value; update it and the docstring together."
        )

        detail = body[_DETAIL_KEY]
        assert detail is None or isinstance(detail, dict), (
            f"{_DETAIL_KEY} was {type(detail).__name__}; the tool expects an object "
            "(the setting) or null (no such setting)"
        )
        if isinstance(detail, dict):
            print(
                "detail property types: "
                + json.dumps(
                    {k: type(v).__name__ for k, v in detail.items()}, sort_keys=True
                )
            )
            assert _VALUE_KEY in detail, (
                f"{_DETAIL_KEY} carried {sorted(detail)} but no {_VALUE_KEY} — the "
                "value has moved and the extractor must follow it"
            )


async def test_organization_scope_normalizes_the_nested_value() -> None:
    """The org-scope form lifts the value out of SettingDetail — every time.

    This assertion is the regression guard for the defect this test file missed
    first time round: the tool returned ``normalized: false`` on 100% of live
    calls because it only looked at the top level of the envelope.
    """
    setting_name = await _require_setting_name()

    result = await _call(setting_name=setting_name)
    _dump(f"dataverse_get_setting({setting_name}) — organization scope", result)

    if result.get("error"):
        pytest.skip(
            "Dataverse refused to read this setting for the calling user: "
            f"{result.get('message')}. A privilege-gated or app-only setting is "
            "not a tool failure; nothing further can be asserted."
        )

    assert result["setting_name"] == setting_name
    assert result["scope"] == "organization"
    assert result["app_unique_name"] is None
    assert "raw_response" in result, "raw_response must ride along on EVERY path"
    assert result["normalized"] is True, (
        "a discovered setting name must normalize. normalized: false here means "
        "the value is no longer at SettingDetail.Value — read raw_response above"
    )
    assert result["setting_found"] is True, (
        f"{setting_name} came from settingdefinitions, so it exists; "
        "setting_found: false means the name/uniquename mapping has changed"
    )
    assert result["setting_value_source"] == f"{_DETAIL_KEY}.{_VALUE_KEY}", (
        "the confirmed tier must be the one that matched, not the defensive "
        f"fallback — got {result.get('setting_value_source')!r}"
    )
    assert "setting_value" in result
    # DataType is an integer CODE and must stay one: mapping it to a type name
    # would be an unverified claim.
    if "setting_data_type" in result:
        assert isinstance(result["setting_data_type"], int)
    print(
        "RECORDED setting_value type: "
        f"{type(result['setting_value']).__name__} "
        f"(DataType code: {result.get('setting_data_type')})"
    )


async def test_app_scope_is_a_distinct_request() -> None:
    """Supplying app_unique_name sends the two-parameter form and is scoped as such."""
    setting_name = await _require_setting_name()
    app_name = await _app_unique_name()
    if app_name is None:
        pytest.skip("this org has no model-driven app to scope a setting to")

    result = await _call(setting_name=setting_name, app_unique_name=app_name)
    _dump(f"dataverse_get_setting({setting_name}, {app_name}) — app scope", result)

    if result.get("error"):
        pytest.skip(
            "Dataverse refused the app-scoped read: "
            f"{result.get('message')}. An org-only setting has no app-level value; "
            "that is a platform answer, not a tool failure."
        )

    assert result["scope"] == "app"
    assert result["app_unique_name"] == app_name
    assert "raw_response" in result
    assert result["normalized"] is True, (
        "the app-scoped form returns the same SettingDetail container as the "
        "org-scoped one and must normalize the same way"
    )
    assert "setting_found" in result


async def test_unknown_setting_name_is_a_successful_empty_answer() -> None:
    """An unrecognized name is HTTP 200 with SettingDetail: null — not an error.

    Live-confirmed, and it is the distinction the tool exists to preserve: "no
    such setting" must not read as "the setting is off". The tool reports it as a
    successful, normalized call with setting_found=false and NO setting_value.
    """
    result = await _call(setting_name=_UNKNOWN_SETTING_NAME)
    _dump(f"dataverse_get_setting({_UNKNOWN_SETTING_NAME})", result)

    assert not result.get("error"), (
        "an unknown setting name returns HTTP 200 live, so an error envelope here "
        f"is a change in platform behaviour: {result.get('message')}"
    )
    assert result["normalized"] is True
    assert result["setting_found"] is False, (
        "SettingDetail: null means the setting does not exist; reporting anything "
        "else lets a typo masquerade as a falsy value"
    )
    assert "setting_value" not in result, (
        "no value may be reported for a setting that does not exist"
    )
    assert result["raw_response"] == {_DETAIL_KEY: None}, (
        f"expected the confirmed null container, got {result['raw_response']}"
    )
