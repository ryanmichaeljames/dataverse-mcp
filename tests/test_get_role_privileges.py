"""Unit tests for dataverse_get_role_privileges.

``RetrieveRolePrivilegesRole`` is an unbound GET taking one ``Edm.Guid``
parameter. Its return type ``RetrieveRolePrivilegesRoleResponse`` is documented,
but its INNER properties are not, so the tool locates the privilege collection by
shape as well as by name and degrades to raw pass-through when it cannot.

Live verification established the real payload: one top-level ``RolePrivileges``
list whose every entry already carries ``PrivilegeName``, ``PrivilegeId``,
``Depth`` (an enum MEMBER NAME, e.g. ``"Global"``), ``BusinessUnitId``,
``RecordFilterId`` and ``RecordFilterUniqueName``. There is therefore no
name-resolution step and no second request: the tool is exactly one round trip.

Acceptance criteria:
- Happy path: the ``RolePrivileges`` collection is found, the list is trimmed to
  ``top`` while ``total_count``/``has_more`` keep the true magnitude, the
  ``@odata`` envelope is stripped, entries are passed through untouched, and the
  Guid is written bare in the URL.
- Exactly one HTTP request is made, whatever the payload or ``top``.
- ``depth_summary`` is computed over ALL entries, not just the returned page, and
  passes the ``Depth`` value through without relabelling it.
- An unexpected shape degrades to ``normalized: false`` with raw pass-through and
  no fabricated counts.
- A nonexistent role id is an HTTP 404, surfaced through the standard error
  envelope — not an empty privilege list.
- An invalid GUID is refused at the input model, so no URL is ever built.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import GetRolePrivilegesInput
from dataverse_mcp.tools.security import dataverse_get_role_privileges

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_ROLE_ID = "7c2f0e6a-6b71-4a35-9a5e-3b1f4a2c8d90"

# The exact URL the tool is expected to build: one parameter alias holding a bare
# Edm.Guid literal.
_EXPECTED_URL = f"{_API_ROOT}/RetrieveRolePrivilegesRole(RoleId=@rid)?@rid={_ROLE_ID}"


def _priv_id(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


def _entries(count: int, *, depth: Any = "Global") -> list[dict]:
    """Entries in the shape a live org actually returns: all six keys, every time."""
    rows: list[dict[str, Any]] = []
    for index in range(count):
        rows.append({
            "BusinessUnitId": _priv_id(9000),
            "Depth": depth,
            "PrivilegeId": _priv_id(index),
            "PrivilegeName": f"prvRead{index}",
            "RecordFilterId": _priv_id(0),
            "RecordFilterUniqueName": "",
        })
    return rows


def _envelope(body: Any) -> Any:
    if not isinstance(body, dict):
        return body
    return {"@odata.context": f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.x", **body}


def _make_ctx() -> MagicMock:
    app_ctx = AppContext(
        credential=None,
        auth_type="azure_cli",
        http_client=MagicMock(spec=httpx.AsyncClient),
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context = app_ctx
    return ctx


async def _run(
    body: Any,
    *,
    status_code: int = 200,
    **kwargs: Any,
) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = GetRolePrivilegesInput(dataverse_url=_BASE_URL, role_id=_ROLE_ID, **kwargs)

    async def _side_effect(_client, _method, url, **_kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(status_code, json=body, request=request)

    with patch(
        "dataverse_mcp.tools.security.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.security.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_get_role_privileges(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_happy_path_trims_the_list_and_builds_a_bare_guid_url() -> None:
    """A thousand-privilege role returns 50 entries but reports all 1000."""
    result, urls = await _run(_envelope({"RolePrivileges": _entries(1000)}))

    assert "error" not in result
    assert result["normalized"] is True
    assert result["privileges_source"] == "RolePrivileges"
    assert result["count"] == 50
    assert result["total_count"] == 1000
    assert result["has_more"] is True
    assert len(result["privileges"]) == 50
    # The entries themselves are passed through untouched — nothing is added,
    # renamed or dropped, and all six live keys survive.
    assert result["privileges"][0] == _entries(1)[0]
    assert sorted(result["privileges"][0]) == [
        "BusinessUnitId",
        "Depth",
        "PrivilegeId",
        "PrivilegeName",
        "RecordFilterId",
        "RecordFilterUniqueName",
    ]
    assert "1000 privileges" in result["message"]

    assert urls == [_EXPECTED_URL], "the tool is exactly one round trip"
    # An Edm.Guid literal is bare: no quotes, no guid'...' prefix, nothing encoded.
    assert "'" not in urls[0]
    assert "guid" not in urls[0]
    assert "%" not in urls[0]


@pytest.mark.asyncio
async def test_depth_summary_covers_every_entry_and_is_not_relabelled() -> None:
    """The breakdown spans all 1000 entries even though only 50 are returned."""
    body = {"RolePrivileges": _entries(900) + _entries(100, depth=1)}
    result, _ = await _run(_envelope(body))

    assert result["count"] == 50
    assert result["depth_summary"] == {"Global": 900, "1": 100}, (
        "depths must be counted across the whole collection and reported raw — a "
        "numeric PrivilegeDepth code must not be turned into a guessed label"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="default-top"),
        pytest.param({"top": 1000}, id="max-top"),
    ],
)
async def test_the_tool_is_always_exactly_one_round_trip(kwargs: dict) -> None:
    """PrivilegeName ships with every entry, so nothing is ever looked up."""
    result, urls = await _run(_envelope({"RolePrivileges": _entries(120)}), **kwargs)

    assert urls == [_EXPECTED_URL]
    assert result["privileges"] == _entries(120)[: kwargs.get("top", 50)]
    assert all("PrivilegeName" in entry for entry in result["privileges"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,source",
    [
        pytest.param([{"PrivilegeId": _priv_id(0)}], "<response body>", id="bare-array"),
        pytest.param(
            {"Privileges": [{"PrivilegeId": _priv_id(0)}]},
            "Privileges",
            id="lone-top-level-list-under-another-name",
        ),
        pytest.param(
            {"RolePrivilegeCollection": {"Items": [{"PrivilegeId": _priv_id(0)}]}},
            "RolePrivilegeCollection.Items",
            id="nested-one-level-deep",
        ),
        pytest.param({"RolePrivileges": []}, "RolePrivileges", id="empty-collection"),
    ],
)
async def test_the_collection_is_located_by_shape_when_the_name_differs(
    body: Any, source: str
) -> None:
    """Tiers 2 and 3 cover the shapes tools 2 and 3 actually met live.

    Tier 1 is what fires against a real org; these fallbacks are insurance against
    a future platform change and must never fabricate a collection.
    """
    result, _ = await _run(_envelope(body))

    assert result["normalized"] is True
    assert result["privileges_source"] == source
    assert result["count"] == result["total_count"] == len(result["privileges"])
    assert result["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,reason",
    [
        pytest.param(
            {"A": [{"x": 1}], "B": [{"y": 2}]},
            "two candidate lists at the top level — which one is the privileges?",
            id="ambiguous-top-level",
        ),
        pytest.param(
            {"Outer": {"A": [{"x": 1}], "B": [{"y": 2}]}},
            "two candidate lists one level down",
            id="ambiguous-nested",
        ),
        pytest.param(
            {"RolePrivileges": ["prvReadAccount", "prvWriteAccount"]},
            "a list of scalars is not a collection of privilege entries",
            id="list-of-scalars",
        ),
        pytest.param({"Count": 12}, "no list anywhere", id="no-list"),
        pytest.param("OK", "a bare string payload", id="scalar"),
        pytest.param(None, "an empty body that is not JSON at all", id="empty-body"),
    ],
)
async def test_unexpected_shapes_degrade_to_raw_pass_through(
    body: Any, reason: str
) -> None:
    """No counts are better than fabricated ones."""
    result, urls = await _run(_envelope(body))

    assert "error" not in result, reason
    assert result["normalized"] is False, reason
    for key in ("count", "total_count", "has_more", "privileges", "depth_summary"):
        assert key not in result, f"{key} was fabricated: {reason}"
    assert result["role_id"] == _ROLE_ID
    expected = (
        {k: v for k, v in body.items() if not k.startswith("@odata.")}
        if isinstance(body, dict)
        else body
    )
    assert result["raw_response"] == expected
    assert urls == [_EXPECTED_URL]


@pytest.mark.asyncio
async def test_http_errors_use_the_standard_error_envelope() -> None:
    """A nonexistent role id is an HTTP 404, not an empty privilege list.

    Verified live: Dataverse answers ``[0x80040217] Entity 'role' With Id = ...
    Does Not Exist``.
    """
    result, _ = await _run(
        {
            "error": {
                "code": "0x80040217",
                "message": (
                    "Entity 'role' With Id = 00000000-0000-0000-0000-000000000001 "
                    "Does Not Exist"
                ),
            }
        },
        status_code=404,
    )

    assert result["error"] is True
    assert "404" in result["message"]
    assert "Does Not Exist" in result["message"]
    assert "privileges" not in result
    assert "raw_response" not in result


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param({"role_id": "not-a-guid"}, "a non-GUID role_id", id="bad-guid"),
        pytest.param({"role_id": _ROLE_ID[:-1]}, "a truncated GUID", id="short-guid"),
        pytest.param(
            {"role_id": f"{_ROLE_ID})/WhoAmI("},
            "a GUID with a URL-breakout suffix — the pattern is the only defence",
            id="guid-breakout",
        ),
        pytest.param({"role_id": _ROLE_ID, "top": 0}, "top below 1", id="top-too-low"),
        pytest.param(
            {"role_id": _ROLE_ID, "top": 1001}, "top above the cap", id="top-too-high"
        ),
        pytest.param(
            {"role_id": _ROLE_ID, "unknown": 1},
            "extra='forbid' on the input model",
            id="unknown-field",
        ),
    ],
)
def test_input_model_rejects_bad_input(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    with pytest.raises(ValidationError):
        GetRolePrivilegesInput(dataverse_url=_BASE_URL, **kwargs)
