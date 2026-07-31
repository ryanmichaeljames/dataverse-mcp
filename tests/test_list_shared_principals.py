"""Unit tests for dataverse_list_shared_principals.

The tool merges two unbound functions — ``RetrieveSharedPrincipalsAndAccess`` and
``RetrieveSharedLinks`` — that both take a ``Target`` typed ``crmbaseentity``, an
EntityReference. The Web API passes one as a parameter alias whose value is a
JSON OBJECT holding an ``@odata.id``, which is a FOURTH escaping regime in this
codebase: JSON-encode then percent-encode, and never ``encode_odata_literal``
(doubling quotes would corrupt the JSON).

Acceptance criteria:
- The built URL carries the JSON ``@odata.id`` alias, percent-encoded, with the
  alias name ``@t`` left literal — and no OData single-quoted literal anywhere.
- Happy path: both blocks normalize, are trimmed to ``top``, and report
  ``count`` / ``total_count`` / ``has_more`` over the FULL set.
- An unrecognized payload degrades that block to ``normalized: false`` with raw
  pass-through and no fabricated counts.
- One function failing lands in ``partial_errors`` while the other's data still
  returns; only BOTH failing yields the standard error envelope.
- A breakout ``entity_set_name`` and an invalid ``record_id`` are refused at the
  input model, before any URL is built.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import ListSharedPrincipalsInput
from dataverse_mcp.tools.security import dataverse_list_shared_principals

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_ENTITY_SET = "accounts"
_RECORD_ID = "1f3d9c58-2a44-4b0e-8c71-6d5e0a9b7c31"

# The exact query string the tool is expected to build: the JSON document
# {"@odata.id":"accounts(<guid>)"} percent-encoded, with the alias name @t kept
# literal (urlencode would otherwise turn it into %40t).
_EXPECTED_QUERY = (
    "@t=%7B%22@odata.id%22%3A%22accounts%28"
    "1f3d9c58-2a44-4b0e-8c71-6d5e0a9b7c31%29%22%7D"
)
_PRINCIPALS_URL = (
    f"{_API_ROOT}/RetrieveSharedPrincipalsAndAccess(Target=@t)?{_EXPECTED_QUERY}"
)
_LINKS_URL = f"{_API_ROOT}/RetrieveSharedLinks(Target=@t)?{_EXPECTED_QUERY}"

_PRINCIPALS_FN = "RetrieveSharedPrincipalsAndAccess"
_LINKS_FN = "RetrieveSharedLinks"


def _guid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


def _principals(count: int) -> list[dict]:
    return [
        {
            "AccessMask": "ReadAccess, WriteAccess",
            "Principal": {"LogicalName": "systemuser", "Id": _guid(index)},
        }
        for index in range(count)
    ]


def _links(count: int) -> list[dict]:
    return [
        {"teamid": _guid(1000 + index), "name": f"Access Team {index}"}
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
    principals: Any = None,
    links: Any = None,
    *,
    principals_status: int = 200,
    links_status: int = 200,
    **kwargs: Any,
) -> tuple[dict, list[str]]:
    """Run the tool with a per-function mocked response; return (result, URLs)."""
    params = ListSharedPrincipalsInput(
        dataverse_url=_BASE_URL,
        entity_set_name=_ENTITY_SET,
        record_id=_RECORD_ID,
        **kwargs,
    )

    async def _side_effect(_client, _method, url, **_kwargs):
        if _PRINCIPALS_FN in url:
            body, status = principals, principals_status
        else:
            body, status = links, links_status
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    with patch(
        "dataverse_mcp.tools.security.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.security.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_list_shared_principals(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_happy_path_merges_both_functions_and_builds_the_odata_id_alias() -> None:
    """Both calls succeed, both blocks normalize, and the URL carries JSON."""
    result, urls = await _run(
        _envelope({"PrincipalAccesses": _principals(3)}),
        _envelope({"value": _links(2)}),
    )

    assert "error" not in result
    assert result["entity_set_name"] == _ENTITY_SET
    assert result["record_id"] == _RECORD_ID
    assert result["target"] == f"{_ENTITY_SET}({_RECORD_ID})"
    assert result["partial_errors"] == []

    principals = result["shared_principals"]
    assert principals["normalized"] is True
    assert principals["source"] == "PrincipalAccesses"
    assert principals["count"] == principals["total_count"] == 3
    assert principals["has_more"] is False
    assert principals["principals"] == _principals(3)

    links = result["shared_links"]
    assert links["normalized"] is True
    assert links["source"] == "value"
    assert links["count"] == links["total_count"] == 2
    assert links["links"] == _links(2)

    # Nothing is empty, so the "no share found" caveat must NOT appear.
    assert "message" not in result

    assert urls == [_PRINCIPALS_URL, _LINKS_URL]
    # The alias value is a JSON document: percent-encoded, never a single-quoted
    # OData literal, and the alias NAME survives as a literal '@t'.
    for url in urls:
        assert "@t=%7B%22@odata.id%22" in url
        assert "%40t=" not in url
        assert "'" not in url, "encode_odata_literal must not be applied to JSON"


@pytest.mark.asyncio
async def test_each_list_is_trimmed_but_reports_its_true_magnitude() -> None:
    """Neither function pages server-side, so trimming happens here."""
    result, _ = await _run(
        _envelope({"PrincipalAccesses": _principals(120)}),
        _envelope({"value": _links(80)}),
        top=10,
    )

    for block, key, total in (
        (result["shared_principals"], "principals", 120),
        (result["shared_links"], "links", 80),
    ):
        assert block["count"] == 10
        assert len(block[key]) == 10
        assert block["total_count"] == total
        assert block["has_more"] is True
        assert str(total) in block["message"]


@pytest.mark.asyncio
async def test_an_all_empty_answer_is_qualified_not_declared_private() -> None:
    """No share found is not proof of no access — say so, do not classify it."""
    result, _ = await _run(
        _envelope({"PrincipalAccesses": []}), _envelope({"value": []})
    )

    assert result["shared_principals"]["total_count"] == 0
    assert result["shared_links"]["total_count"] == 0
    assert "not proof" in result["message"]


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
            {"PrincipalAccesses": ["systemuser"]},
            "a list of scalars is not a collection of principal entries",
            id="list-of-scalars",
        ),
        pytest.param({"Count": 2}, "no list anywhere", id="no-list"),
    ],
)
async def test_an_unrecognized_shape_degrades_that_block_to_raw(
    body: Any, reason: str
) -> None:
    """The inner properties are undocumented, so nothing may be fabricated."""
    result, _ = await _run(_envelope(body), _envelope({"value": _links(1)}))

    block = result["shared_principals"]
    assert block["normalized"] is False, reason
    for key in ("count", "total_count", "has_more", "principals"):
        assert key not in block, f"{key} was fabricated: {reason}"
    assert block["raw_response"] == body
    # The other function's data is unaffected.
    assert result["shared_links"]["normalized"] is True
    # An unreadable block must never be counted as "no share found".
    assert "message" not in result
    assert result["partial_errors"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing,surviving,failing_fn",
    [
        pytest.param("links", "shared_principals", _LINKS_FN, id="links-fail"),
        pytest.param(
            "principals", "shared_links", _PRINCIPALS_FN, id="principals-fail"
        ),
    ],
)
async def test_one_function_failing_becomes_a_partial_error(
    failing: str, surviving: str, failing_fn: str
) -> None:
    """RetrieveSharedLinks is the likelier to be gated; it must not fail the tool."""
    statuses = {f"{failing}_status": 403}
    result, urls = await _run(
        _envelope({"PrincipalAccesses": _principals(1)}),
        _envelope({"value": _links(1)}),
        **statuses,
    )

    assert "error" not in result
    assert result[surviving]["normalized"] is True
    assert f"shared_{failing}" not in result, (
        "a failed function must contribute no block at all, not an empty one"
    )
    assert [e["function"] for e in result["partial_errors"]] == [failing_fn]
    assert "403" in result["partial_errors"][0]["message"]
    # Both calls are still attempted; neither short-circuits the other.
    assert urls == [_PRINCIPALS_URL, _LINKS_URL]


@pytest.mark.asyncio
async def test_an_empty_survivor_still_qualifies_itself_when_the_other_failed() -> None:
    """One function down + the other empty must NOT read as "nothing is shared".

    The caveat message still fires here by design — an empty block really is
    empty — but the half of the answer that never arrived is the reason the
    message points at partial_errors rather than declaring the record unshared.
    """
    result, _ = await _run(
        _envelope({"PrincipalAccesses": []}),
        {"error": {"code": "0x0", "message": "denied"}},
        links_status=403,
    )

    assert "error" not in result
    assert result["shared_principals"]["normalized"] is True
    assert result["shared_principals"]["total_count"] == 0
    assert "shared_links" not in result
    assert [e["function"] for e in result["partial_errors"]] == [_LINKS_FN]
    assert "not proof" in result["message"]
    assert "partial_errors" in result["message"], (
        "an all-empty verdict reached with a function missing must send the caller "
        "to partial_errors"
    )


@pytest.mark.asyncio
async def test_both_functions_failing_returns_the_error_envelope() -> None:
    """Only a total failure is an error."""
    result, _ = await _run(
        {"error": {"code": "0x0", "message": "denied"}},
        {"error": {"code": "0x0", "message": "denied"}},
        principals_status=403,
        links_status=403,
    )

    assert result["error"] is True
    assert _PRINCIPALS_FN in result["message"]
    assert _LINKS_FN in result["message"]
    assert "shared_principals" not in result
    assert "shared_links" not in result


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param(
            {"entity_set_name": 'accounts(1)"}/../whoami'},
            'a breakout carrying ", ), / and } — rejected before any URL is built',
            id="entity-set-breakout",
        ),
        pytest.param(
            {"entity_set_name": "accounts?$select=name"},
            "a query-option injection",
            id="entity-set-query-injection",
        ),
        pytest.param(
            {"entity_set_name": "accounts/../whoami"}, "traversal", id="traversal"
        ),
        pytest.param({"entity_set_name": ""}, "an empty name", id="empty-entity-set"),
        pytest.param(
            {"record_id": "not-a-guid"}, "a non-GUID record_id", id="bad-guid"
        ),
        pytest.param(
            {"record_id": f"{_RECORD_ID})/WhoAmI("},
            "a key-predicate breakout inside the @odata.id",
            id="record-id-breakout",
        ),
        pytest.param({"top": 0}, "top below 1", id="top-too-low"),
        pytest.param({"top": 1001}, "top above the cap", id="top-too-high"),
        pytest.param(
            {"unknown": 1}, "extra='forbid' on the input model", id="unknown-field"
        ),
    ],
)
def test_input_model_rejects_bad_input(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    payload = {
        "dataverse_url": _BASE_URL,
        "entity_set_name": _ENTITY_SET,
        "record_id": _RECORD_ID,
        **kwargs,
    }
    with pytest.raises(ValidationError):
        ListSharedPrincipalsInput(**payload)


def test_a_pluralized_max_length_logical_name_is_accepted() -> None:
    """The length bound is the entity SET cap, not the schema-name cap.

    A 50-character logical name is legal, and its plural is 51-52 characters —
    measuring an entity set name against the 50-character schema-name cap would
    reject real tables. The bound still exists, because the value lands in a URL.
    """
    plural = "a" * 50 + "es"
    params = ListSharedPrincipalsInput(
        dataverse_url=_BASE_URL, entity_set_name=plural, record_id=_RECORD_ID
    )
    assert params.entity_set_name == plural

    with pytest.raises(ValidationError):
        ListSharedPrincipalsInput(
            dataverse_url=_BASE_URL,
            entity_set_name="a" * 65,
            record_id=_RECORD_ID,
        )
