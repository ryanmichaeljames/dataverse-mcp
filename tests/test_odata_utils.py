"""Unit tests for odata_quote and _parse_retry_after_seconds in client.py.

Acceptance criteria:
- odata_quote: no-quote passthrough, single quote doubled, multiple quotes, empty string.
- _parse_retry_after_seconds: numeric header -> float; missing header -> default 2.0;
  unparseable -> default 2.0; negative clamped to 0.0.
"""

import httpx
import pytest

from dataverse_mcp.client import (
    _DEFAULT_RETRY_AFTER_SECONDS,
    _parse_retry_after_seconds,
    odata_quote,
)

# ---------------------------------------------------------------------------
# odata_quote
# ---------------------------------------------------------------------------


def test_odata_quote_no_single_quote() -> None:
    """A value with no single quotes passes through unchanged."""
    assert odata_quote("hello world") == "hello world"


def test_odata_quote_single_quote_doubled() -> None:
    """A single quote is replaced by two single quotes (OData escaping)."""
    assert odata_quote("O'Brien") == "O''Brien"


def test_odata_quote_multiple_quotes() -> None:
    """Every single quote in the value is doubled."""
    assert odata_quote("it's a 'test'") == "it''s a ''test''"


def test_odata_quote_empty_string() -> None:
    """An empty string returns an empty string."""
    assert odata_quote("") == ""


def test_odata_quote_only_quotes() -> None:
    """A string that is only single quotes doubles each one."""
    assert odata_quote("''") == "''''"


# ---------------------------------------------------------------------------
# _parse_retry_after_seconds
# ---------------------------------------------------------------------------


def _make_response(headers: dict[str, str]) -> httpx.Response:
    """Build a minimal httpx.Response with the given headers."""
    return httpx.Response(200, headers=headers)


def test_parse_retry_after_numeric() -> None:
    """A numeric Retry-After header is returned as a float."""
    response = _make_response({"Retry-After": "5"})
    assert _parse_retry_after_seconds(response) == 5.0


def test_parse_retry_after_float_string() -> None:
    """A float string Retry-After header is parsed correctly."""
    response = _make_response({"Retry-After": "12.5"})
    assert _parse_retry_after_seconds(response) == pytest.approx(12.5)


def test_parse_retry_after_missing_header() -> None:
    """A missing Retry-After header returns the default (2.0)."""
    response = _make_response({})
    assert _parse_retry_after_seconds(response) == _DEFAULT_RETRY_AFTER_SECONDS


def test_parse_retry_after_unparseable() -> None:
    """An unparseable Retry-After header returns the default (2.0)."""
    response = _make_response({"Retry-After": "not-a-number"})
    assert _parse_retry_after_seconds(response) == _DEFAULT_RETRY_AFTER_SECONDS


def test_parse_retry_after_negative_clamped_to_zero() -> None:
    """A negative Retry-After value is clamped to 0.0."""
    response = _make_response({"Retry-After": "-3"})
    assert _parse_retry_after_seconds(response) == 0.0
