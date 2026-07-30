"""Live integration coverage for dataverse_retrieve_access_origin.

``RetrieveAccessOrigin`` is an unbound GET taking THREE parameters of TWO OData
types — ``ObjectId``/``PrincipalId`` (``Edm.Guid``, written bare) and
``LogicalName`` (``Edm.String``, written as a quoted, escaped literal). Microsoft
Learn documents the call and the return TYPE (``RetrieveAccessOriginResponse``)
but **not its inner properties**; a live run recorded them, and this suite now
DEFENDS what it found rather than merely printing it:

* the mixed bare-Guid / quoted-string URL is accepted — HTTP 200;
* the body is exactly ``{"Response": "<string>"}`` once the ``@odata.*`` envelope
  is stripped: ONE scalar string property, never a collection, so the tool
  reports ``access_origin_source == "Response"`` and never a ``count``;
* **a nonexistent record id is HTTP 200, not a 404** — the platform's "Does Not
  Exist" text arrives INSIDE the ``Response`` string. That is the trap: a
  successful call does not mean the principal has access, and the three outcomes
  (access / no access / no such record) differ only by English prose;
* an unknown but grammar-valid ``logical_name`` is a clean HTTP 400
  ``[0x80041102] … was not found in the MetadataCache`` — which is also the live
  proof that the quoted ``Edm.String`` literal round-trips as a LITERAL and not
  as URL structure.

No id and no org URL is hardcoded: the caller's own user id comes from
``WhoAmI`` and every record id is discovered live. The org host and every GUID
are redacted before anything is printed.

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
from dataverse_mcp.models import RetrieveAccessOriginInput
from dataverse_mcp.tools.security import dataverse_retrieve_access_origin

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# The one confirmed property name of RetrieveAccessOriginResponse.
_RESPONSE_KEY = "Response"

# Tables guaranteed to exist and to hold at least one row in every org, given as
# (entity set name, singular logical name, primary key column). Both are
# ORGANIZATION-owned, which is why the answer they give is the same for every
# principal: ownership resolves at organization level. That is correct platform
# behaviour, not a defect — discrimination between principals shows up on user-
# and team-owned rows, which cannot be guaranteed to exist in an arbitrary org.
_PROBE_TABLES = [
    ("solutions", "solution", "solutionid"),
    ("roles", "role", "roleid"),
]

# A well-formed GUID that cannot name a real row.
_ABSENT_GUID = "00000000-0000-0000-0000-000000000001"

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


async def _caller_user_id() -> str:
    """The calling user's own systemuserid, from WhoAmI — never hardcoded."""
    response = await _get("/WhoAmI")
    assert response.status_code == 200, (
        f"WhoAmI failed — HTTP {response.status_code}: {_redact(response.text[:500])}"
    )
    user_id = response.json().get("UserId")
    assert isinstance(user_id, str) and user_id, "WhoAmI returned no UserId"
    return user_id


async def _first_record(entity_set: str, primary_key: str) -> str | None:
    """One id from a table the caller can read. ``$filter`` is not used or needed.

    (Data-plane ``$filter`` works fine in this org; it is ``EntityDefinitions`` on
    the schema plane that rejects any ``$filter`` with HTTP 400 ``[0x80060888]``.)
    """
    response = await _get(f"/{entity_set}?$select={primary_key}&$top=1")
    if response.status_code != 200:
        print(
            f"\n=== {entity_set} discovery returned HTTP {response.status_code}: "
            f"{_redact(response.text[:300])} ==="
        )
        return None
    rows = response.json().get("value", [])
    return rows[0].get(primary_key) if rows else None


async def _probe() -> tuple[str, str, str]:
    """(logical_name, object_id, principal_id) for a record the caller can read."""
    principal_id = await _caller_user_id()
    for entity_set, logical_name, primary_key in _PROBE_TABLES:
        record_id = await _first_record(entity_set, primary_key)
        if record_id:
            print(
                f"\n=== probing '{logical_name}' (record id and principal id "
                "redacted) ==="
            )
            return logical_name, record_id, principal_id
    pytest.skip("no readable row found in any probe table")


def _built_url(object_id: str, logical_name: str, principal_id: str) -> str:
    """Exactly the URL src/dataverse_mcp/tools/security.py builds."""
    base_url = resolve_base_url(_url())
    return (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/RetrieveAccessOrigin(ObjectId=@oid,LogicalName=@ln,PrincipalId=@pid)"
        f"?@oid={object_id}&@ln='{encode_odata_literal(logical_name)}'"
        f"&@pid={principal_id}"
    )


async def _raw_call(object_id: str, logical_name: str, principal_id: str):
    async with httpx.AsyncClient(timeout=60.0) as client:
        return await client.get(
            _built_url(object_id, logical_name, principal_id), headers=_headers()
        )


async def _call(object_id: str, logical_name: str, principal_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_retrieve_access_origin(
                RetrieveAccessOriginInput(
                    dataverse_url=_url(),
                    object_id=object_id,
                    logical_name=logical_name,
                    principal_id=principal_id,
                ),
                ctx,
            )
        )


async def test_raw_call_confirms_the_single_scalar_response_shape() -> None:
    """The body is ``{"Response": "<string>"}`` — one scalar, never a collection.

    This also proves the mixed escaping is right: two bare ``Edm.Guid`` literals
    and one quoted, escaped ``Edm.String`` literal in a single request line.
    """
    logical_name, object_id, principal_id = await _probe()
    response = await _raw_call(object_id, logical_name, principal_id)

    print("\n=== RetrieveAccessOrigin raw HTTP call ===")
    print(f"url (org host + guids redacted): {_redact(_built_url(object_id, logical_name, principal_id))}")
    print(f"status: {response.status_code}")
    print(f"RESPONSE SIZE: {len(response.content)} bytes")
    print(f"RAW BODY (first {_RAW_BODY_PREVIEW} chars):")
    print(_redact(response.text[:_RAW_BODY_PREVIEW]))

    assert response.status_code == 200, (
        "the mixed bare-Guid / quoted-string parameter-alias URL was rejected — "
        f"HTTP {response.status_code}: {_redact(response.text[:1000])}. Check the "
        "parameter names and the escaping of each OData type before changing "
        "anything else."
    )

    body = response.json()
    assert isinstance(body, dict), f"expected an object, got {type(body).__name__}"
    top_level = sorted(k for k in body if not k.startswith("@odata."))
    print(f"top_level_keys (envelope stripped): {top_level}")

    assert top_level == [_RESPONSE_KEY], (
        f"the confirmed shape is a lone {_RESPONSE_KEY!r} property; this org "
        f"returned {top_level}. _extract_access_origin must be taught the new "
        "shape before the tool is trusted."
    )
    assert isinstance(body[_RESPONSE_KEY], str), (
        f"{_RESPONSE_KEY} is a scalar string, not "
        f"{type(body[_RESPONSE_KEY]).__name__} — a collection here would mean the "
        "shape changed"
    )


async def test_tool_explains_access_to_a_readable_record() -> None:
    """The caller asks why they can see a record they demonstrably can see."""
    logical_name, object_id, principal_id = await _probe()
    result = await _call(object_id, logical_name, principal_id)
    _dump("readable record: full tool response", result)

    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["object_id"] == object_id
    assert result["logical_name"] == logical_name
    assert result["principal_id"] == principal_id
    assert result["normalized"] is True, (
        "the response shape was not recognized. The payload printed above is the "
        "record of the real shape; teach _extract_access_origin before trusting "
        "this tool."
    )
    assert result["access_origin_source"] == _RESPONSE_KEY
    assert isinstance(result["access_origin"], str) and result["access_origin"]
    assert "count" not in result, "the answer is never list-shaped"
    # raw_response rides along on every successful path, so the true shape stays
    # checkable by the caller.
    assert result["raw_response"] == {_RESPONSE_KEY: result["access_origin"]}
    # The in-band warning that a successful call is not a grant.
    assert "does NOT mean" in result["message"]

    print(f"RECORDED access_origin: {_redact(result['access_origin'])}")


async def test_absent_record_id_is_http_200_with_text_not_a_404() -> None:
    """THE TRAP: a nonexistent record id succeeds, carrying the fault text inline.

    A caller that reads only the status code — or only ``normalized`` — will
    mistake "there is no such record" for "access confirmed". Both the raw call
    and the tool are asserted, so a future platform change to a real 404 breaks
    here loudly instead of silently changing what the tool means.
    """
    logical_name, _, principal_id = await _probe()

    raw = await _raw_call(_ABSENT_GUID, logical_name, principal_id)
    print(f"\n=== nonexistent record id: raw status {raw.status_code} ===")
    print(_redact(raw.text[:_RAW_BODY_PREVIEW]))
    assert raw.status_code == 200, (
        "a nonexistent record id used to answer HTTP 200 with the 'Does Not "
        f"Exist' text inside the {_RESPONSE_KEY} string; this org answered HTTP "
        f"{raw.status_code}. The tool's docstring and the error handling both "
        "assume 200 — update them together."
    )

    result = await _call(_ABSENT_GUID, logical_name, principal_id)
    _dump("nonexistent record id: full tool response", result)

    assert not result.get("error"), (
        "the tool reported an error for a call Dataverse answered with HTTP 200"
    )
    assert result["normalized"] is True
    assert result["access_origin_source"] == _RESPONSE_KEY
    assert isinstance(result["access_origin"], str) and result["access_origin"]
    assert "raw_response" in result
    print(f"RECORDED absent-record access_origin: {_redact(result['access_origin'])}")


async def test_absent_principal_id_stays_within_the_contract() -> None:
    """The platform does not validate the principal against an org-owned row.

    Live: a nonexistent principal id returned the SAME generic ownership text as
    a real one, because ownership on an organization-owned table resolves at
    organization level. Nothing is asserted about WHICH answer comes back, only
    that the tool answers within its contract — the printed output is the record.
    """
    logical_name, object_id, _ = await _probe()
    result = await _call(object_id, logical_name, _ABSENT_GUID)
    _dump("nonexistent principal id: full tool response", result)

    if result.get("error"):
        print(f"RECORDED absent-principal error message: {_redact(result['message'])}")
        assert isinstance(result["message"], str) and result["message"]
        assert "access_origin" not in result
    else:
        assert result["normalized"] is True
        assert isinstance(result["access_origin"], str)
        assert "raw_response" in result
        print(
            "RECORDED absent-principal access_origin: "
            f"{_redact(result['access_origin'])}"
        )


async def test_unknown_logical_name_is_rejected_by_the_platform_not_by_injection() -> None:
    """A syntactically valid but nonexistent table name is a clean HTTP 400.

    The value passes the input model's identifier grammar, so it really is sent —
    which makes this the live proof that the quoted ``Edm.String`` literal is
    parsed as a literal and not as URL structure. Live-confirmed error:
    ``[0x80041102] … was not found in the MetadataCache``.
    """
    _, object_id, principal_id = await _probe()
    result = await _call(object_id, "zz_no_such_table_zz", principal_id)
    _dump("unknown logical name: full tool response", result)

    assert result.get("error") is True, (
        "an unknown table name was accepted — the LogicalName parameter may not be "
        "reaching Dataverse as sent; read the payload above"
    )
    message = result["message"]
    assert "400" in message, f"expected an HTTP 400, got: {_redact(message)}"
    assert "0x80041102" in message or "MetadataCache" in message, (
        f"expected the MetadataCache lookup failure, got: {_redact(message)}"
    )
    print(f"RECORDED unknown-logical-name error: {_redact(message)}")
