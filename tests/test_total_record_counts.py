"""Unit tests for dataverse_get_total_record_counts.

Acceptance criteria:
- Happy path: the parallel Keys/Values arrays Dataverse uses to serialize
  EntityRecordCountCollection are zipped into a {logical_name: count} map, and
  the collection-typed EntityNames parameter is sent as a percent-encoded JSON
  array behind the @p1 parameter alias.
- Unexpected payload shape: nothing is fabricated — the payload comes back raw
  under raw_response with normalized=false. _extract_count_map's bail-out
  branches are covered directly.
- Unknown table names are all-or-nothing (live-verified): one unrecognized name
  fails the whole batch with HTTP 400 [0x80040203], and the tool turns that into
  an actionable error envelope naming the offending table.
- A uniformly zero result is flagged, because it usually means the ≤24h snapshot
  job has not run rather than that every table is empty.
- Input validation: an entity name carrying an OData/URL breakout payload is
  rejected by the model, so it never reaches a URL; so are out-of-range list
  lengths and over-long names.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import GetTotalRecordCountsInput
from dataverse_mcp.tools.tables import (
    _extract_count_map,
    dataverse_get_total_record_counts,
)

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"

# The exact URL the tool must build: a parameter alias carrying the JSON array,
# percent-encoded, with the '@' of the alias left literal.
_EXPECTED_URL = (
    f"{_API_ROOT}/RetrieveTotalRecordCount(EntityNames=@p1)"
    "?@p1=%5B%22account%22%2C%22contact%22%5D"
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


async def _run(
    body: dict, entity_names: list[str], status_code: int = 200
) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = GetTotalRecordCountsInput(
        dataverse_url=_BASE_URL, entity_names=entity_names
    )

    async def _side_effect(_client, _method, url, **_kwargs):
        return httpx.Response(
            status_code, json=body, request=httpx.Request("GET", url)
        )

    with patch(
        "dataverse_mcp.tools.tables.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.tables.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_get_total_record_counts(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


@pytest.mark.asyncio
async def test_keys_values_are_normalized_into_a_count_map() -> None:
    """The .NET-dictionary Keys/Values form becomes a plain {name: count} map."""
    body = {
        "@odata.context": f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.RetrieveTotalRecordCountResponse",
        "EntityRecordCountCollection": {
            "Count": 2,
            "IsReadOnly": False,
            "Keys": ["account", "contact"],
            "Values": [1234, 56],
        },
    }

    result, urls = await _run(body, ["account", "contact"])

    assert result["normalized"] is True
    assert result["counts"] == {"account": 1234, "contact": 56}
    assert result["count"] == 2
    assert result["requested_count"] == 2
    assert result["approximate"] is True
    assert result["all_counts_zero"] is False
    assert "not_returned" not in result, (
        "not_returned is dead: Dataverse 400s the whole batch on an unknown name"
    )
    # The collection parameter must travel as a percent-encoded JSON array
    # behind the @p1 alias — inlining it is not valid OData.
    assert urls == [_EXPECTED_URL]


@pytest.mark.asyncio
async def test_unexpected_shape_falls_back_to_the_raw_payload() -> None:
    """An unrecognized response is returned raw rather than guessed at."""
    body = {
        "@odata.context": "ctx",
        "EntityRecordCountCollection": {"account": 1234, "contact": 56},
    }

    result, _ = await _run(body, ["account", "contact"])

    assert "error" not in result
    assert result["normalized"] is False
    assert "counts" not in result, "no count map may be fabricated from an unknown shape"
    # The @odata envelope is stripped; everything else is passed through untouched.
    assert result["raw_response"] == {
        "EntityRecordCountCollection": {"account": 1234, "contact": 56}
    }
    assert result["requested"] == ["account", "contact"]


@pytest.mark.asyncio
async def test_unknown_name_400_is_reported_as_a_whole_batch_failure() -> None:
    """Live behaviour: one bad name 400s the batch — say so, and name the table."""
    body = {
        "error": {
            "code": "0x80040203",
            "message": (
                "Entity zzz_not_a_real_table was not found in the CRM system. "
                "Check the entity name."
            ),
        }
    }

    result, _ = await _run(
        body, ["account", "contact", "zzz_not_a_real_table"], status_code=400
    )

    assert result["error"] is True
    message = result["message"]
    # Actionable: which name, that nothing came back, and what to do about it.
    assert "zzz_not_a_real_table" in message
    assert "all-or-nothing" in message
    assert "3 requested name(s)" in message
    assert "dataverse_list_tables" in message
    assert set(result) == {"error", "message"}, "the error envelope shape is fixed"


@pytest.mark.asyncio
async def test_other_http_errors_use_the_standard_envelope() -> None:
    """A 403 is not the unknown-entity case and must not claim it is."""
    body = {"error": {"code": "0x80040220", "message": "Principal user is missing prvReadEntity."}}

    result, _ = await _run(body, ["account"], status_code=403)

    assert result["error"] is True
    assert "all-or-nothing" not in result["message"]
    assert "403" in result["message"]


@pytest.mark.asyncio
async def test_all_zero_counts_are_flagged_as_a_probable_stale_snapshot() -> None:
    """Live orgs return 0 for every table when the ≤24h snapshot job has not run."""
    body = {
        "EntityRecordCountCollection": {
            "Count": 2,
            "IsReadOnly": False,
            "Keys": ["systemuser", "solution"],
            "Values": [0, 0],
        }
    }

    result, _ = await _run(body, ["systemuser", "solution"])

    assert result["counts"] == {"systemuser": 0, "solution": 0}
    assert result["all_counts_zero"] is True
    assert "snapshot" in result["message"]
    assert "dataverse_count_records" in result["message"]
    assert "error" not in result, "an all-zero snapshot is a warning, not a failure"


@pytest.mark.asyncio
async def test_a_single_genuine_zero_is_not_flagged() -> None:
    """all_counts_zero must not fire while any table reports rows."""
    body = {
        "EntityRecordCountCollection": {"Keys": ["account", "contact"], "Values": [0, 7]}
    }

    result, _ = await _run(body, ["account", "contact"])

    assert result["all_counts_zero"] is False
    assert "message" not in result


@pytest.mark.parametrize(
    "collection,reason",
    [
        pytest.param(
            {"Keys": ["account", "contact"], "Values": [1]},
            "keys/values length mismatch must never be zipped",
            id="length-mismatch",
        ),
        pytest.param(
            {"Keys": ["account", 7], "Values": [1, 2]},
            "a non-string key is not a logical name",
            id="non-string-key",
        ),
        pytest.param(
            {"Keys": ["account"], "Values": [False]},
            "bool is a subclass of int; IsReadOnly-style data is not a count of 0",
            id="bool-value",
        ),
        pytest.param(
            {"Keys": ["account"], "Values": ["1234"]},
            "a stringified count is not an int",
            id="non-int-value",
        ),
        pytest.param(
            {"Keys": ["account"], "Values": [12.5]},
            "a float is not a row count",
            id="float-value",
        ),
        pytest.param(
            {"Keys": "account", "Values": [1]},
            "Keys must be a list",
            id="keys-not-a-list",
        ),
        pytest.param({"account": 1}, "no parallel arrays at all", id="no-arrays"),
    ],
)
def test_extract_count_map_bails_out_rather_than_guessing(
    collection: dict, reason: str
) -> None:
    """Every malformed shape returns None so the tool falls back to raw_response."""
    assert _extract_count_map({"EntityRecordCountCollection": collection}) is None, reason
    # Also rejected when the parallel arrays sit at the top level.
    assert _extract_count_map(collection) is None, reason


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="none"),
        pytest.param([{"Keys": ["account"], "Values": [1]}], id="list"),
        pytest.param("EntityRecordCountCollection", id="str"),
    ],
)
def test_extract_count_map_requires_an_object(payload: Any) -> None:
    """A non-dict payload can carry no dictionary serialization."""
    assert _extract_count_map(payload) is None


def test_extract_count_map_accepts_an_empty_collection() -> None:
    """Empty parallel arrays are a valid (if useless) result, not a bail-out."""
    assert _extract_count_map({"EntityRecordCountCollection": {"Keys": [], "Values": []}}) == {}


@pytest.mark.parametrize(
    "entity_name",
    [
        'account"]',  # closes the JSON array
        "account')/WhoAmI(",  # OData key-predicate / function breakout
        "account,contact",  # smuggles a second array element
        "account?$select=name",  # query-option injection
        "account/../whoami",  # path traversal
        "account\r\nX: y",  # header/CRLF injection
        "",
        # Pattern-valid but longer than Dataverse's 50-character schema-name limit,
        # so it can name no real table and would only bloat the query string.
        "a" * 51,
    ],
)
def test_injection_payloads_are_rejected_by_validation(entity_name: str) -> None:
    """A breakout entity name is rejected at the model, so no URL is ever built."""
    with pytest.raises(ValidationError):
        GetTotalRecordCountsInput(
            dataverse_url=_BASE_URL, entity_names=["account", entity_name]
        )


def test_a_name_at_the_schema_name_limit_is_accepted() -> None:
    """50 characters is the Dataverse limit itself, so it must not be rejected."""
    name = "a" * 50
    params = GetTotalRecordCountsInput(dataverse_url=_BASE_URL, entity_names=[name])
    assert params.entity_names == [name]


@pytest.mark.parametrize(
    "entity_names",
    [
        pytest.param([], id="empty"),
        pytest.param([f"table{i}" for i in range(51)], id="over-max-length"),
    ],
)
def test_list_length_bounds_are_enforced(entity_names: list[str]) -> None:
    """An empty list is pointless and an oversized one would blow the URL limit."""
    with pytest.raises(ValidationError):
        GetTotalRecordCountsInput(dataverse_url=_BASE_URL, entity_names=entity_names)
