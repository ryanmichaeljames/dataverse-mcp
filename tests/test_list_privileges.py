"""Unit tests for dataverse_list_privileges.

The tool reads the ordinary ``privileges`` entity set (never
``RetrievePrivilegeSet``, which ignores every query option) and, when a table is
named, the private ``privilegeobjecttypecodesset`` join table. Three live-proven
traps drive the assertions here:

- ``@odata.count`` CAPS AT 5,000 on ``privileges`` and under-reports, so
  ``total_count`` must come from ``$apply=aggregate($count as c)``;
- ``accessright`` has no option set anywhere, so it is decoded by a hand-rolled
  map — and an unmapped value must be passed through RAW rather than labelled;
- table scoping by privilege NAME is wrong in general, so it must never be built.

A fourth drives the input model: the join table 400s on ``Account`` where
``name_startswith`` is case-insensitive on both routes, so ``table_logical_name``
is lowercased at the boundary and an unknown name is an error, never an empty
list — the two are opposite answers and are asserted separately.

``$skip`` is unsupported on this collection (HTTP 400) and must never appear in
any URL. All HTTP is mocked; no network access.
"""

import json
from typing import Any, get_args
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import ListPrivilegesInput, _AccessRightName
from dataverse_mcp.tools.security import (
    _ACCESS_RIGHT_NAMES,
    dataverse_list_privileges,
)

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"


def _guid(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


def _row(index: int = 0, **overrides: Any) -> dict:
    """A privilege row in the shape the default $select returns."""
    row = {
        "name": f"prvReadThing{index}",
        "accessright": 1,
        "canbebasic": True,
        "canbelocal": True,
        "canbedeep": True,
        "canbeglobal": True,
        "canbeentityreference": False,
        "canbeparententityreference": False,
        "privilegeid": _guid(index),
    }
    row.update(overrides)
    return row


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
    *,
    rows: Any = None,
    body: Any = None,
    total: Any = 7346,
    status_code: int = 200,
    **kwargs: Any,
) -> tuple[dict, list[str]]:
    """Run the tool against mocked responses; return (result, requested URLs).

    *rows* is wrapped in an OData ``value`` envelope; pass *body* instead to serve
    a payload of some other shape. *total* is what the aggregation returns — None
    makes the aggregation fail with HTTP 400.
    """
    params = ListPrivilegesInput(dataverse_url=_BASE_URL, **kwargs)
    data_body = body if rows is None else {"value": rows}

    async def _side_effect(_client, _method, url, **_kwargs):
        if "$apply=" in url:
            if total is None:
                return httpx.Response(
                    400, json={"error": {"message": "no"}},
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                json={"value": [{"@odata.id": None, "c": total}]},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            status_code, json=data_body, request=httpx.Request("GET", url)
        )

    with patch(
        "dataverse_mcp.tools.security.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.security.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_list_privileges(params, _make_ctx()))

    urls = [call.args[2] for call in mock_request.await_args_list]
    # $skip is refused by Dataverse on this collection ("Skip Clause is not
    # supported in CRM"), so it must never be built on ANY route.
    assert not any("skip" in url.lower() for url in urls), urls
    return result, urls


@pytest.mark.asyncio
async def test_catalogue_decodes_access_rights_and_collapses_depths() -> None:
    """The hand-rolled map is the whole point: every value must decode, 0 included."""
    rows = [
        _row(i, accessright=value, name=f"prv{name}")
        for i, (value, name) in enumerate(_ACCESS_RIGHT_NAMES.items())
    ]
    # Depth flags: only real booleans count, and 1 is not True.
    rows[0].update(canbebasic=True, canbelocal=False, canbedeep=1, canbeglobal=None)
    result, urls = await _run(rows=rows, total=7346)

    assert result["normalized"] is True
    assert result["source"] == "privileges"
    decoded = [(p["access_right"], p["access_right_name"]) for p in result["privileges"]]
    assert decoded == list(_ACCESS_RIGHT_NAMES.items())
    assert decoded[0] == (0, "None"), "accessright 0 is a real value, not 'unknown'"
    assert result["privileges"][0]["depths"] == ["Basic"], (
        "canbedeep=1 is an int, not True, and must not become a depth"
    )
    assert result["privileges"][1]["depths"] == ["Basic", "Local", "Deep", "Global"]
    assert "unmapped_access_rights" not in result
    assert urls[0].startswith(f"{_API_ROOT}/privileges?")
    assert "%24select" not in urls[0] and "$select=name,accessright" in urls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,reason",
    [
        pytest.param(8, "bit 3 is unused — the gaps in the map are real", id="unused-bit"),
        pytest.param(3, "a combined mask was never observed live", id="combined"),
        pytest.param(True, "bool is a subclass of int; True must not be ReadAccess", id="bool"),
        pytest.param("1", "a string is not an access right", id="string"),
        pytest.param(None, "a missing column is not an access right", id="null"),
    ],
)
async def test_unknown_access_right_is_reported_raw_with_no_invented_name(
    value: Any, reason: str
) -> None:
    """A wrong access-level label is more dangerous than an unlabelled one."""
    result, _ = await _run(rows=[_row(accessright=value)], total=1)

    entry = result["privileges"][0]
    assert entry["access_right"] == value, reason
    assert "access_right_name" not in entry, reason
    assert result["unmapped_access_rights"] == [str(value)]
    assert "NO access_right_name" in result["message"]


@pytest.mark.asyncio
async def test_total_count_comes_from_the_aggregation_not_the_capped_odata_count() -> None:
    """@odata.count caps at 5000 and lies; only $apply gives the true 7346."""
    result, urls = await _run(rows=[_row(i) for i in range(50)], total=7346, top=50)

    assert result["count"] == 50
    assert result["total_count"] == 7346, "the capped 5000 must never be reported"
    assert result["has_more"] is True
    assert "7346 privileges match" in result["message"]

    assert len(urls) == 2, "one data call plus one aggregation"
    assert "$count" not in urls[0], "$count=true would cap at 5000"
    assert urls[1] == f"{_API_ROOT}/privileges?$apply=aggregate%28$count+as+c%29"


@pytest.mark.asyncio
async def test_a_failed_aggregation_omits_total_count_rather_than_guessing() -> None:
    """No number is better than a number that may be capped."""
    result, _ = await _run(rows=[_row(i) for i in range(3)], total=None, top=3)

    assert result["normalized"] is True
    assert "total_count" not in result
    assert result["count"] == 3
    assert result["has_more"] is True, "a full page with no total means 'maybe more'"
    assert "OMITTED" in result["message"]


@pytest.mark.asyncio
async def test_filters_are_escaped_and_never_interpolate_caller_text_for_access_right() -> None:
    """Quote-doubling then percent-encoding; the Literal becomes an integer."""
    _, urls = await _run(
        rows=[], total=0, name_startswith="prv'Read", access_right="AssignAccess"
    )

    expected = "$filter=startswith%28name,%27prv%27%27Read%27%29+and+accessright+eq+524288"
    assert expected in urls[0], urls[0]
    assert "AssignAccess" not in urls[0], "the Literal is translated, never echoed"
    assert urls[1] == (
        f"{_API_ROOT}/privileges?$apply=filter%28startswith%28name,%27prv%27%27Read"
        "%27%29+and+accessright+eq+524288%29%2Faggregate%28$count+as+c%29"
    )


@pytest.mark.asyncio
async def test_table_scoping_uses_the_join_table_and_never_a_name_filter() -> None:
    """endswith(name,'Role') spans four tables — name matching is never built."""
    rows = [
        {"objecttypecode": "account", "privilegeid": _row(i)} for i in range(8)
    ]
    result, urls = await _run(rows=rows, table_logical_name="account")

    assert result["source"] == "privilegeobjecttypecodesset"
    assert result["table_logical_name"] == "account"
    assert result["count"] == result["total_count"] == 8
    assert result["has_more"] is False
    assert result["privileges"][0]["name"] == "prvReadThing0"

    assert len(urls) == 1, "the join route is exact, so no aggregation is needed"
    assert urls[0].startswith(f"{_API_ROOT}/privilegeobjecttypecodesset?")
    assert "$filter=objecttypecode+eq+%27account%27" in urls[0]
    for forbidden in ("endswith", "startswith", "contains"):
        assert forbidden not in urls[0], "table scoping must not match on the name"


@pytest.mark.asyncio
@pytest.mark.parametrize("given", ["Account", "ACCOUNT", "  AcCoUnT  "])
async def test_table_logical_name_is_lowercased_so_casing_never_400s(given: str) -> None:
    """'Account' 400s live where 'account' works — the model removes that trap.

    name_startswith is deliberately case-insensitive on BOTH routes, so leaving
    table_logical_name case-sensitive was an asymmetry, not a guarantee.
    """
    rows = [{"objecttypecode": "account", "privilegeid": _row(0)}]
    lower, lower_urls = await _run(rows=rows, table_logical_name="account")
    result, urls = await _run(rows=rows, table_logical_name=given)

    assert urls == lower_urls, "casing must not change the URL that is built"
    assert "$filter=objecttypecode+eq+%27account%27" in urls[0]
    assert result == lower
    # The echo reports the normalized value actually queried, not the raw input.
    assert result["table_logical_name"] == "account"


@pytest.mark.asyncio
async def test_join_route_filters_client_side_and_reports_the_filtered_total() -> None:
    """The join table cannot express name/access-right, so the tool does it."""
    rows = [
        {"objecttypecode": "account", "privilegeid": _row(0)},
        {"objecttypecode": "account", "privilegeid": _row(1, name="prvCreateThing")},
        {"objecttypecode": "account", "privilegeid": _row(2, accessright=32)},
    ]
    result, _ = await _run(
        rows=rows, table_logical_name="account", name_startswith="prvread", top=1
    )

    # The prefix match is case-insensitive, matching OData startswith on Dataverse.
    assert result["count"] == 1
    assert result["total_count"] == 2, "counts describe the FILTERED set"
    assert result["has_more"] is True
    assert "client-side" in result["message"]


@pytest.mark.asyncio
async def test_join_route_failure_names_the_route_instead_of_falling_back() -> None:
    """The entity set is private and undocumented — say so, guess nothing."""
    result, _ = await _run(
        body={"error": {"code": "0x0", "message": "Resource not found"}},
        status_code=404,
        table_logical_name="account",
    )

    assert result["error"] is True
    assert "privilegeobjecttypecodesset" in result["message"]
    assert "NOT a valid substitute" in result["message"]
    assert "privileges" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,kwargs,reason",
    [
        pytest.param({"count": 3}, {}, "no value container", id="no-value"),
        pytest.param({"value": {"a": 1}}, {}, "value is not a list", id="value-not-a-list"),
        pytest.param({"value": ["prvReadAccount"]}, {}, "scalars, not rows", id="scalars"),
        pytest.param(None, {}, "an empty body", id="empty-body"),
        pytest.param(
            {"value": [{"objecttypecode": "account"}]},
            {"table_logical_name": "account"},
            "the $expand did not materialise",
            id="join-without-expand",
        ),
    ],
)
async def test_unrecognised_shapes_degrade_to_raw_pass_through(
    body: Any, kwargs: dict, reason: str
) -> None:
    """A missing container is NOT 'no privileges' — never fabricate an empty list."""
    result, _ = await _run(body=body, **kwargs)

    assert "error" not in result, reason
    assert result["normalized"] is False, reason
    for key in ("count", "total_count", "has_more", "privileges"):
        assert key not in result, f"{key} was fabricated: {reason}"
    assert result["raw_response"] == body
    assert "NOT 'no privileges'" in result["message"]


@pytest.mark.asyncio
async def test_an_empty_collection_means_a_valid_table_with_no_privileges() -> None:
    """Zero join rows is a REAL answer — the name was right, not misspelled.

    An unknown name never reaches here: the join table validates objecttypecode
    and 400s (see the test below). ``privilege`` is the live-confirmed example of
    a valid table that maps to no privileges at all.
    """
    result, _ = await _run(rows=[], table_logical_name="privilege")

    assert result["normalized"] is True
    assert result["count"] == result["total_count"] == 0
    assert result["privileges"] == []
    assert "EXISTS and has no privileges" in result["message"]
    assert "spelling" not in result["message"].lower(), (
        "the name WAS correct here — spelling advice belongs on the 400 path"
    )


@pytest.mark.asyncio
async def test_an_unknown_table_name_is_a_400_that_leads_with_the_bad_name() -> None:
    """Live: an unknown/misspelled/plural name 400s with 0x80041102, never empty."""
    result, _ = await _run(
        body={
            "error": {
                "code": "0x80041102",
                "message": (
                    "The entity with a name = 'acount' with namemapping = "
                    "'Logical' was not found in the MetadataCache."
                ),
            }
        },
        status_code=400,
        table_logical_name="acount",
    )

    assert result["error"] is True
    message = result["message"]
    # The bad name leads; the private-entity-set caveat is NOT the diagnosis here.
    assert message.startswith("'acount' is not a table logical name"), message
    assert "0x80041102" in message
    assert "private" not in message, "wrong first-order diagnosis for a typo"
    assert "NOT a valid substitute" in message
    assert "privileges" not in result, "no list is fabricated for a failed lookup"


def test_the_input_literal_and_the_decode_map_stay_in_step() -> None:
    """models.py cannot import the map, so the two are asserted equal here."""
    assert set(get_args(_AccessRightName)) == set(_ACCESS_RIGHT_NAMES.values())
    assert len(set(_ACCESS_RIGHT_NAMES.values())) == len(_ACCESS_RIGHT_NAMES)


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param(
            {"table_logical_name": "account')/privileges("},
            "a literal breakout — the identifier grammar is the first layer",
            id="breakout",
        ),
        pytest.param(
            {"table_logical_name": "my account"},
            "an embedded space is not part of the identifier grammar",
            id="space",
        ),
        pytest.param({"table_logical_name": ""}, "an empty table name", id="empty-table"),
        pytest.param({"name_startswith": ""}, "an empty prefix", id="empty-prefix"),
        pytest.param({"name_startswith": "x" * 101}, "an over-long prefix", id="long-prefix"),
        pytest.param({"access_right": "Read"}, "not one of the nine names", id="bad-right"),
        pytest.param({"top": 0}, "top below 1", id="top-too-low"),
        pytest.param({"top": 5001}, "top above the cap", id="top-too-high"),
        pytest.param({"unknown": 1}, "extra='forbid'", id="unknown-field"),
    ],
)
def test_input_model_rejects_bad_input(kwargs: dict, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    with pytest.raises(ValidationError):
        ListPrivilegesInput(dataverse_url=_BASE_URL, **kwargs)
