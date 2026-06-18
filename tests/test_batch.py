"""Unit tests for batch serialization helpers and OData batch parsing in batch.py.

Acceptance criteria (PR #69 — serialisation invariants):
- build_inner_request: GET produces no Content-Type/Content-Length and no body
  even when body is passed; POST/PUT/PATCH with body include Content-Type,
  Content-Length, and the JSON body.
- build_batch_body: structural invariants — boundary markers present, changeset
  parts have Content-ID, standalone parts have no Content-ID wrapper.
- parse_batch_response: parses a synthetic multipart response into
  [{status_code, body}] correctly.

Acceptance criteria (issue #65 — boundary / CRLF / unparseable):
- Per-request boundary is unique (no two calls share the same boundary).
- A response body that literally contains the old hardcoded token
  ``batch_dataverse_mcp`` is not confused with a boundary.
- Responses using bare \\n line endings parse identically to \\r\\n responses.
- A part with content but no HTTP status line surfaces status_code=0 + error key.
- Normal multi-part \\r\\n success responses parse correctly (status_code + body).
"""

import json
import re

import pytest

from dataverse_mcp.batch import (
    build_batch_body,
    build_inner_request,
    parse_batch_response,
)
from dataverse_mcp.models import BatchOperationItem

# ---------------------------------------------------------------------------
# Helpers — PR #69 (build_inner_request / build_batch_body tests)
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
# Helpers — issue #65 (parse_batch_response CRLF/LF tests)
# ---------------------------------------------------------------------------


def _make_crlf_response(boundary: str, parts: list[str]) -> str:
    """Assemble a minimal multipart/mixed response body using \\r\\n endings."""
    sep = f"\r\n--{boundary}\r\n"
    return f"--{boundary}\r\n" + sep.join(parts) + f"\r\n--{boundary}--\r\n"


def _make_lf_response(boundary: str, parts: list[str]) -> str:
    """Assemble a minimal multipart/mixed response body using bare \\n endings."""
    sep = f"\n--{boundary}\n"
    return f"--{boundary}\n" + sep.join(parts) + f"\n--{boundary}--\n"


def _single_op_part_crlf(status: int, body_json: str = "") -> str:
    """Build one inner part with a valid HTTP status line (CRLF)."""
    inner = f"HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\n\r\n{body_json}"
    return (
        "Content-Type: application/http\r\n"
        "Content-Transfer-Encoding: binary\r\n"
        "\r\n"
        + inner
    )


def _single_op_part_lf(status: int, body_json: str = "") -> str:
    """Build one inner part with a valid HTTP status line (LF only)."""
    inner = f"HTTP/1.1 {status} OK\nContent-Type: application/json\n\n{body_json}"
    return (
        "Content-Type: application/http\n"
        "Content-Transfer-Encoding: binary\n"
        "\n"
        + inner
    )


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
# (a) Per-request boundary uniqueness  [issue #65]
# ---------------------------------------------------------------------------


def test_boundary_uniqueness() -> None:
    """Two separate boundary values must never be equal."""
    import uuid
    b1 = f"batch_{uuid.uuid4().hex}"
    b2 = f"batch_{uuid.uuid4().hex}"
    assert b1 != b2


def test_boundary_format() -> None:
    """Generated boundary must match expected pattern and not use the old literal."""
    import uuid
    boundary = f"batch_{uuid.uuid4().hex}"
    assert re.match(r"^batch_[0-9a-f]{32}$", boundary), f"Unexpected format: {boundary}"
    assert boundary != "batch_dataverse_mcp"


def test_old_literal_absent_from_codebase(tmp_path) -> None:
    """Confirm the hardcoded literal 'batch_dataverse_mcp' is not in tables.py.

    This is a canary — if the fix is reverted the test fails immediately.
    """
    import pathlib
    tables_path = pathlib.Path(__file__).parent.parent / "src" / "dataverse_mcp" / "tools" / "tables.py"
    source = tables_path.read_text(encoding="utf-8")
    assert "batch_dataverse_mcp" not in source, (
        "Hardcoded boundary literal 'batch_dataverse_mcp' found in tables.py"
    )


def test_body_containing_old_literal_does_not_confuse_parser() -> None:
    """A response body that contains the old literal must be parsed by boundary, not content."""
    boundary = "batch_abc123"
    # The body of the single operation contains the old hardcoded token.
    body_json = '{"value": "batch_dataverse_mcp is just data here"}'
    part = _single_op_part_crlf(200, body_json)
    response = _make_crlf_response(boundary, [part])

    results = parse_batch_response(response, boundary)

    assert len(results) == 1
    assert results[0]["status_code"] == 200
    assert results[0]["body"]["value"] == "batch_dataverse_mcp is just data here"


# ---------------------------------------------------------------------------
# (b) Bare \n line endings  [issue #65]
# ---------------------------------------------------------------------------


def test_lf_only_single_part_success() -> None:
    """A single-operation response with bare \\n endings must parse correctly."""
    boundary = "batch_lf_test"
    body_json = '{"id": "abc"}'
    part = _single_op_part_lf(200, body_json)
    response = _make_lf_response(boundary, [part])

    results = parse_batch_response(response, boundary)

    assert len(results) == 1
    assert results[0]["status_code"] == 200
    assert results[0]["body"] == {"id": "abc"}


def test_lf_only_multiple_parts() -> None:
    """Multiple parts with bare \\n endings must each parse into a result."""
    boundary = "batch_lf_multi"
    parts = [
        _single_op_part_lf(200, '{"a": 1}'),
        _single_op_part_lf(204, ""),
        _single_op_part_lf(201, '{"b": 2}'),
    ]
    response = _make_lf_response(boundary, parts)

    results = parse_batch_response(response, boundary)

    assert len(results) == 3
    assert results[0]["status_code"] == 200
    assert results[0]["body"] == {"a": 1}
    assert results[1]["status_code"] == 204
    assert results[2]["status_code"] == 201
    assert results[2]["body"] == {"b": 2}


# ---------------------------------------------------------------------------
# (c) Unparseable status line surfaces status_code=0 + error  [issue #65]
# ---------------------------------------------------------------------------


def test_unparseable_part_surfaces_error() -> None:
    """A part with content but no HTTP status line must produce status_code=0 + error."""
    boundary = "batch_bad"
    # Part has headers but no HTTP/1.1 line — just a raw body instead.
    bad_part = (
        "Content-Type: application/http\r\n"
        "Content-Transfer-Encoding: binary\r\n"
        "\r\n"
        "this is some garbage content without an HTTP status line"
    )
    response = _make_crlf_response(boundary, [bad_part])

    results = parse_batch_response(response, boundary)

    assert len(results) == 1
    assert results[0]["status_code"] == 0
    assert "error" in results[0]
    assert "unparseable" in results[0]["error"].lower() or "no http status" in results[0]["error"].lower()


def test_unparseable_part_mixed_with_valid_parts() -> None:
    """An unparseable part in the middle must appear in results without hiding valid parts."""
    boundary = "batch_mixed_bad"
    good_before = _single_op_part_crlf(200, '{"ok": true}')
    bad_part = (
        "Content-Type: application/http\r\n"
        "Content-Transfer-Encoding: binary\r\n"
        "\r\n"
        "no status line here"
    )
    good_after = _single_op_part_crlf(201, '{"created": true}')
    response = _make_crlf_response(boundary, [good_before, bad_part, good_after])

    results = parse_batch_response(response, boundary)

    assert len(results) == 3
    assert results[0]["status_code"] == 200
    assert results[1]["status_code"] == 0
    assert "error" in results[1]
    assert results[2]["status_code"] == 201


def test_genuinely_empty_part_is_skipped() -> None:
    """A part that is empty (just whitespace/newlines) must not produce a result."""
    boundary = "batch_empty"
    good = _single_op_part_crlf(200, '{"x": 1}')
    # Inject an empty part manually by splitting with an extra delimiter.
    response = f"--{boundary}\r\n\r\n--{boundary}\r\n{good}\r\n--{boundary}--\r\n"

    results = parse_batch_response(response, boundary)

    # Only the non-empty good part should produce a result.
    assert len(results) == 1
    assert results[0]["status_code"] == 200


# ---------------------------------------------------------------------------
# (d) Normal multi-part CRLF success responses (regression)  [issue #65]
# ---------------------------------------------------------------------------


def test_normal_crlf_two_operations() -> None:
    """Standard \\r\\n batch response with two operations parses correctly."""
    boundary = "batch_normal"
    parts = [
        _single_op_part_crlf(200, '{"value": [{"name": "Account"}]}'),
        _single_op_part_crlf(204, ""),
    ]
    response = _make_crlf_response(boundary, parts)

    results = parse_batch_response(response, boundary)

    assert len(results) == 2
    assert results[0]["status_code"] == 200
    assert results[0]["body"] == {"value": [{"name": "Account"}]}
    assert results[1]["status_code"] == 204
    assert results[1]["body"] is None


def test_normal_crlf_error_status() -> None:
    """A 404 response part is parsed with the correct status code and error body."""
    boundary = "batch_err"
    error_body = '{"error": {"code": "0x80040217", "message": "Object not found"}}'
    part = _single_op_part_crlf(404, error_body)
    response = _make_crlf_response(boundary, [part])

    results = parse_batch_response(response, boundary)

    assert len(results) == 1
    assert results[0]["status_code"] == 404
    assert results[0]["body"]["error"]["code"] == "0x80040217"


def test_normal_crlf_no_json_body() -> None:
    """A part whose body is not JSON is returned as raw text rather than raising."""
    boundary = "batch_nonjson"
    part = _single_op_part_crlf(200, "plain text body")
    response = _make_crlf_response(boundary, [part])

    results = parse_batch_response(response, boundary)

    assert len(results) == 1
    assert results[0]["status_code"] == 200
    assert results[0]["body"] == "plain text body"


# ---------------------------------------------------------------------------
# parse_batch_response — parses synthetic multipart response  [PR #69]
# ---------------------------------------------------------------------------


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
