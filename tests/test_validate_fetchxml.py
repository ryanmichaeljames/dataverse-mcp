"""Unit tests for dataverse_validate_fetchxml.

The payload fixtures below mirror the shape recorded from a live v9.2 org: one
top-level ``ValidationResults`` object holding ``Helplink`` and a ``Messages``
list, where each message carries ``TypeCode``, ``Severity``,
``LocalizedMessageText`` and an ``OptionalPropertyBag``. There is no ``Result`` /
``IsValid`` boolean anywhere in it.

Acceptance criteria:
- Happy path: the FetchXml travels as a single-quoted OData string literal behind
  the ``@p1`` parameter alias and the ``@odata`` envelope is stripped.
- A semantically invalid query comes back as HTTP 200 with a Severity-3 message,
  NOT an HTTP 400, so the severity split must be surfaced at the top level or a
  caller will read "200" as "valid".
- Severity 1 = warning and 3 = error are observed, not documented: >= 3 is an
  error, < 3 a warning, and an unrecognized or missing severity is bucketed as an
  error while still being counted, so count == error_count + warning_count always.
- An unexpected payload must pass through raw with normalized=false, no counts and
  no crash.
- OData escaping is the entire defence for this free-form input: a FetchXml full of
  single quotes, including a ``')/WhoAmI(`` breakout attempt, must still parse
  server-side as one literal that decodes back to exactly the input.
- Malformed markup and DTD/entity payloads fail locally with an actionable message
  and never reach the network.

All HTTP is mocked; no network access.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import unquote

import httpx
import pytest
from pydantic import ValidationError

from dataverse_mcp.client import AppContext
from dataverse_mcp.models import ValidateFetchXmlInput
from dataverse_mcp.tools.tables import dataverse_validate_fetchxml

_BASE_URL = "https://yourorg.crm.dynamics.com"
_API_ROOT = f"{_BASE_URL}/api/data/v9.2"
_FUNCTION_PREFIX = f"{_API_ROOT}/ValidateFetchXmlExpression(FetchXml=@p1)?@p1="

_SIMPLE_FETCH = (
    '<fetch top="5"><entity name="systemuser">'
    '<attribute name="fullname" /></entity></fetch>'
)

# A quote-heavy query that also tries to close the OData literal and navigate to a
# different function — the exact class of breakout hardened in commit 47d01d6.
_BREAKOUT_FETCH = (
    "<fetch top='5'><entity name='systemuser'>"
    "<condition attribute='fullname' operator='eq' value=\"')/WhoAmI(\" />"
    "<condition attribute='domainname' operator='eq' value=\"o'brien\" />"
    "</entity></fetch>"
)

_HELPLINK = "https://go.microsoft.com/fwlink/?linkid=0000000"


def _results(*messages: dict) -> dict:
    """Wrap *messages* in the live ValidationResults envelope."""
    return {"ValidationResults": {"Helplink": _HELPLINK, "Messages": list(messages)}}


def _envelope(body: dict) -> dict:
    """Add the @odata.context annotation the tool is expected to strip."""
    return {
        "@odata.context": (
            f"{_API_ROOT}/$metadata#Microsoft.Dynamics.CRM."
            "ValidateFetchXmlExpressionResponse"
        ),
        **body,
    }


# Observed live: a clean, narrow, top-limited query reports nothing at all.
_CLEAN_BODY = _results()

# Observed live for a query with no <attribute> elements, a leading-wildcard
# 'like' and an order on an unindexed column: two Severity-1 messages.
_WARNINGS_BODY = _results(
    {
        "TypeCode": 1,
        "Severity": 1,
        "LocalizedMessageText": (
            "The condition uses a leading wildcard, which prevents index usage."
        ),
        "OptionalPropertyBag": {"Count": 0, "Keys": [], "Values": []},
    },
    {
        "TypeCode": 2,
        "Severity": 1,
        "LocalizedMessageText": "The query selects too many attributes.",
        "OptionalPropertyBag": {
            "Count": 2,
            "Keys": ["AttributeCount", "AttributeLimit"],
            "Values": ["130", "100"],
        },
    },
)

# Observed live for a nonexistent table — returned with HTTP 200, not 400.
_ERROR_TEXT = (
    "Error handling FetchXML: The entity with a name = 'nosuchtable' was not "
    "found in the MetadataCache."
)
_ERROR_BODY = _results(
    {
        "TypeCode": 3,
        "Severity": 3,
        "LocalizedMessageText": _ERROR_TEXT,
        "OptionalPropertyBag": {"Count": 0, "Keys": [], "Values": []},
    }
)

_MIXED_BODY = _results(
    {
        "TypeCode": 3,
        "Severity": 3,
        "LocalizedMessageText": _ERROR_TEXT,
        "OptionalPropertyBag": {"Count": 0, "Keys": [], "Values": []},
    },
    {
        "TypeCode": 1,
        "Severity": 1,
        "LocalizedMessageText": "The query selects too many attributes.",
        "OptionalPropertyBag": {
            "Count": 2,
            "Keys": ["AttributeCount", "AttributeLimit"],
            "Values": ["130", "100"],
        },
    },
)

_BILLION_LAUGHS = """\
<?xml version="1.0"?>
<!DOCTYPE fetch [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;">
]>
<fetch><entity name="systemuser">&lol3;</entity></fetch>"""


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
    body: Any, fetch_xml: str = _SIMPLE_FETCH, status_code: int = 200, params=None
) -> tuple[dict, list[str]]:
    """Run the tool against a mocked response; return (result, requested URLs)."""
    params = params or ValidateFetchXmlInput(
        dataverse_url=_BASE_URL, fetch_xml=fetch_xml
    )

    async def _side_effect(_client, _method, url, **_kwargs):
        return httpx.Response(status_code, json=body, request=httpx.Request("GET", url))

    with patch(
        "dataverse_mcp.tools.tables.build_headers",
        new=AsyncMock(return_value={"Authorization": "Bearer token"}),
    ), patch(
        "dataverse_mcp.tools.tables.request_with_retry",
        new=AsyncMock(side_effect=_side_effect),
    ) as mock_request:
        result = json.loads(await dataverse_validate_fetchxml(params, _make_ctx()))

    return result, [call.args[2] for call in mock_request.await_args_list]


def _parse_odata_literal(url: str) -> tuple[str, str]:
    """Reproduce how Dataverse reads the alias value: percent-decode, then scan.

    The whole URL is percent-decoded *before* the OData expression is parsed, so
    that is the order applied here. Returns ``(literal_value, trailing_text)``;
    a non-empty trailing text means the caller's value escaped the literal and
    became part of the request expression.
    """
    assert url.startswith(_FUNCTION_PREFIX), url
    decoded = unquote(url[len(_FUNCTION_PREFIX):])
    assert decoded.startswith("'"), decoded

    out: list[str] = []
    i = 1
    while i < len(decoded):
        ch = decoded[i]
        if ch == "'":
            if decoded[i + 1: i + 2] == "'":  # '' is an escaped quote
                out.append("'")
                i += 2
                continue
            return "".join(out), decoded[i + 1:]  # literal closed here
        out.append(ch)
        i += 1
    raise AssertionError("unterminated OData literal")


@pytest.mark.asyncio
async def test_clean_query_builds_the_alias_url_and_reports_no_findings() -> None:
    """The FetchXml rides the @p1 alias; an empty Messages list means all clear."""
    result, urls = await _run(_envelope(_CLEAN_BODY))

    assert "error" not in result
    # Envelope stripped, everything else untouched — no property renamed.
    assert result["raw_response"] == _CLEAN_BODY
    assert result["normalized"] is True
    assert result["has_errors"] is False
    assert (result["count"], result["error_count"], result["warning_count"]) == (0, 0, 0)
    assert result["errors"] == []
    assert result["fetch_xml_length"] == len(_SIMPLE_FETCH)

    assert len(urls) == 1
    assert urls[0].startswith(_FUNCTION_PREFIX)
    literal, trailing = _parse_odata_literal(urls[0])
    assert literal == _SIMPLE_FETCH
    assert trailing == ""


@pytest.mark.asyncio
async def test_warning_only_response_is_not_reported_as_an_error() -> None:
    """Two Severity-1 messages: worth reading, but the query is still valid."""
    result, _ = await _run(_envelope(_WARNINGS_BODY))

    assert result["has_errors"] is False
    assert (result["count"], result["error_count"], result["warning_count"]) == (2, 0, 2)
    assert result["errors"] == []
    # The detail an LLM needs to act on stays available, untrimmed.
    messages = result["raw_response"]["ValidationResults"]["Messages"]
    assert messages[1]["OptionalPropertyBag"]["Values"] == ["130", "100"]
    assert result["raw_response"]["ValidationResults"]["Helplink"] == _HELPLINK


@pytest.mark.asyncio
async def test_severity_3_on_an_http_200_is_surfaced_as_an_error() -> None:
    """The trap: an invalid query returns 200, so has_errors is the real verdict."""
    result, _ = await _run(_envelope(_ERROR_BODY), status_code=200)

    assert "error" not in result, "a 200 is not a transport failure"
    assert result["has_errors"] is True
    assert (result["count"], result["error_count"], result["warning_count"]) == (1, 1, 0)
    assert result["errors"] == [_ERROR_TEXT]
    # The top-level message must not read as "accepted", or the caller will run it.
    assert "NOT VALID" in result["message"]


@pytest.mark.asyncio
async def test_mixed_severities_are_split_and_nothing_is_dropped() -> None:
    """An error alongside a warning: both counted, only the error in errors."""
    result, _ = await _run(_envelope(_MIXED_BODY))

    assert result["has_errors"] is True
    assert (result["count"], result["error_count"], result["warning_count"]) == (2, 1, 1)
    assert result["errors"] == [_ERROR_TEXT]
    assert result["count"] == result["error_count"] + result["warning_count"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message,reason",
    [
        pytest.param(
            {"Severity": 9, "LocalizedMessageText": "Unknown future severity."},
            "a code above the observed error severity",
            id="higher-code",
        ),
        pytest.param(
            {"LocalizedMessageText": "No severity at all."},
            "Severity absent entirely",
            id="missing",
        ),
        pytest.param(
            {"Severity": "3", "LocalizedMessageText": "Severity as a string."},
            "Severity not an integer",
            id="non-int",
        ),
        pytest.param(
            {"Severity": True, "LocalizedMessageText": "bool is an int subclass."},
            "True must not slip through as severity 1",
            id="bool",
        ),
        pytest.param("just a string", "the message is not even an object", id="not-a-dict"),
    ],
)
async def test_unrecognized_severities_are_bucketed_as_errors_not_dropped(
    message: Any, reason: str
) -> None:
    """The mapping is observed, not documented, so unknown codes fail closed."""
    result, _ = await _run(_envelope(_results(message)))

    assert result["count"] == 1, f"the message was dropped: {reason}"
    assert result["error_count"] == 1, reason
    assert result["warning_count"] == 0, reason
    assert result["has_errors"] is True, reason
    assert len(result["errors"]) == 1


@pytest.mark.asyncio
async def test_missing_message_text_still_yields_a_placeholder_error() -> None:
    """An error with no text must still be visible, not an empty string."""
    result, _ = await _run(_envelope(_results({"Severity": 3})))

    assert result["errors"] == ["(no message text; Severity=3)"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,reason",
    [
        pytest.param(
            {"@odata.context": "ctx", "ValidationResults": {"Helplink": "x"}},
            "ValidationResults present but no Messages list",
            id="no-messages",
        ),
        pytest.param(
            {"ValidationResults": {"Helplink": "x", "Messages": {"Severity": 3}}},
            "Messages is an object, not a list",
            id="messages-not-a-list",
        ),
        pytest.param(
            {"ValidationResults": "unexpected"},
            "ValidationResults is not an object",
            id="results-not-a-dict",
        ),
        pytest.param(
            {"Result": True, "Warnings": []},
            "a shape that does not exist — no ValidationResults at all",
            id="no-validation-results",
        ),
        pytest.param(
            [{"Severity": 3}], "a bare array is not the observed shape", id="top-level-list"
        ),
        pytest.param("OK", "a bare string payload", id="scalar"),
    ],
)
async def test_unexpected_shapes_pass_through_without_counts(body: Any, reason: str) -> None:
    """An unrecognized payload is returned raw, uncounted, and does not crash."""
    result, _ = await _run(body)

    assert "error" not in result, reason
    assert result["normalized"] is False, reason
    for key in ("count", "error_count", "warning_count", "has_errors", "errors"):
        assert key not in result, f"{key} must not be fabricated: {reason}"
    # The caller is told not to read the 200 as a pass.
    assert "does" in result["message"] and "NOT mean the query is valid" in result["message"]
    expected = (
        {k: v for k, v in body.items() if not k.startswith("@odata.")}
        if isinstance(body, dict)
        else body
    )
    assert result["raw_response"] == expected


@pytest.mark.asyncio
async def test_embedded_quotes_and_breakout_attempt_stay_inside_the_literal() -> None:
    """Every quote is doubled before percent-encoding, so nothing escapes @p1.

    Percent-encoding alone would leave each ``'`` as a lone ``%27`` that decodes
    back to a literal-terminating quote, letting ``')/WhoAmI(`` become part of the
    request expression.
    """
    _, urls = await _run({"Result": True}, fetch_xml=_BREAKOUT_FETCH)

    url = urls[0]
    query = url[len(_FUNCTION_PREFIX):]
    # Only the two delimiters are bare quotes; the payload's quotes are encoded.
    assert query.startswith("'") and query.endswith("'")
    assert "'" not in query[1:-1]
    # Every payload quote is doubled: an even %27 count means no lone quote survives
    # percent-decoding to terminate the literal.
    assert query.count("%27") % 2 == 0
    # The breakout tokens never appear as live URL syntax.
    assert "/WhoAmI(" not in url
    assert "%2FWhoAmI%28" in url

    # Server-side view: decode, then parse the literal. It must round-trip exactly
    # and leave nothing dangling after the closing quote.
    literal, trailing = _parse_odata_literal(url)
    assert literal == _BREAKOUT_FETCH
    assert trailing == ""


@pytest.mark.asyncio
async def test_malformed_xml_fails_locally_without_a_round_trip() -> None:
    """Unclosed markup is rejected before the request is built."""
    params = ValidateFetchXmlInput(
        dataverse_url=_BASE_URL,
        fetch_xml='<fetch><entity name="systemuser"></fetch>',
    )

    result, urls = await _run({"Result": True}, params=params)

    assert result["error"] is True
    assert "not well-formed" in result["message"]
    assert urls == [], "a document Dataverse cannot parse must not cost a round trip"


@pytest.mark.asyncio
async def test_dtd_and_entity_payloads_are_rejected_by_the_defused_parser() -> None:
    """The tool's own guard holds even if the model's '<fetch' prefix check does not.

    A ``<!DOCTYPE`` prologue is already rejected at the input model, so this drives
    the tool with ``model_construct`` (validation bypassed) to prove the parse site
    itself is defused and never hands caller XML to the stdlib parser.
    """
    params = ValidateFetchXmlInput.model_construct(
        dataverse_url=_BASE_URL, fetch_xml=_BILLION_LAUGHS
    )

    result, urls = await _run({"Result": True}, params=params)

    assert result["error"] is True
    assert "DTD" in result["message"]
    assert urls == [], "an entity-expansion payload must not be relayed to Dataverse"


@pytest.mark.asyncio
async def test_url_too_long_is_reported_with_a_shortening_hint() -> None:
    """HTTP 414 is the one status whose fix is 'make the FetchXml smaller'."""
    result, _ = await _run({"error": {"message": "URI too long"}}, status_code=414)

    assert result["error"] is True
    assert "HTTP 414" in result["message"]
    assert "<link-entity>" in result["message"]


@pytest.mark.asyncio
async def test_http_errors_use_the_standard_envelope() -> None:
    """A 400 from Dataverse is surfaced through the shared error contract."""
    body = {"error": {"code": "0x80040203", "message": "Entity nope was not found."}}

    result, _ = await _run(body, status_code=400)

    assert result["error"] is True
    assert "400" in result["message"]
    assert "raw_response" not in result


@pytest.mark.parametrize(
    "fetch_xml,reason",
    [
        pytest.param("SELECT * FROM account", "not XML at all", id="sql"),
        pytest.param("<query><entity /></query>", "wrong root element", id="wrong-root"),
        pytest.param(
            '<?xml version="1.0"?><fetch />', "a prologue hides the root check", id="prologue"
        ),
        pytest.param("<fetch/" + "x" * 2000, "over the documented max_length", id="too-long"),
        pytest.param("<fetch>", "under the minimum length", id="too-short"),
    ],
)
def test_input_model_rejects_non_fetchxml(fetch_xml: str, reason: str) -> None:
    """Bad input is refused at the model boundary, so no URL is ever built."""
    with pytest.raises(ValidationError):
        ValidateFetchXmlInput(dataverse_url=_BASE_URL, fetch_xml=fetch_xml)


def test_input_model_accepts_a_document_at_the_length_limit() -> None:
    """2000 characters is the documented cap itself and must not be rejected."""
    padding = "x" * (2000 - len('<fetch><entity name="" /></fetch>'))
    fetch_xml = f'<fetch><entity name="{padding}" /></fetch>'
    assert len(fetch_xml) == 2000
    params = ValidateFetchXmlInput(dataverse_url=_BASE_URL, fetch_xml=fetch_xml)
    assert params.fetch_xml == fetch_xml
