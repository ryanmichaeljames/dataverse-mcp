"""Regression tests for OData batch serializer (_build_batch_body) and response
parser (_parse_batch_response).

Covers the bugs reported in issue #34:
- 0x80048d19: stream not readable (missing Content-Length on write ops)
- 0x80060888: missing Content-ID in change set operations
- RecursionError in _parse_batch_response for change set responses
"""

import json
import re
import unittest

from dataverse_mcp.tools.tables import _build_batch_body, _build_inner_request, _parse_batch_response
from dataverse_mcp.models import ExecuteBatchInput, BatchOperationItem

_BASE = "https://org.crm.dynamics.com"
_BOUNDARY = "batch_test"
_API = "v9.2"


def _make_op(**kwargs) -> BatchOperationItem:
    return BatchOperationItem(**kwargs)


def _parse_parts(body: str, boundary: str) -> list[str]:
    """Split a batch body into its top-level parts (excluding preamble/epilogue)."""
    parts = body.split(f"--{boundary}")
    return [p.strip() for p in parts if p.strip() and p.strip() != "--"]


class TestBuildInnerRequest(unittest.TestCase):
    """Unit tests for the inner HTTP request builder."""

    def test_get_no_body(self):
        inner = _build_inner_request("GET", f"{_BASE}/api/data/{_API}/WhoAmI()", None)
        self.assertIn("GET ", inner)
        self.assertNotIn("Content-Type", inner)
        self.assertNotIn("Content-Length", inner)
        # Should end with blank line (CRLF)
        self.assertTrue(inner.endswith("\r\n"))

    def test_post_with_body_has_content_length(self):
        body = {"name": "Test Account"}
        inner = _build_inner_request("POST", f"{_BASE}/api/data/{_API}/accounts", body)
        body_bytes = json.dumps(body).encode("utf-8")
        self.assertIn(f"Content-Length: {len(body_bytes)}", inner)
        self.assertIn("Content-Type: application/json", inner)
        self.assertIn(json.dumps(body), inner)

    def test_patch_with_body_has_content_length(self):
        body = {"name": "Updated"}
        inner = _build_inner_request("PATCH", f"{_BASE}/api/data/{_API}/accounts(abc)", body)
        body_bytes = json.dumps(body).encode("utf-8")
        self.assertIn(f"Content-Length: {len(body_bytes)}", inner)

    def test_delete_no_body(self):
        inner = _build_inner_request("DELETE", f"{_BASE}/api/data/{_API}/accounts(abc)", None)
        self.assertNotIn("Content-Type", inner)
        self.assertNotIn("Content-Length", inner)

    def test_inner_request_uses_crlf(self):
        inner = _build_inner_request("GET", f"{_BASE}/api/data/{_API}/WhoAmI()", None)
        self.assertNotIn("\r\r\n", inner)
        self.assertIn("\r\n", inner)


class TestBatchGetSingleOp(unittest.TestCase):
    """Batch with a single GET operation — control case (must keep working)."""

    def setUp(self):
        self.ops = [_make_op(method="GET", url="/WhoAmI()")]
        self.body = _build_batch_body(self.ops, _BASE, _BOUNDARY)

    def test_opens_and_closes_boundary(self):
        self.assertTrue(self.body.startswith(f"--{_BOUNDARY}\r\n"))
        self.assertTrue(self.body.endswith(f"--{_BOUNDARY}--"))

    def test_part_has_application_http_content_type(self):
        self.assertIn("Content-Type: application/http", self.body)

    def test_part_has_transfer_encoding_binary(self):
        self.assertIn("Content-Transfer-Encoding: binary", self.body)

    def test_get_part_has_no_body_content_type(self):
        # GET operations must NOT carry Content-Type: application/json
        parts = _parse_parts(self.body, _BOUNDARY)
        self.assertEqual(len(parts), 1)
        # The part header section (before the blank line + inner request) should
        # not mention application/json for a GET
        part = parts[0]
        # Find inner request (after part headers blank line)
        inner_start = part.find("GET ")
        self.assertGreater(inner_start, 0)
        inner_section = part[inner_start:]
        # No Content-Type line in the inner GET request
        self.assertNotIn("Content-Type: application/json", inner_section)


class TestBatchPostSingleOp(unittest.TestCase):
    """Batch with a single POST operation — previously failed with 0x80048d19."""

    def setUp(self):
        self.payload = {"name": "Test Account"}
        self.ops = [_make_op(method="POST", url="/accounts", body=self.payload)]
        self.body = _build_batch_body(self.ops, _BASE, _BOUNDARY)

    def test_opens_and_closes_boundary(self):
        self.assertTrue(self.body.startswith(f"--{_BOUNDARY}\r\n"))
        self.assertTrue(self.body.endswith(f"--{_BOUNDARY}--"))

    def test_post_part_has_content_length(self):
        body_bytes = json.dumps(self.payload).encode("utf-8")
        self.assertIn(f"Content-Length: {len(body_bytes)}", self.body)

    def test_post_part_has_content_type_json(self):
        self.assertIn("Content-Type: application/json", self.body)

    def test_post_body_present(self):
        self.assertIn(json.dumps(self.payload), self.body)

    def test_no_content_id_on_standalone_op(self):
        # Standalone (non-change-set) ops must NOT have Content-ID
        parts = _parse_parts(self.body, _BOUNDARY)
        self.assertEqual(len(parts), 1)
        # Content-ID should only appear inside change sets
        part_headers_end = parts[0].find("\r\n\r\n")
        part_headers = parts[0][:part_headers_end]
        self.assertNotIn("Content-ID", part_headers)


class TestBatchPostWithChangeSet(unittest.TestCase):
    """Batch POST inside a change set — previously failed with 0x80060888."""

    def setUp(self):
        self.payload1 = {"name": "Account A"}
        self.payload2 = {"name": "Account B"}
        self.ops = [
            _make_op(method="POST", url="/accounts", body=self.payload1, change_set_id="cs1"),
            _make_op(method="POST", url="/accounts", body=self.payload2, change_set_id="cs1"),
        ]
        self.body = _build_batch_body(self.ops, _BASE, _BOUNDARY)

    def test_change_set_boundary_present(self):
        self.assertIn("changeset_cs1", self.body)

    def test_change_set_content_type_header(self):
        self.assertIn("Content-Type: multipart/mixed; boundary=changeset_cs1", self.body)

    def test_content_id_present_on_each_op(self):
        # Both ops must have Content-ID headers (1-based)
        self.assertIn("Content-ID: 1", self.body)
        self.assertIn("Content-ID: 2", self.body)

    def test_content_length_present_on_each_op(self):
        body1_bytes = json.dumps(self.payload1).encode("utf-8")
        body2_bytes = json.dumps(self.payload2).encode("utf-8")
        self.assertIn(f"Content-Length: {len(body1_bytes)}", self.body)
        self.assertIn(f"Content-Length: {len(body2_bytes)}", self.body)

    def test_both_bodies_present(self):
        self.assertIn(json.dumps(self.payload1), self.body)
        self.assertIn(json.dumps(self.payload2), self.body)

    def test_change_set_closes(self):
        self.assertIn("--changeset_cs1--", self.body)


class TestBatchMixedOps(unittest.TestCase):
    """Mixed batch: standalone GET + change set POST."""

    def setUp(self):
        self.get_op = _make_op(method="GET", url="/WhoAmI()")
        self.post_op = _make_op(
            method="POST", url="/accounts", body={"name": "X"}, change_set_id="cs2"
        )
        self.ops = [self.get_op, self.post_op]
        self.body = _build_batch_body(self.ops, _BASE, _BOUNDARY)

    def test_batch_boundary_used_twice_or_more(self):
        # At least: opening, change set part, closing
        count = self.body.count(f"--{_BOUNDARY}")
        self.assertGreaterEqual(count, 3)

    def test_change_set_present(self):
        self.assertIn("changeset_cs2", self.body)

    def test_get_present(self):
        self.assertIn("GET ", self.body)
        self.assertIn("/WhoAmI()", self.body)

    def test_content_id_only_in_change_set(self):
        self.assertIn("Content-ID: 1", self.body)

    def test_crlf_boundaries(self):
        # No bare \n that isn't preceded by \r
        lines = self.body.split("\n")
        for line in lines[:-1]:  # last may be empty
            self.assertTrue(
                line.endswith("\r") or line == "",
                f"Line missing CRLF: {line!r}",
            )


class TestBatchExecuteInputValidation(unittest.TestCase):
    """Validate that ExecuteBatchInput Pydantic model enforces constraints."""

    def test_valid_input_parses(self):
        data = {
            "operations": [{"method": "GET", "url": "/WhoAmI()"}],
            "dataverse_url": "https://org.crm.dynamics.com",
        }
        model = ExecuteBatchInput(**data)
        self.assertEqual(len(model.operations), 1)
        self.assertEqual(model.operations[0].method, "GET")

    def test_empty_operations_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExecuteBatchInput(operations=[])

    def test_continue_on_error_defaults_false(self):
        model = ExecuteBatchInput(operations=[{"method": "GET", "url": "/WhoAmI()"}])
        self.assertFalse(model.continue_on_error)


class TestParseBatchResponse(unittest.TestCase):
    """Regression tests for _parse_batch_response to prevent RecursionError."""

    def _make_changeset_response(self, cs_boundary: str, outer_boundary: str) -> str:
        """Build a synthetic batch response containing a change set."""
        cs_part = (
            f"Content-Type: application/http\r\n"
            f"Content-Transfer-Encoding: binary\r\n"
            f"\r\n"
            f"HTTP/1.1 204 No Content\r\n"
            f"OData-Version: 4.0\r\n"
            f"\r\n"
        )
        cs_body = (
            f"--{cs_boundary}\r\n"
            f"{cs_part}"
            f"--{cs_boundary}--"
        )
        response = (
            f"--{outer_boundary}\r\n"
            f"Content-Type: multipart/mixed; boundary={cs_boundary}\r\n"
            f"\r\n"
            f"{cs_body}\r\n"
            f"--{outer_boundary}--"
        )
        return response

    def test_change_set_response_no_recursion_error(self):
        """_parse_batch_response must not raise RecursionError for change set responses."""
        response = self._make_changeset_response("changesetresponse_cs1", "batchresponse_1")
        # Should not raise
        results = _parse_batch_response(response, "batchresponse_1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status_code"], 204)

    def test_standalone_get_response_parsed(self):
        """A single GET response is parsed correctly."""
        body_json = json.dumps({"UserId": "abc-123"})
        response = (
            "--batchresponse\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            f"{body_json}\r\n"
            "--batchresponse--"
        )
        results = _parse_batch_response(response, "batchresponse")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status_code"], 200)
        self.assertEqual(results[0]["body"]["UserId"], "abc-123")

    def test_mixed_response_get_plus_changeset(self):
        """Mixed response: a GET result + a change set result are both parsed."""
        get_part = (
            "--batchresponse\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 200 OK\r\n"
            "\r\n"
            '{"ok": true}\r\n'
        )
        cs_inner = (
            "--cs_boundary\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "\r\n"
            "--cs_boundary--"
        )
        cs_part = (
            "--batchresponse\r\n"
            "Content-Type: multipart/mixed; boundary=cs_boundary\r\n"
            "\r\n"
            f"{cs_inner}\r\n"
        )
        response = get_part + cs_part + "--batchresponse--"
        results = _parse_batch_response(response, "batchresponse")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["status_code"], 200)
        self.assertEqual(results[1]["status_code"], 204)


if __name__ == "__main__":
    unittest.main()
