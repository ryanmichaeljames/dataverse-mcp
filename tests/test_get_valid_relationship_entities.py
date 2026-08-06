"""Unit tests for dataverse_get_valid_relationship_entities.

Fixtures encode the response shape ``{"EntityNames": ["account", ...]}``, which a
live run has since confirmed for all three functions. ``_NAMES_CONTAINER`` below
stays the single place to correct if that ever changes, and the by-shape fallback
is tested as hard as the documented path because a previous round in this
codebase shipped a bug from a fixture that encoded a shape no real org produces.

The load-bearing assertions here are the URLs. Two things are easy to get wrong
and both are asserted byte-for-byte:

* the parameter names are INVERTED relative to the function names —
  ``GetValidReferencedEntities(ReferencingEntityName=...)`` and
  ``GetValidReferencingEntities(ReferencedEntityName=...)``; you always name the
  OTHER side;
* an omitted optional parameter drops the WHOLE SLOT (``GetValidReferencedEntities()``),
  not just the value — ``Fn(Name=@p1)?@p1=''`` is a different request.

The parameter is still sent, but it DOES NOT FILTER: live, the scoped and
unscoped calls returned byte-identical lists. The response says so through
``table_logical_name_filtered: false`` (which replaced a ``scope`` field that
claimed a narrowing that never happened), and that honesty is asserted below.

Acceptance criteria:
- Both 1:N roles build the inverted parameter name, as a single-quoted Edm.String
  literal behind a literal ``@p1`` alias (never ``%40p1``).
- Omitting table_logical_name removes the parameter and its alias entirely.
- A supplied table_logical_name is reported as non-filtering, never as a scope.
- role='many_to_many' with a table_logical_name is REJECTED by the model rather
  than silently ignored.
- Trimming: count/total_count/has_more always describe the full set.
- An unreadable payload degrades to normalized: false with the raw body; an empty
  EntityNames list is a real answer.
- Hostile table names never reach a URL.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import GetValidRelationshipEntitiesInput
from dataverse_mcp.tools.metadata import (
    _RELATIONSHIP_ROLE_FUNCTIONS,
    dataverse_get_valid_relationship_entities,
)

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"

# The one place a live run should be corrected.
_NAMES_CONTAINER = "EntityNames"

# The exact URLs this tool must build, all five call shapes.
_URL_REFERENCED_SCOPED = (
    f"{_API_ROOT}/GetValidReferencedEntities(ReferencingEntityName=@p1)?@p1='account'"
)
_URL_REFERENCING_SCOPED = (
    f"{_API_ROOT}/GetValidReferencingEntities(ReferencedEntityName=@p1)?@p1='account'"
)
_URL_REFERENCED_ALL = f"{_API_ROOT}/GetValidReferencedEntities()"
_URL_REFERENCING_ALL = f"{_API_ROOT}/GetValidReferencingEntities()"
_URL_MANY_TO_MANY = f"{_API_ROOT}/GetValidManyToMany()"


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


def _body(names: list) -> dict:
    """The documented envelope."""
    return {
        "@odata.context": (
            f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM.GetValidEntitiesResponse"
        ),
        _NAMES_CONTAINER: names,
    }


async def _run(body: Any, **kwargs: Any) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = GetValidRelationshipEntitiesInput(dataverse_url=_BASE_URL, **kwargs)

    async def _side_effect(_client, _method, url, **_kwargs):
        if isinstance(body, httpx.Response):
            body.request = httpx.Request("GET", url)
            return body
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    with patch(
        "dataverse_mcp.tools.metadata.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.metadata.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(
            await dataverse_get_valid_relationship_entities(params, _make_ctx())
        )

    return result, [call.args[2] for call in mock_request.await_args_list]


def test_the_parameter_names_are_inverted_relative_to_the_function_names() -> None:
    """The allowlist itself, asserted: you always name the OTHER side."""
    assert _RELATIONSHIP_ROLE_FUNCTIONS == {
        "referenced": ("GetValidReferencedEntities", "ReferencingEntityName"),
        "referencing": ("GetValidReferencingEntities", "ReferencedEntityName"),
        "many_to_many": ("GetValidManyToMany", None),
    }, (
        "the inversion is deliberate: GetValidReferencedEntities takes "
        "ReferencingEntityName and vice versa. Do not 'fix' this to match."
    )


async def test_referenced_role_sends_the_referencing_parameter() -> None:
    """role='referenced' -> GetValidReferencedEntities(ReferencingEntityName=...)."""
    result, urls = await _run(
        _body(["contact", "systemuser"]), role="referenced", table_logical_name="account"
    )

    assert urls == [_URL_REFERENCED_SCOPED]
    assert "ReferencedEntityName" not in urls[0], "the parameter must be the OTHER side"
    assert result["function"] == "GetValidReferencedEntities"
    assert result["parameter"] == "ReferencingEntityName"
    assert result["role"] == "referenced"
    assert result["table_logical_name"] == "account"
    assert result["normalized"] is True
    assert result["source"] == _NAMES_CONTAINER
    assert result["table_logical_names"] == ["contact", "systemuser"]
    assert result["count"] == 2
    assert result["total_count"] == 2
    assert result["has_more"] is False


async def test_referencing_role_sends_the_referenced_parameter() -> None:
    """role='referencing' -> GetValidReferencingEntities(ReferencedEntityName=...)."""
    result, urls = await _run(
        _body(["contact"]), role="referencing", table_logical_name="account"
    )

    assert urls == [_URL_REFERENCING_SCOPED]
    assert "ReferencingEntityName" not in urls[0], "the parameter must be the OTHER side"
    assert result["function"] == "GetValidReferencingEntities"
    assert result["parameter"] == "ReferencedEntityName"


async def test_a_supplied_table_name_is_reported_as_non_filtering() -> None:
    """The parameter is sent and validated server-side, but it does NOT narrow.

    Live, the scoped and unscoped calls returned byte-identical lists, so the
    response must not imply a narrowing that did not happen. If Microsoft ever
    starts honouring the parameter, this and the integration test are what say so.
    """
    result, _urls = await _run(
        _body(["contact"]), role="referenced", table_logical_name="account"
    )

    assert result["table_logical_name_filtered"] is False
    assert "scope" not in result, (
        "the old scope field claimed 'table' vs 'environment' for identical data"
    )
    note = result["table_logical_name_note"]
    assert "account" in note and "did NOT narrow" in note
    assert "dataverse_check_relationship_eligibility" in note, (
        "the caller must be pointed at the tool that really answers per-table"
    )


async def test_the_alias_survives_as_at_p1_and_the_literal_is_single_quoted() -> None:
    """Edm.String regime: bare delimiting quotes, a literal @p1, nothing over-encoded."""
    _result, urls = await _run(
        _body([]), role="referenced", table_logical_name="new_customtable"
    )
    url = urls[0]

    assert url.endswith("?@p1='new_customtable'")
    assert "%40p1" not in url, "the alias name must stay literal or Dataverse ignores it"
    assert "%" not in url, (
        "an identifier-grammar name has nothing to percent-encode; a % here means "
        "the value was encoded twice"
    )
    assert url.count("'") == 2, "exactly the two delimiting quotes"


@pytest.mark.parametrize(
    ("role", "expected_url", "parameter"),
    [
        ("referenced", _URL_REFERENCED_ALL, "ReferencingEntityName"),
        ("referencing", _URL_REFERENCING_ALL, "ReferencedEntityName"),
    ],
)
async def test_omitting_the_table_drops_the_whole_slot(
    role: str, expected_url: str, parameter: str
) -> None:
    """Fn() is a DIFFERENT request from Fn(Name=@p1)?@p1='' — no empty hole is emitted."""
    result, urls = await _run(_body(["account", "contact"]), role=role)

    assert urls == [expected_url]
    assert parameter not in urls[0], "the parameter slot itself must be gone"
    assert "@p1" not in urls[0] and "?" not in urls[0] and "'" not in urls[0]
    assert urls[0].endswith("()")
    assert result["table_logical_name"] is None
    assert result["table_logical_name_filtered"] is False
    assert "table_logical_name_note" not in result, (
        "no name was supplied, so there is nothing to disclaim"
    )
    # The echoed parameter still names which side the function would take.
    assert result["parameter"] == parameter


async def test_many_to_many_is_a_zero_parameter_call() -> None:
    """GetValidManyToMany takes nothing at all."""
    result, urls = await _run(_body(["account", "contact"]), role="many_to_many")

    assert urls == [_URL_MANY_TO_MANY]
    assert result["function"] == "GetValidManyToMany"
    assert result["parameter"] is None
    assert result["table_logical_name_filtered"] is False


def test_many_to_many_with_a_table_name_is_rejected_not_ignored() -> None:
    """Silently dropping the scope would answer a different question."""
    with pytest.raises(ValidationError) as excinfo:
        GetValidRelationshipEntitiesInput(
            dataverse_url=_BASE_URL,
            role="many_to_many",
            table_logical_name="account",
        )
    assert "many_to_many" in str(excinfo.value)
    # Without the table name it is perfectly valid.
    assert (
        GetValidRelationshipEntitiesInput(
            dataverse_url=_BASE_URL, role="many_to_many"
        ).table_logical_name
        is None
    )


async def test_large_answers_are_trimmed_but_report_their_true_magnitude() -> None:
    """The answers run to hundreds of names (575/305/166 live); top bounds the payload only."""
    names = [f"table{i:04d}" for i in range(1200)]
    result, _urls = await _run(_body(names), role="many_to_many", top=10)

    assert result["count"] == 10
    assert result["total_count"] == 1200
    assert result["has_more"] is True
    assert result["table_logical_names"] == names[:10]
    assert "message" in result and "1200" in result["message"]


async def test_default_top_is_250() -> None:
    """The documented default, asserted where a caller can see it."""
    names = [f"table{i:04d}" for i in range(300)]
    result, _urls = await _run(_body(names), role="many_to_many")

    assert result["count"] == 250
    assert result["total_count"] == 300
    assert result["has_more"] is True


async def test_an_empty_list_is_a_real_answer() -> None:
    """A present but empty EntityNames means "nothing qualifies", not a failure."""
    result, _urls = await _run(
        _body([]), role="referencing", table_logical_name="account"
    )

    assert result["normalized"] is True
    assert result["table_logical_names"] == []
    assert result["count"] == 0
    assert result["total_count"] == 0
    assert result["has_more"] is False
    assert not result.get("error")


async def test_unreadable_payload_degrades_instead_of_faking_an_empty_list() -> None:
    """No unambiguous list -> normalized false, raw body, no counts, no guess."""
    result, _urls = await _run(
        {"@odata.context": "ctx", "Alpha": ["account"], "Beta": ["contact"]},
        role="referenced",
    )

    assert result["normalized"] is False
    assert "table_logical_names" not in result
    assert "count" not in result and "total_count" not in result
    assert "has_more" not in result
    assert result["raw_response"] == {"Alpha": ["account"], "Beta": ["contact"]}
    assert "no eligible tables" in result["message"], (
        "the message must warn against reading absence as emptiness"
    )
    # Identity is still reported so the caller knows which call this was.
    assert result["function"] == "GetValidReferencedEntities"


async def test_a_differently_named_sole_list_is_still_found() -> None:
    """The documented EntityNames may be wrong; a sole list of strings is unambiguous."""
    result, _urls = await _run(
        {"@odata.context": "ctx", "ValidEntities": ["account"]}, role="many_to_many"
    )

    assert result["normalized"] is True
    assert result["source"] == "ValidEntities"
    assert result["table_logical_names"] == ["account"]


async def test_http_error_returns_the_standard_envelope() -> None:
    """A nonexistent table is an error, not an empty list."""
    result, _urls = await _run(
        httpx.Response(
            400,
            json={"error": {"code": "0x80040216", "message": "no such entity"}},
        ),
        role="referenced",
        table_logical_name="nosuchtable",
    )

    assert result["error"] is True
    assert "400" in result["message"]
    assert "table_logical_names" not in result


@pytest.mark.parametrize(
    "bad_name",
    [
        "account')/WhoAmI(",       # OData literal / navigation breakout
        "account'",                # bare quote
        "account?$select=name",    # inject a query option
        "account/../whoami",       # path traversal
        "account\r\nX: y",         # header injection
        "account,contact",         # smuggle a second value
        "",                        # empty is NOT the same as omitted
        "   ",                     # ... and neither is whitespace
        "a" * 51,                  # beyond Dataverse's 50-char schema-name cap
    ],
)
def test_hostile_table_names_never_reach_a_url(bad_name: str) -> None:
    """The model refuses them; encode_odata_literal is only layer two."""
    with pytest.raises(ValidationError):
        GetValidRelationshipEntitiesInput(
            dataverse_url=_BASE_URL, role="referenced", table_logical_name=bad_name
        )


def test_role_is_a_closed_set() -> None:
    """The role picks a URL PATH SEGMENT, so it is a Literal, not a free string."""
    for bad_role in ("Referenced", "manytomany", "", "referenced')/WhoAmI("):
        with pytest.raises(ValidationError):
            GetValidRelationshipEntitiesInput(dataverse_url=_BASE_URL, role=bad_role)
    with pytest.raises(ValidationError):
        GetValidRelationshipEntitiesInput(dataverse_url=_BASE_URL)


def test_top_is_bounded() -> None:
    """The whole answer rides in one response; top stays inside Dataverse's page cap."""
    assert GetValidRelationshipEntitiesInput(
        dataverse_url=_BASE_URL, role="many_to_many"
    ).top == 250
    for bad_top in (0, -1, 5001):
        with pytest.raises(ValidationError):
            GetValidRelationshipEntitiesInput(
                dataverse_url=_BASE_URL, role="many_to_many", top=bad_top
            )
