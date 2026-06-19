"""Unit tests for auth error handling and bounded credential acquisition.

Acceptance criteria:
- (a) ClientAuthenticationError passed to tool_error_response returns JSON with
  error=True and an actionable auth message that mentions az login and
  DATAVERSE_AUTH_TYPE; it must NOT say "Unexpected error: ...".
- (b) When the credential's blocking call exceeds the configured timeout,
  build_headers raises a ClientAuthenticationError whose message surfaces
  through tool_error_response as the actionable auth message rather than
  hanging; AND after the timeout, the per-scope lock is free so a subsequent
  build_headers call for the same scope can proceed.
- Existing httpx.HTTPStatusError and other branches in tool_error_response are
  not broken.
"""

import asyncio
import json
import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError

from dataverse_mcp.client import (
    _DEFAULT_AUTH_TIMEOUT_SECONDS,
    AppContext,
    _get_auth_timeout_seconds,
    build_headers,
    tool_error_response,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_ctx(credential: Any | None = None) -> AppContext:
    """Build a minimal AppContext with a mock HTTP client."""
    http_client = MagicMock(spec=httpx.AsyncClient)
    return AppContext(
        credential=credential or MagicMock(),
        auth_type="azure_cli",
        http_client=http_client,
        _token_cache={},
        _token_locks={},
    )


# ---------------------------------------------------------------------------
# tool_error_response — ClientAuthenticationError branch
# ---------------------------------------------------------------------------


def test_tool_error_response_auth_error_returns_actionable_message() -> None:
    """ClientAuthenticationError maps to an actionable auth message, not 'Unexpected error'."""
    exc = ClientAuthenticationError(message="DefaultAzureCredential failed to retrieve a token")
    result = tool_error_response(exc, "some_tool")
    payload = json.loads(result)

    assert payload["error"] is True
    message = payload["message"]
    assert "az login" in message, "message should mention az login"
    assert "DATAVERSE_AUTH_TYPE" in message, "message should mention DATAVERSE_AUTH_TYPE"
    assert "Unexpected error" not in message, (
        "message must NOT fall through to the broad 'Unexpected error' fallback"
    )


def test_tool_error_response_auth_error_does_not_leak_credential_detail() -> None:
    """The returned message is a fixed string, not str(e), so cred details do not leak."""
    sensitive = "secret-token-value-xyzzy"
    exc = ClientAuthenticationError(message=sensitive)
    result = tool_error_response(exc, "some_tool")
    payload = json.loads(result)

    assert sensitive not in payload["message"], (
        "Credential exception detail must not appear in the returned message"
    )


def test_tool_error_response_auth_error_is_not_transient() -> None:
    """Auth errors do not carry is_transient; they require user action."""
    exc = ClientAuthenticationError(message="failed")
    result = tool_error_response(exc, "some_tool")
    payload = json.loads(result)

    assert "is_transient" not in payload


# ---------------------------------------------------------------------------
# tool_error_response — existing branches are unaffected
# ---------------------------------------------------------------------------


def test_tool_error_response_http_status_error_unaffected() -> None:
    """HTTPStatusError branch still works after adding ClientAuthenticationError branch."""
    response = httpx.Response(404, content=b'{"error":{"code":"ObjectNotFound","message":"Not found"}}')
    exc = httpx.HTTPStatusError("404", request=MagicMock(), response=response)
    result = tool_error_response(exc, "some_tool")
    payload = json.loads(result)

    assert payload["error"] is True
    assert "404" in payload["message"]


def test_tool_error_response_value_error_unaffected() -> None:
    """ValueError branch still works."""
    exc = ValueError("dataverse_url must use https")
    result = tool_error_response(exc, "some_tool")
    payload = json.loads(result)

    assert payload["error"] is True
    assert "dataverse_url must use https" in payload["message"]


def test_tool_error_response_unexpected_error_fallback_unaffected() -> None:
    """Broad Exception fallback still triggers for unknown exception types."""
    exc = RuntimeError("something unexpected")
    result = tool_error_response(exc, "some_tool")
    payload = json.loads(result)

    assert payload["error"] is True
    assert "Unexpected error" in payload["message"]


# ---------------------------------------------------------------------------
# _get_auth_timeout_seconds — env var parsing
# ---------------------------------------------------------------------------


def test_get_auth_timeout_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns _DEFAULT_AUTH_TIMEOUT_SECONDS when env var is absent."""
    monkeypatch.delenv("DATAVERSE_AUTH_TIMEOUT_SECONDS", raising=False)
    assert _get_auth_timeout_seconds() == _DEFAULT_AUTH_TIMEOUT_SECONDS


def test_get_auth_timeout_returns_custom_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns the float value when env var is a valid positive number."""
    monkeypatch.setenv("DATAVERSE_AUTH_TIMEOUT_SECONDS", "10")
    assert _get_auth_timeout_seconds() == 10.0


def test_get_auth_timeout_falls_back_on_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to default when env var is not a number."""
    monkeypatch.setenv("DATAVERSE_AUTH_TIMEOUT_SECONDS", "not-a-number")
    assert _get_auth_timeout_seconds() == _DEFAULT_AUTH_TIMEOUT_SECONDS


def test_get_auth_timeout_falls_back_on_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to default when env var is 0 (non-positive)."""
    monkeypatch.setenv("DATAVERSE_AUTH_TIMEOUT_SECONDS", "0")
    assert _get_auth_timeout_seconds() == _DEFAULT_AUTH_TIMEOUT_SECONDS


def test_get_auth_timeout_falls_back_on_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to default when env var is negative."""
    monkeypatch.setenv("DATAVERSE_AUTH_TIMEOUT_SECONDS", "-5")
    assert _get_auth_timeout_seconds() == _DEFAULT_AUTH_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# build_headers — timeout surfaces as ClientAuthenticationError + lock released
# ---------------------------------------------------------------------------


async def test_build_headers_timeout_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When credential.get_token blocks beyond the timeout, build_headers raises
    ClientAuthenticationError.  Uses a very short timeout so the test completes fast.
    """
    # Patch the module constant so _get_auth_timeout_seconds returns 0.05 s.
    monkeypatch.setattr("dataverse_mcp.client._DEFAULT_AUTH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.delenv("DATAVERSE_AUTH_TIMEOUT_SECONDS", raising=False)

    def _slow_get_token(scope: str):
        # Block much longer than the 0.05 s timeout.
        time.sleep(5)
        token = MagicMock()
        token.token = "tok"
        token.expires_on = time.time() + 3600
        return token

    credential = MagicMock()
    credential.get_token = _slow_get_token
    app_ctx = _make_app_ctx(credential)

    with pytest.raises(ClientAuthenticationError) as exc_info:
        await build_headers(app_ctx, "https://example.crm.dynamics.com")

    assert "timed out" in str(exc_info.value).lower()


async def test_build_headers_timeout_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a timeout the per-scope lock is released; a subsequent call that
    succeeds immediately can acquire the lock and return headers.
    """
    # Phase 1: short timeout so the slow credential times out.
    monkeypatch.setattr("dataverse_mcp.client._DEFAULT_AUTH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.delenv("DATAVERSE_AUTH_TIMEOUT_SECONDS", raising=False)

    base_url = "https://example.crm.dynamics.com"
    scope = f"{base_url}/.default"

    # First credential: deliberately blocks longer than the timeout.
    def _slow_get_token(scope_arg: str):
        time.sleep(5)
        tok = MagicMock()
        tok.token = "slow-tok"
        tok.expires_on = time.time() + 3600
        return tok

    slow_credential = MagicMock()
    slow_credential.get_token = _slow_get_token
    app_ctx = _make_app_ctx(slow_credential)

    # First call: should raise ClientAuthenticationError from the timeout.
    with pytest.raises(ClientAuthenticationError):
        await build_headers(app_ctx, base_url)

    # Verify the lock exists and is NOT held (released by async with on exception).
    lock = app_ctx._token_locks.get(scope)
    assert lock is not None, "per-scope lock should have been created"
    assert not lock.locked(), "lock must be released after a timeout exception"

    # Phase 2: increase the timeout so a fast credential can succeed.
    monkeypatch.setattr("dataverse_mcp.client._DEFAULT_AUTH_TIMEOUT_SECONDS", 5.0)

    def _fast_get_token(scope_arg: str):
        tok = MagicMock()
        tok.token = "fast-tok"
        tok.expires_on = time.time() + 3600
        return tok

    # Replace the credential on the same app_ctx so cache is still empty.
    app_ctx.credential = MagicMock()
    app_ctx.credential.get_token = _fast_get_token

    # Second call: should succeed and return headers with the fast token.
    headers = await build_headers(app_ctx, base_url)
    assert headers["Authorization"] == "Bearer fast-tok"
