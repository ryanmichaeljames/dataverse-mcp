"""Unit tests for request_with_retry idempotency gating on 5xx responses.

Acceptance criteria:
- POST/PATCH receiving 502/503/504 returns immediately (exactly one HTTP request).
- GET/PUT/DELETE receiving 5xx retries up to max_attempts.
- Any method receiving 429 retries (rejected before processing).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dataverse_mcp.client import request_with_retry


def _mock_response(status_code: int) -> httpx.Response:
    """Build a minimal httpx.Response with the given status code."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.headers = httpx.Headers({})
    return response


def _make_client(responses: list) -> httpx.AsyncClient:
    """Return a mock AsyncClient whose .request() yields responses in order."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=responses)
    return client


# ---------------------------------------------------------------------------
# POST/PATCH + 5xx: must NOT retry (return immediately, one request issued)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("status_code", [502, 503, 504])
async def test_non_idempotent_5xx_no_retry(method: str, status_code: int) -> None:
    """POST and PATCH must return a 5xx immediately without retrying."""
    response = _mock_response(status_code)
    client = _make_client([response])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await request_with_retry(
            client, method, "https://example.com/api", max_attempts=3
        )

    assert result.status_code == status_code
    assert client.request.call_count == 1, (
        f"{method} {status_code}: expected 1 request, got {client.request.call_count}"
    )
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# GET/PUT/DELETE + 5xx: must retry up to max_attempts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
@pytest.mark.parametrize("status_code", [502, 503, 504])
async def test_idempotent_5xx_retries(method: str, status_code: int) -> None:
    """GET, PUT, and DELETE must retry on 5xx up to max_attempts."""
    max_attempts = 3
    responses = [_mock_response(status_code)] * max_attempts
    client = _make_client(responses)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await request_with_retry(
            client, method, "https://example.com/api", max_attempts=max_attempts
        )

    assert result.status_code == status_code
    assert client.request.call_count == max_attempts, (
        f"{method} {status_code}: expected {max_attempts} requests, "
        f"got {client.request.call_count}"
    )


# ---------------------------------------------------------------------------
# GET/PUT/DELETE + 5xx: succeeds on retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
async def test_idempotent_5xx_succeeds_on_retry(method: str) -> None:
    """Idempotent method recovers when a retry returns 200."""
    responses = [_mock_response(503), _mock_response(200)]
    client = _make_client(responses)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await request_with_retry(
            client, method, "https://example.com/api", max_attempts=3
        )

    assert result.status_code == 200
    assert client.request.call_count == 2


# ---------------------------------------------------------------------------
# 429: all methods must retry (throttle means request was not processed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH", "GET", "PUT", "DELETE"])
async def test_429_retries_all_methods(method: str) -> None:
    """429 must trigger a retry regardless of HTTP method."""
    max_attempts = 3
    responses = [_mock_response(429)] * max_attempts
    client = _make_client(responses)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await request_with_retry(
            client, method, "https://example.com/api", max_attempts=max_attempts
        )

    assert result.status_code == 429
    assert client.request.call_count == max_attempts, (
        f"{method} 429: expected {max_attempts} requests, "
        f"got {client.request.call_count}"
    )


@pytest.mark.asyncio
async def test_post_429_succeeds_on_retry() -> None:
    """POST recovering from 429 on retry returns 200."""
    responses = [_mock_response(429), _mock_response(201)]
    client = _make_client(responses)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await request_with_retry(
            client, "POST", "https://example.com/api", max_attempts=3
        )

    assert result.status_code == 201
    assert client.request.call_count == 2
