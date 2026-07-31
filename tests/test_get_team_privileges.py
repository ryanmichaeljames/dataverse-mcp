"""Unit tests for dataverse_get_team_privileges.

``RetrieveTeamPrivileges`` is an ENTITY-BOUND GET on ``teams`` that takes no
function parameters at all — the team id is the OData key predicate. Its return
type ``RetrieveTeamPrivilegesResponse`` is documented; its INNER properties are
not, and unlike ``dataverse_get_role_privileges`` no live run has pinned them
down. The tool therefore tries two plausible names (``TeamPrivileges``, then
``RolePrivileges`` — the key the sibling ``RetrieveUserPrivileges`` really uses)
and then locates the list by shape, degrading to raw pass-through when it cannot.

Acceptance criteria:
- Happy path: the collection is found, trimmed to ``top`` while ``total_count`` /
  ``has_more`` / ``depth_summary`` keep the true magnitude over the FULL set, the
  ``@odata`` envelope is stripped, and entries are passed through untouched.
- The URL is the bound-function form with a BARE Guid key predicate: nothing
  quoted, nothing percent-encoded, no parameter alias.
- An unexpected shape degrades to ``normalized: false`` with raw pass-through and
  no fabricated counts.
- An HTTP failure uses the standard error envelope.
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
from dataverse_mcp.models import GetTeamPrivilegesInput
from dataverse_mcp.tools.security import dataverse_get_team_privileges

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_TEAM_ID = "1f3d9c58-2a44-4b0e-8c71-6d5e0a9b7c31"

# The exact URL the tool is expected to build: an entity-bound function with a
# bare Guid key predicate and an empty parameter list.
_EXPECTED_URL = (
    f"{_API_ROOT}/teams({_TEAM_ID})/Microsoft.Dynamics.CRM.RetrieveTeamPrivileges()"
)


def _guid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


def _entries(count: int, *, depth: Any = "Global") -> list[dict]:
    """Entries in the shape RetrieveRolePrivilegesRole returns live."""
    return [
        {
            "BusinessUnitId": _guid(9000),
            "Depth": depth,
            "PrivilegeId": _guid(index),
            "PrivilegeName": f"prvRead{index}",
            "RecordFilterId": _guid(0),
            "RecordFilterUniqueName": "",
        }
        for index in range(count)
    ]


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
    body: Any, *, status_code: int = 200, **kwargs: Any
) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = GetTeamPrivilegesInput(dataverse_url=_BASE_URL, team_id=_TEAM_ID, **kwargs)

    async def _side_effect(_client, _method, url, **_kwargs):
        return httpx.Response(status_code, json=body, request=httpx.Request("GET", url))

    with patch(
        "dataverse_mcp.tools.security.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.security.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_get_team_privileges(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_happy_path_trims_and_builds_a_bare_key_predicate_url() -> None:
    """A thousand-privilege team returns 50 entries but reports all 1000."""
    result, urls = await _run(_envelope({"TeamPrivileges": _entries(1000)}))

    assert "error" not in result
    assert result["normalized"] is True
    assert result["privileges_source"] == "TeamPrivileges"
    assert result["team_id"] == _TEAM_ID
    assert result["count"] == 50
    assert result["total_count"] == 1000
    assert result["has_more"] is True
    # Entries pass through untouched — nothing added, renamed or dropped.
    assert result["privileges"] == _entries(1000)[:50]
    assert "1000 privileges" in result["message"]
    # depth_summary spans the WHOLE collection, not the returned page, and the
    # raw Depth value is never relabelled.
    assert result["depth_summary"] == {"Global": 1000}

    assert urls == [_EXPECTED_URL], "the tool is exactly one round trip"
    # A Guid key predicate is bare: no quotes, no guid'...' prefix, no encoding,
    # and no parameter alias — this function takes no parameters.
    assert "'" not in urls[0]
    assert "%" not in urls[0]
    assert "@" not in urls[0]


@pytest.mark.asyncio
async def test_rolepriveleges_key_is_accepted_because_the_name_is_unverified() -> None:
    """The sibling RetrieveUserPrivileges returns RolePrivileges; so may this one."""
    result, _ = await _run(_envelope({"RolePrivileges": _entries(3, depth=1)}))

    assert result["normalized"] is True
    assert result["privileges_source"] == "RolePrivileges"
    assert result["count"] == result["total_count"] == 3
    assert result["has_more"] is False
    assert result["depth_summary"] == {"1": 3}, (
        "a numeric PrivilegeDepth code must be reported raw, never mapped to a "
        "guessed access-level label"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,source",
    [
        pytest.param([{"PrivilegeId": _guid(0)}], "<response body>", id="bare-array"),
        pytest.param(
            {"Privileges": [{"PrivilegeId": _guid(0)}]},
            "Privileges",
            id="lone-top-level-list-under-a-third-name",
        ),
        pytest.param(
            {"TeamPrivilegeCollection": {"Items": [{"PrivilegeId": _guid(0)}]}},
            "TeamPrivilegeCollection.Items",
            id="nested-one-level-deep",
        ),
        pytest.param(
            {"TeamPrivileges": []}, "TeamPrivileges", id="empty-collection"
        ),
    ],
)
async def test_the_collection_is_located_by_shape_when_the_name_differs(
    body: Any, source: str
) -> None:
    """The property name is unverified, so the by-shape tiers are load-bearing."""
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
            "two candidate lists at the top level",
            id="ambiguous-top-level",
        ),
        pytest.param(
            {"TeamPrivileges": ["prvReadAccount"]},
            "a list of scalars is not a collection of privilege entries",
            id="list-of-scalars",
        ),
        pytest.param({"Count": 12}, "no list anywhere", id="no-list"),
        pytest.param(None, "an empty body", id="empty-body"),
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
    assert result["team_id"] == _TEAM_ID
    expected = (
        {k: v for k, v in body.items() if not k.startswith("@odata.")}
        if isinstance(body, dict)
        else body
    )
    assert result["raw_response"] == expected
    assert urls == [_EXPECTED_URL]


@pytest.mark.asyncio
async def test_http_errors_use_the_standard_error_envelope() -> None:
    """A nonexistent team id must not read as a team that grants nothing."""
    result, _ = await _run(
        {
            "error": {
                "code": "0x80040217",
                "message": (
                    "Entity 'team' With Id = 00000000-0000-0000-0000-000000000001 "
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
        pytest.param({"team_id": "not-a-guid"}, "a non-GUID team_id", id="bad-guid"),
        pytest.param({"team_id": _TEAM_ID[:-1]}, "a truncated GUID", id="short-guid"),
        pytest.param(
            {"team_id": f"{_TEAM_ID})/Microsoft.Dynamics.CRM.WhoAmI("},
            "a key-predicate breakout — the GUID pattern is the only defence",
            id="key-predicate-breakout",
        ),
        pytest.param({"team_id": _TEAM_ID, "top": 0}, "top below 1", id="top-too-low"),
        pytest.param(
            {"team_id": _TEAM_ID, "top": 1001}, "top above the cap", id="top-too-high"
        ),
        pytest.param(
            {"team_id": _TEAM_ID, "unknown": 1},
            "extra='forbid' on the input model",
            id="unknown-field",
        ),
    ],
)
def test_input_model_rejects_bad_input(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    with pytest.raises(ValidationError):
        GetTeamPrivilegesInput(dataverse_url=_BASE_URL, **kwargs)
