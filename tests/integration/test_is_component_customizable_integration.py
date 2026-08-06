"""Live integration coverage for dataverse_is_component_customizable.

The response shape of ``IsComponentCustomizable`` is undocumented on Microsoft
Learn but has been **verified live**: it is flat, carrying exactly one property
named after the function itself —

    {"IsComponentCustomizable": true}

— observed for component types 1 (Entity), 2 (Attribute) and 61 (Web Resource).
This suite pins that shape down and re-checks it on every run: if a future
platform version changes it, the raw-call test is what fails first, and every
test still prints the full raw payload (org host and GUIDs redacted).

What it establishes:

* the payload keys and value types are still the recorded ones;
* the tool really discriminates — a **managed** web resource answers ``false``
  and an **unmanaged** one ``true`` (component type 61), the cheapest pair of
  opposite verdicts an org can offer;
* the system-table nuance: ``systemuser`` (component type 1) answers **true**,
  because core tables permit customization such as adding columns even though the
  base asset is managed. A caller must not read "system table" as "not
  customizable". The value is recorded, not asserted, since it is a platform
  behaviour rather than a tool contract.

No component id is hardcoded: every id is discovered live.

Read-only throughout: nothing is created, updated or published.

Requires (else auto-skipped by tests/integration/conftest.py):
  DATAVERSE_INTEGRATION_URL   — base org URL
  DATAVERSE_INTEGRATION_TOKEN — bearer access token for that org
"""

import json
import os
import re
import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from dataverse_mcp.client import _DATAVERSE_API_VERSION, AppContext, resolve_base_url
from dataverse_mcp.models import IsComponentCustomizableInput
from dataverse_mcp.tools.metadata import dataverse_is_component_customizable

_INTEGRATION_URL_VAR = "DATAVERSE_INTEGRATION_URL"
_INTEGRATION_TOKEN_VAR = "DATAVERSE_INTEGRATION_TOKEN"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Solution component types. 1 = Entity, the one code guaranteed to have a target
# in every org; 61 = Web Resource, where managed and unmanaged examples usually
# both exist and give opposite verdicts.
_ENTITY_COMPONENT_TYPE = 1
_WEB_RESOURCE_COMPONENT_TYPE = 61

_SYSTEM_TABLE = "systemuser"

# The live-verified property name carrying the verdict.
_VERDICT_PROPERTY = "IsComponentCustomizable"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get(_INTEGRATION_URL_VAR),
        reason=f"{_INTEGRATION_URL_VAR} is not set; skipping integration test.",
    ),
]


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
    print(f"\n=== {label} ===")
    print(_redact(json.dumps(payload, indent=2, sort_keys=True, default=str)))


def _make_live_ctx(client: httpx.AsyncClient) -> MagicMock:
    """MCPServer-style ctx backed by an AppContext pre-seeded with the sandbox token."""
    base_url = resolve_base_url(os.environ[_INTEGRATION_URL_VAR])
    token = os.environ[_INTEGRATION_TOKEN_VAR]
    app_ctx = AppContext(credential=None, auth_type="azure_cli", http_client=client)
    app_ctx._token_cache[f"{base_url}/.default"] = (token, time.time() + 3600)
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _get_json(path: str) -> dict:
    """GET a path under the API root and return the parsed body."""
    base_url = resolve_base_url(_url())
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            f"{base_url}/api/data/{_DATAVERSE_API_VERSION}{path}", headers=_headers()
        )
    assert response.status_code == 200, (
        f"discovery call failed — HTTP {response.status_code}: "
        f"{_redact(response.text[:500])}"
    )
    return response.json()


async def _system_table_metadata_id() -> str:
    """Discover the MetadataId of a table every org has."""
    body = await _get_json(
        f"/EntityDefinitions(LogicalName='{_SYSTEM_TABLE}')?$select=MetadataId,IsManaged"
    )
    print(f"\n=== discovered {_SYSTEM_TABLE} (IsManaged={body.get('IsManaged')}) ===")
    return body["MetadataId"]


async def _custom_table() -> tuple[str, str] | None:
    """Discover an unmanaged custom table, or None if the org has none.

    ``EntityDefinitions`` rejects **any** ``$filter`` on at least some orgs with
    HTTP 400 ``[0x80060888] The query parameter ... is not supported``, so the
    collection is fetched with a narrow ``$select`` (the full definition set runs
    to hundreds of entries) and filtered client-side instead.
    """
    body = await _get_json(
        "/EntityDefinitions"
        "?$select=LogicalName,MetadataId,IsCustomEntity,IsManaged"
    )
    rows = body.get("value") or []
    print(f"\n=== EntityDefinitions returned {len(rows)} entries (filtered client-side) ===")
    matches = [
        row
        for row in rows
        if row.get("IsCustomEntity") is True and row.get("IsManaged") is False
    ]
    if not matches:
        return None
    return matches[0]["MetadataId"], matches[0]["LogicalName"]


def _record_says_customizable(row: dict) -> bool | None:
    """Read a record's own ``iscustomizable`` managed property, if it is readable.

    The Web API serializes a BooleanManagedProperty as
    ``{"Value": bool, "CanBeChanged": bool, ...}``; a plain bool is accepted too.
    None means "cannot tell", never a guessed verdict.
    """
    value = row.get("iscustomizable")
    if isinstance(value, bool):
        return value
    if isinstance(value, dict) and isinstance(value.get("Value"), bool):
        return value["Value"]
    return None


async def _web_resource(*, managed: bool, customizable: bool) -> tuple[str, str] | None:
    """Discover a web resource by managed state and its own ``iscustomizable``.

    Unlike ``EntityDefinitions`` (schema plane), ``webresourceset`` is a normal
    data-plane collection and does accept ``$filter`` — but ``iscustomizable`` is
    a BooleanManagedProperty, which is not filterable, so a page of candidates is
    narrowed client-side. Returns None when the org has no such record, so the
    caller can skip rather than fail.
    """
    body = await _get_json(
        "/webresourceset?$select=webresourceid,name,ismanaged,iscustomizable"
        f"&$filter=ismanaged eq {str(managed).lower()}&$top=50"
    )
    rows = body.get("value") or []
    match = next(
        (row for row in rows if _record_says_customizable(row) is customizable), None
    )
    if match is None:
        return None
    return match["webresourceid"], match["name"]


async def _check(component_id: str, component_type: int) -> dict:
    """Run the tool live and return the parsed result."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = _make_live_ctx(client)
        return json.loads(
            await dataverse_is_component_customizable(
                IsComponentCustomizableInput(
                    dataverse_url=_url(),
                    component_id=component_id,
                    component_type=component_type,
                ),
                ctx,
            )
        )


def _assert_tool_contract(
    result: dict, component_id: str, component_type: int, type_name: str
) -> None:
    """Assert the tool contract and the live-verified response shape."""
    assert not result.get("error"), f"tool returned an error: {result.get('message')}"
    assert result["component_id"] == component_id
    assert result["component_type"] == component_type
    assert result["component_type_name"] == type_name
    assert "raw_response" in result

    assert result["normalized"] is True, (
        "the verdict could not be identified — the response shape has changed; "
        "read raw_response above before trusting this tool"
    )
    assert isinstance(result["is_customizable"], bool)
    assert result["is_customizable_source"] == _VERDICT_PROPERTY, (
        "the verdict no longer comes from the confirmed property "
        f"{_VERDICT_PROPERTY!r} — the fallback answered instead, so the recorded "
        "shape has drifted"
    )


async def test_is_component_customizable_raw_call_records_the_response_shape() -> None:
    """Call the function directly with the exact URL the tool builds and print it."""
    base_url = resolve_base_url(_url())
    metadata_id = await _system_table_metadata_id()

    # Exactly the URL src/dataverse_mcp/tools/metadata.py builds: parameter
    # aliases, a BARE Edm.Guid literal (no quotes, no guid'...' prefix) and a bare
    # Edm.Int32.
    url = (
        f"{base_url}/api/data/{_DATAVERSE_API_VERSION}"
        f"/IsComponentCustomizable(ComponentId=@cid,ComponentType=@ct)"
        f"?@cid={metadata_id}&@ct={_ENTITY_COMPONENT_TYPE}"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=_headers())

    print("\n=== IsComponentCustomizable raw HTTP call ===")
    print(f"url (org host + guid redacted): {_redact(url)}")
    print(f"status: {response.status_code}")
    print(f"body: {_redact(response.text)}")

    assert response.status_code == 200, (
        "the bare-Guid parameter-alias URL was rejected — "
        f"HTTP {response.status_code}: {_redact(response.text[:1000])}"
    )

    body = response.json()
    top_level = sorted(k for k in body if not k.startswith("@odata."))
    print(f"top_level_keys (envelope stripped): {top_level}")
    print(
        "value_types: "
        + json.dumps({k: type(body[k]).__name__ for k in top_level}, sort_keys=True)
    )
    # The recorded shape: flat, exactly one property, named after the function.
    assert top_level == [_VERDICT_PROPERTY], (
        "the response shape has changed from the recorded flat "
        f"{{{_VERDICT_PROPERTY!r}: bool}} — update the tool docstring and "
        "_extract_verdict in tools/metadata.py"
    )
    assert isinstance(body[_VERDICT_PROPERTY], bool)


async def test_system_table_is_reported_through_the_tool() -> None:
    """A system table — and the nuance that it is customizable anyway."""
    metadata_id = await _system_table_metadata_id()
    result = await _check(metadata_id, _ENTITY_COMPONENT_TYPE)
    _dump(f"{_SYSTEM_TABLE} (system table): full tool response", result)

    _assert_tool_contract(result, metadata_id, _ENTITY_COMPONENT_TYPE, "Entity")
    # Deliberately NOT asserting False: core tables permit customization (adding
    # columns, for instance) even though the base asset is managed — systemuser
    # was recorded as True. The value is recorded, not presumed.
    print(
        f"RECORDED: {_SYSTEM_TABLE} is_customizable={result['is_customizable']} "
        f"(from {result['is_customizable_source']!r})"
    )


async def test_web_resource_pair_shows_the_tool_discriminates() -> None:
    """A managed and an unmanaged web resource must give opposite verdicts.

    This is the cheapest evidence that the tool answers a real question rather
    than echoing a constant: two records of the same component type (61), the same
    call shape, and a false/true split. Each candidate is picked by its OWN
    ``iscustomizable`` managed property, so the tool's verdict is cross-checked
    against the record rather than against an assumption about managed state.
    """
    managed = await _web_resource(managed=True, customizable=False)
    unmanaged = await _web_resource(managed=False, customizable=True)
    if managed is None or unmanaged is None:
        pytest.skip(
            "this org has no locked managed web resource paired with a "
            f"customizable unmanaged one (locked managed={managed is not None}, "
            f"customizable unmanaged={unmanaged is not None}); no opposite-verdict "
            "comparison is possible here"
        )

    managed_id, managed_name = managed
    unmanaged_id, unmanaged_name = unmanaged

    managed_result = await _check(managed_id, _WEB_RESOURCE_COMPONENT_TYPE)
    _dump(f"managed web resource {managed_name!r}", managed_result)
    unmanaged_result = await _check(unmanaged_id, _WEB_RESOURCE_COMPONENT_TYPE)
    _dump(f"unmanaged web resource {unmanaged_name!r}", unmanaged_result)

    for result, component_id in (
        (managed_result, managed_id),
        (unmanaged_result, unmanaged_id),
    ):
        _assert_tool_contract(
            result, component_id, _WEB_RESOURCE_COMPONENT_TYPE, "Web Resource"
        )

    assert unmanaged_result["is_customizable"] is True, (
        f"web resource {unmanaged_name!r} says iscustomizable=true on the record "
        "but the tool reported 'not customizable' — the two disagree; inspect "
        "raw_response above"
    )
    assert managed_result["is_customizable"] is False, (
        f"web resource {managed_name!r} says iscustomizable=false on the record "
        "but the tool reported 'customizable' — the two disagree; inspect "
        "raw_response above"
    )
    assert "NOT customizable" in managed_result["message"]


async def test_custom_table_is_reported_through_the_tool() -> None:
    """A custom unmanaged table is the customizable end of the type-1 comparison."""
    custom = await _custom_table()
    if custom is None:
        pytest.skip(
            "no custom unmanaged table in this org (EntityDefinitions was "
            "fetched in full and filtered client-side on "
            "IsCustomEntity/IsManaged); nothing to compare against"
        )
    metadata_id, logical_name = custom

    result = await _check(metadata_id, _ENTITY_COMPONENT_TYPE)
    _dump("custom table: full tool response", result)

    _assert_tool_contract(result, metadata_id, _ENTITY_COMPONENT_TYPE, "Entity")
    print(
        f"RECORDED: custom table {logical_name!r} is_customizable="
        f"{result['is_customizable']} (from {result['is_customizable_source']!r})"
    )
    assert result["is_customizable"] is True, (
        f"unmanaged custom table {logical_name!r} reporting 'not customizable' "
        "means the boolean read from the payload is not the customizability "
        "verdict — inspect raw_response above before trusting this tool"
    )


async def test_unknown_component_id_is_an_actionable_error_not_a_crash() -> None:
    """A well-formed but nonexistent GUID must fail through the error contract.

    Live-observed: HTTP 400 ``[0x80040216] There should be at least one metadata
    entity returned for EntityName: ...``.
    """
    result = await _check("00000000-0000-0000-0000-000000000001", _ENTITY_COMPONENT_TYPE)
    _dump("nonexistent component: full tool response", result)

    assert result.get("error") is True, (
        "a nonexistent component id no longer errors — record the new behaviour "
        "before relaxing this assertion"
    )
    assert isinstance(result["message"], str) and result["message"]
    assert "raw_response" not in result
