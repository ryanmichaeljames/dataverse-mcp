"""Unit tests for batch serialization helpers in batch.py.

Acceptance criteria:
- build_inner_request: GET produces no Content-Type/Content-Length and no body
  even when body is passed; POST/PUT/PATCH with body include Content-Type,
  Content-Length, and the JSON body.
- build_batch_body: structural invariants — boundary markers present, changeset
  parts have Content-ID, standalone parts have no Content-ID wrapper.
- parse_batch_response: parses a synthetic multipart response into
  [{status_code, body}] correctly.
"""

import json

import pytest

from dataverse_mcp.batch import (
    build_batch_body,
    build_inner_request,
    parse_batch_response,
)
from dataverse_mcp.models import BatchOperationItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_op(
    method: str,
    url: str,
    body: dict | None = None,
    change_set_id: str | None = None,
) -> BatchOperationItem:
    """Build a BatchOperationItem for test use."""
    data: dict = {"method": method, "url": url}
    if body is not None:
        data["body"] = body
    if change_set_id is not None:
        data["change_set_id"] = change_set_id
    return BatchOperationItem(**data)


# ---------------------------------------------------------------------------
# build_inner_request — GET / DELETE must not carry a body or content headers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_inner_request_no_body_methods_omit_content_headers(method: str) -> None:
    """GET and DELETE must not include Content-Type or Content-Length."""
    result = build_inner_request(method, "https://example.com/api/test", {"key": "value"})
    assert "Content-Type" not in result
    assert "Content-Length" not in result


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_inner_request_no_body_methods_no_body_text(method: str) -> None:
    """GET and DELETE must not include the JSON body text."""
    body = {"key": "value"}
    result = build_inner_request(method, "https://example.com/api/test", body)
    assert json.dumps(body) not in result


def test_inner_request_get_with_none_body() -> None:
    """GET with no body ends with a single blank line (CRLF CRLF end of headers)."""
    result = build_inner_request("GET", "https://example.com/api/test", None)
    assert "Content-Type" not in result
    assert result.endswith("\r\n")


# ---------------------------------------------------------------------------
# build_inner_request — POST / PUT / PATCH must include body + headers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_inner_request_write_methods_include_content_type(method: str) -> None:
    """POST, PUT, PATCH with a body must include Content-Type: application/json."""
    result = build_inner_request(method, "https://example.com/api/test", {"name": "test"})
    assert "Content-Type: application/json" in result


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_inner_request_write_methods_include_content_length(method: str) -> None:
    """POST, PUT, PATCH with a body must include a Content-Length header."""
    body = {"name": "test"}
    result = build_inner_request(method, "https://example.com/api/test", body)
    expected_length = len(json.dumps(body).encode("utf-8"))
    assert f"Content-Length: {expected_length}" in result


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_inner_request_write_methods_include_body_text(method: str) -> None:
    """POST, PUT, PATCH must include the serialized JSON body."""
    body = {"name": "test", "value": 42}
    result = build_inner_request(method, "https://example.com/api/test", body)
    assert json.dumps(body) in result


def test_inner_request_post_with_none_body_no_content_type() -> None:
    """POST with a None body must not include Content-Type."""
    result = build_inner_request("POST", "https://example.com/api/test", None)
    assert "Content-Type" not in result


# ---------------------------------------------------------------------------
# build_batch_body — structural invariants
# ---------------------------------------------------------------------------

_BASE_URL = "https://org.crm.dynamics.com"
_BATCH_BOUNDARY = "batch_test-boundary"


def test_build_batch_body_contains_boundary_markers() -> None:
    """Batch body must start and end with the batch boundary."""
    ops = [_make_op("GET", "/api/data/v9.2/accounts")]
    body = build_batch_body(ops, _BASE_URL, _BATCH_BOUNDARY)
    assert body.startswith(f"--{_BATCH_BOUNDARY}")
    assert body.endswith(f"--{_BATCH_BOUNDARY}--")


def test_build_batch_body_standalone_get_no_content_id() -> None:
    """Standalone (non-changeset) GET operations must not include Content-ID."""
    ops = [_make_op("GET", "/accounts")]
    body = build_batch_body(ops, _BASE_URL, _BATCH_BOUNDARY)
    assert "Content-ID" not in body


def test_build_batch_body_changeset_includes_content_id() -> None:
    """Change set operations must include at least one Content-ID header."""
    ops = [
        _make_op("POST", "/contacts", {"firstname": "Test"}, change_set_id="cs1"),
        _make_op("PATCH", "/contacts(00000000-0000-0000-0000-000000000001)", {"lastname": "User"}, change_set_id="cs1"),
    ]
    body = build_batch_body(ops, _BASE_URL, _BATCH_BOUNDARY)
    assert "Content-ID:" in body


def test_build_batch_body_changeset_boundary_present() -> None:
    """A change set must introduce an inner changeset boundary."""
    ops = [
        _make_op("POST", "/accounts", {"name": "Acme"}, change_set_id="cs-abc"),
    ]
    body = build_batch_body(ops, _BASE_URL, _BATCH_BOUNDARY)
    assert "changeset_cs-abc" in body


def test_build_batch_body_url_includes_api_path() -> None:
    """The inner request URL must include the Dataverse API path prefix."""
    ops = [_make_op("GET", "/accounts")]
    body = build_batch_body(ops, _BASE_URL, _BATCH_BOUNDARY)
    assert "/api/data/" in body


def test_build_batch_body_multiple_parts_contain_multiple_boundaries() -> None:
    """Two standalone ops must produce two batch part delimiters."""
    ops = [
        _make_op("GET", "/accounts"),
        _make_op("GET", "/contacts"),
    ]
    body = build_batch_body(ops, _BASE_URL, _BATCH_BOUNDARY)
    # boundary appears as: --boundary\r\n (opening) ... --boundary\r\n (second part) ... --boundary-- (close)
    assert body.count(f"--{_BATCH_BOUNDARY}") >= 3  # open + second sep + close


# ---------------------------------------------------------------------------
# parse_batch_response — parses synthetic multipart response
# ---------------------------------------------------------------------------


def _build_synthetic_batch_response(boundary: str, parts: list[tuple[int, dict | None]]) -> str:
    """Build a synthetic multipart/mixed batch response body for testing."""
    lines: list[str] = []
    for status_code, body_obj in parts:
        lines.append(f"--{boundary}")
        lines.append("Content-Type: application/http")
        lines.append("Content-Transfer-Encoding: binary")
        lines.append("")
        lines.append(f"HTTP/1.1 {status_code} OK")
        lines.append("Content-Type: application/json")
        lines.append("")
        if body_obj is not None:
            lines.append(json.dumps(body_obj))
        else:
            lines.append("")
    lines.append(f"--{boundary}--")
    return "\r\n".join(lines)


def test_parse_batch_response_single_part_status_code() -> None:
    """parse_batch_response returns the correct status_code for a single part."""
    boundary = "resp_boundary"
    text = _build_synthetic_batch_response(boundary, [(200, {"value": []})])
    results = parse_batch_response(text, boundary)
    assert len(results) == 1
    assert results[0]["status_code"] == 200


def test_parse_batch_response_single_part_body() -> None:
    """parse_batch_response returns the parsed JSON body for a single part."""
    boundary = "resp_boundary"
    body_obj = {"value": [{"id": "abc"}], "@odata.count": 1}
    text = _build_synthetic_batch_response(boundary, [(200, body_obj)])
    results = parse_batch_response(text, boundary)
    assert results[0]["body"] == body_obj


def test_parse_batch_response_multiple_parts() -> None:
    """parse_batch_response returns one entry per response part."""
    boundary = "resp_boundary"
    parts = [
        (200, {"name": "Acme"}),
        (204, None),
    ]
    text = _build_synthetic_batch_response(boundary, parts)
    results = parse_batch_response(text, boundary)
    assert len(results) == 2
    assert results[0]["status_code"] == 200
    assert results[1]["status_code"] == 204


def test_parse_batch_response_no_body_part_returns_none_or_empty() -> None:
    """A part with no body produces None or an empty body (not an error)."""
    boundary = "resp_boundary"
    text = _build_synthetic_batch_response(boundary, [(204, None)])
    results = parse_batch_response(text, boundary)
    assert len(results) == 1
    # body may be None or empty string for a 204 with no payload
    assert results[0]["body"] is None or results[0]["body"] == ""


def test_parse_batch_response_error_status_code() -> None:
    """parse_batch_response captures non-2xx status codes correctly."""
    boundary = "resp_boundary"
    error_body = {"error": {"code": "0x80040217", "message": "Not found"}}
    text = _build_synthetic_batch_response(boundary, [(404, error_body)])
    results = parse_batch_response(text, boundary)
    assert results[0]["status_code"] == 404
    assert results[0]["body"] == error_body
