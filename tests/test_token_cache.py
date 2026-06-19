"""Unit tests for token-cache persistence env parsers and _build_credential kwarg wiring.

Acceptance criteria:
- _get_token_cache_persist: 'true'/'TRUE' -> True; 'false'/'FALSE' -> False;
  unset -> True (default-on); garbage -> True with logged warning.
- _get_token_cache_allow_unencrypted: 'true'/'TRUE' -> True; 'false'/'FALSE' -> False;
  unset -> False (default-off); garbage -> False with logged warning.
- _build_credential('interactive') with persist=true passes cache_persistence_options
  with name='dataverse-mcp.cache' and allow_unencrypted_storage reflecting the flag;
  with persist=false passes no cache_persistence_options kwarg.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from dataverse_mcp.client import (
    _get_token_cache_allow_unencrypted,
    _get_token_cache_persist,
)

# ---------------------------------------------------------------------------
# _get_token_cache_persist
# ---------------------------------------------------------------------------


def test_token_cache_persist_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit 'true' (lowercase) returns True."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "true")
    assert _get_token_cache_persist() is True


def test_token_cache_persist_true_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    """'TRUE' (uppercase) is accepted case-insensitively."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "TRUE")
    assert _get_token_cache_persist() is True


def test_token_cache_persist_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit 'false' (lowercase) returns False."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "false")
    assert _get_token_cache_persist() is False


def test_token_cache_persist_false_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    """'FALSE' (uppercase) is accepted case-insensitively."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "FALSE")
    assert _get_token_cache_persist() is False


def test_token_cache_persist_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset variable defaults to True."""
    monkeypatch.delenv("DATAVERSE_TOKEN_CACHE_PERSIST", raising=False)
    assert _get_token_cache_persist() is True


def test_token_cache_persist_garbage_defaults_true_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Garbage value falls back to True and logs a warning."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "yes")
    with caplog.at_level(logging.WARNING, logger="dataverse_mcp.client"):
        result = _get_token_cache_persist()
    assert result is True
    assert any("DATAVERSE_TOKEN_CACHE_PERSIST" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _get_token_cache_allow_unencrypted
# ---------------------------------------------------------------------------


def test_token_cache_allow_unencrypted_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit 'true' returns True."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", "true")
    assert _get_token_cache_allow_unencrypted() is True


def test_token_cache_allow_unencrypted_true_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    """'TRUE' is accepted case-insensitively."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", "TRUE")
    assert _get_token_cache_allow_unencrypted() is True


def test_token_cache_allow_unencrypted_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit 'false' returns False."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", "false")
    assert _get_token_cache_allow_unencrypted() is False


def test_token_cache_allow_unencrypted_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset variable defaults to False (encrypted-by-default)."""
    monkeypatch.delenv("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", raising=False)
    assert _get_token_cache_allow_unencrypted() is False


def test_token_cache_allow_unencrypted_garbage_defaults_false_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Garbage value falls back to False and logs a warning."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", "1")
    with caplog.at_level(logging.WARNING, logger="dataverse_mcp.client"):
        result = _get_token_cache_allow_unencrypted()
    assert result is False
    assert any("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _build_credential kwarg-capture tests
# ---------------------------------------------------------------------------


def test_build_credential_interactive_with_persist_passes_cache_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With persist=true, InteractiveBrowserCredential receives cache_persistence_options
    with name='dataverse-mcp.cache' and allow_unencrypted_storage=False (default)."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "true")
    monkeypatch.delenv("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", raising=False)

    captured_kwargs: dict = {}

    def fake_credential(*args, **kwargs):
        captured_kwargs.update(kwargs)
        cred = MagicMock()
        cred.get_token = MagicMock(return_value=MagicMock(token="tok", expires_on=9999999999.0))
        return cred

    # Also patch _load_auth_record to return None (no prior sidecar)
    with (
        patch("dataverse_mcp.client.InteractiveBrowserCredential", side_effect=fake_credential),
        patch("dataverse_mcp.client._load_auth_record", return_value=None),
    ):
        from dataverse_mcp.client import _build_credential
        _build_credential("interactive")

    assert "cache_persistence_options" in captured_kwargs
    opts = captured_kwargs["cache_persistence_options"]
    # TokenCachePersistenceOptions exposes name and allow_unencrypted_storage as attributes
    assert opts.name == "dataverse-mcp.cache"
    assert opts.allow_unencrypted_storage is False


def test_build_credential_interactive_with_persist_allow_unencrypted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With persist=true and allow_unencrypted=true, options carry allow_unencrypted_storage=True."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "true")
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", "true")

    captured_kwargs: dict = {}

    def fake_credential(*args, **kwargs):
        captured_kwargs.update(kwargs)
        cred = MagicMock()
        cred.get_token = MagicMock(return_value=MagicMock(token="tok", expires_on=9999999999.0))
        return cred

    with (
        patch("dataverse_mcp.client.InteractiveBrowserCredential", side_effect=fake_credential),
        patch("dataverse_mcp.client._load_auth_record", return_value=None),
    ):
        from dataverse_mcp.client import _build_credential
        _build_credential("interactive")

    assert "cache_persistence_options" in captured_kwargs
    opts = captured_kwargs["cache_persistence_options"]
    assert opts.allow_unencrypted_storage is True


def test_build_credential_interactive_without_persist_passes_no_cache_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With persist=false, InteractiveBrowserCredential is called with no
    cache_persistence_options kwarg (old in-memory-only behaviour)."""
    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "false")

    captured_kwargs: dict = {}

    def fake_credential(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("dataverse_mcp.client.InteractiveBrowserCredential", side_effect=fake_credential):
        from dataverse_mcp.client import _build_credential
        _build_credential("interactive")

    assert "cache_persistence_options" not in captured_kwargs


# ---------------------------------------------------------------------------
# Regression test: wrapper must not recurse when authenticate() calls get_token
# ---------------------------------------------------------------------------


def test_get_token_wrapper_no_recursion_on_reentering_authenticate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-shot get_token wrapper must restore the original method BEFORE
    calling credential.authenticate(), so that authenticate()'s internal
    self.get_token() call hits the real method rather than re-entering the
    wrapper.

    This mirrors the real azure-identity behaviour where
    InteractiveCredential.authenticate() is implemented as
    self.get_token(*scopes, _allow_prompt=True, ...).

    Assertions:
    (a) No RecursionError is raised (no unbounded re-entry).
    (b) authenticate() is invoked exactly once.
    (c) _save_auth_record is called exactly once.
    (d) The instance get_token is restored to the original method after the
        wrapper fires.
    """
    import sys
    from unittest.mock import MagicMock, call, patch

    monkeypatch.setenv("DATAVERSE_TOKEN_CACHE_PERSIST", "true")
    monkeypatch.delenv("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", raising=False)

    authenticate_call_count = 0
    fake_token = MagicMock(token="tok", expires_on=9999999999.0)
    fake_record = MagicMock()

    class ReentrantFakeCredential:
        """Mimics azure-identity: authenticate() calls self.get_token internally."""

        def get_token(self, *args, **kwargs):
            return fake_token

        def authenticate(self, *, scopes):
            nonlocal authenticate_call_count
            authenticate_call_count += 1
            # Mirror azure-identity: authenticate calls self.get_token.
            # With the buggy (pre-fix) code this re-enters the wrapper → recursion.
            self.get_token(*scopes)
            return fake_record

    fake_cred_instance = ReentrantFakeCredential()
    original_get_token = fake_cred_instance.get_token

    def fake_credential_factory(*args, **kwargs):
        return fake_cred_instance

    save_record_calls: list = []

    def fake_save_auth_record(record, path):
        save_record_calls.append((record, path))

    with (
        patch("dataverse_mcp.client.InteractiveBrowserCredential", side_effect=fake_credential_factory),
        patch("dataverse_mcp.client._load_auth_record", return_value=None),
        patch("dataverse_mcp.client._save_auth_record", side_effect=fake_save_auth_record),
    ):
        from dataverse_mcp.client import _build_credential
        cred = _build_credential("interactive")

        # Trigger the wrapper while the patch on _save_auth_record is still live.
        # (The wrapper closes over the module global name, so unpatching before
        # calling would let the real _save_auth_record run instead of our spy.)
        scope = "https://orgname.crm.dynamics.com/.default"
        # Should not raise RecursionError (assertion a).
        result = cred.get_token(scope)

    assert result is fake_token, "get_token must return the original token"

    # (b) authenticate() invoked exactly once.
    assert authenticate_call_count == 1, (
        f"authenticate() should be called exactly once, got {authenticate_call_count}"
    )

    # (c) _save_auth_record called exactly once.
    assert len(save_record_calls) == 1, (
        f"_save_auth_record should be called exactly once, got {len(save_record_calls)}"
    )
    assert save_record_calls[0][0] is fake_record

    # (d) The wrapper has unwrapped itself: the instance-level 'get_token' override
    # must no longer be the wrapper function.  The wrapper stored the original bound
    # method back onto the instance dict, so we check that the stored function is
    # NOT the wrapper closure (it must be ReentrantFakeCredential.get_token).
    instance_get_token_override = cred.__dict__.get("get_token", None)
    if instance_get_token_override is not None:
        # An instance override is present — confirm it is NOT the wrapper by
        # verifying it is the underlying class method (same __func__).
        assert getattr(instance_get_token_override, "__func__", instance_get_token_override) is (
            ReentrantFakeCredential.get_token
        ), "instance-level get_token override after unwrap must be the original class method"
    # Either the instance dict has no 'get_token' key (full removal) or it points
    # back to the class method — both mean the wrapper is gone.  Confirm a second
    # call goes to the original method and does NOT increment authenticate_call_count.
    _ = cred.get_token(scope)
    assert authenticate_call_count == 1, (
        "authenticate() must NOT be called again on a second get_token call after unwrap"
    )
