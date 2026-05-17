"""Shared OData batch serialization and response parsing helpers."""

import json
import logging

from dataverse_mcp.client import _DATAVERSE_API_VERSION

logger = logging.getLogger(__name__)


def build_inner_request(method: str, url: str, body: dict | None) -> str:
    """Build the inner HTTP/1.1 request string for a single batch operation."""
    inner = f"{method} {url} HTTP/1.1\r\nAccept: application/json\r\n"
    if method.upper() in ("POST", "PUT", "PATCH") and body is not None:
        body_bytes = json.dumps(body).encode("utf-8")
        inner += f"Content-Type: application/json\r\nContent-Length: {len(body_bytes)}\r\n\r\n"
        inner += body_bytes.decode("utf-8")
    else:
        inner += "\r\n"
    return inner


def build_batch_body(operations: list, base_url: str, batch_boundary: str) -> str:
    """Build an OData-compliant multipart/mixed batch request body."""
    parts: list[str] = []

    ops = list(operations)
    processed_change_sets: set[str] = set()
    i = 0

    while i < len(ops):
        op = ops[i]
        if op.change_set_id and op.change_set_id not in processed_change_sets:
            cs_id = op.change_set_id
            cs_boundary = f"changeset_{cs_id}"
            processed_change_sets.add(cs_id)

            cs_ops = [o for o in ops if o.change_set_id == cs_id]
            cs_parts: list[str] = []
            for cs_idx, cs_op in enumerate(cs_ops):
                inner_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}{cs_op.url}"
                inner = build_inner_request(cs_op.method, inner_url, cs_op.body)
                op_part = (
                    f"Content-Type: application/http\r\n"
                    f"Content-Transfer-Encoding: binary\r\n"
                    f"Content-ID: {cs_idx + 1}\r\n"
                    f"\r\n"
                    f"{inner}"
                )
                logger.debug(
                    "Batch change set op %d/%d [%s]: %s %s",
                    cs_idx + 1, len(cs_ops), cs_id, cs_op.method, inner_url,
                )
                cs_parts.append(op_part)

            cs_body = f"\r\n--{cs_boundary}\r\n".join(cs_parts)
            part = (
                f"Content-Type: multipart/mixed; boundary={cs_boundary}\r\n"
                f"\r\n"
                f"--{cs_boundary}\r\n{cs_body}\r\n--{cs_boundary}--"
            )
            parts.append(part)
        elif not op.change_set_id:
            inner_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}{op.url}"
            inner = build_inner_request(op.method, inner_url, op.body)
            part = (
                f"Content-Type: application/http\r\n"
                f"Content-Transfer-Encoding: binary\r\n"
                f"\r\n"
                f"{inner}"
            )
            logger.debug("Batch standalone op: %s %s", op.method, inner_url)
            parts.append(part)
        i += 1

    logger.debug(
        "Building batch body: boundary=%s, parts=%d", batch_boundary, len(parts)
    )
    body = (
        f"--{batch_boundary}\r\n"
        + f"\r\n--{batch_boundary}\r\n".join(parts)
        + f"\r\n--{batch_boundary}--"
    )
    return body


def parse_batch_response(response_text: str, boundary: str) -> list[dict]:
    """Parse a multipart/mixed batch response into per-operation results."""
    results: list[dict] = []
    parts = response_text.split(f"--{boundary}")

    for part in parts:
        part = part.strip()
        if not part or part == "--":
            continue

        if "\r\n\r\n" in part:
            part_headers, part_body = part.split("\r\n\r\n", 1)
        else:
            part_headers = part
            part_body = ""

        if "multipart/mixed" in part_headers:
            inner_boundary_match = part_headers.find("boundary=")
            if inner_boundary_match != -1:
                inner_boundary = (
                    part_headers[inner_boundary_match + 9:]
                    .split("\r\n")[0]
                    .split(";")[0]
                    .strip()
                )
                inner_results = parse_batch_response(part_body, inner_boundary)
                results.extend(inner_results)
            continue

        lines = part_body.split("\r\n")
        http_status_line = None
        body_start = 0
        for j, line in enumerate(lines):
            if line.startswith("HTTP/1.1"):
                http_status_line = line
                body_start = j + 1
                break

        if not http_status_line:
            continue

        try:
            status_code = int(http_status_line.split(" ")[1])
        except (IndexError, ValueError):
            status_code = 0

        while body_start < len(lines) and lines[body_start].strip():
            body_start += 1
        body_start += 1

        body_text = "\r\n".join(lines[body_start:]).strip()
        body_json = None
        if body_text:
            try:
                body_json = json.loads(body_text)
            except Exception:
                body_json = body_text

        results.append({"status_code": status_code, "body": body_json})

    return results
