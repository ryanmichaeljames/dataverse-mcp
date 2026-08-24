"""Tests for the branded interactive sign-in redirect page.

Covers:
- render_auth_redirect_page: success vs. error branch, HTML escaping, the
  authorization code never being echoed, error truncation.
- BrandedAuthCodeRedirectServer: the duck-typed contract azure-identity relies
  on — constructed as (hostname, port, timeout), wait_for_redirect() returns the
  parsed query params, and the response body is the branded page.  Exercised
  over a loopback socket on an ephemeral port; no external network.
- _new_interactive_credential: passes _server_class through, and degrades to a
  plain credential if azure-identity ever rejects that keyword.
"""

import threading
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from dataverse_mcp.auth_redirect import (
    BrandedAuthCodeRedirectServer,
    render_auth_redirect_page,
)

# ---------------------------------------------------------------------------
# render_auth_redirect_page
# ---------------------------------------------------------------------------


def test_success_page_is_the_success_branch():
    html = render_auth_redirect_page({"code": "0.AR8Aabc", "session_state": "xyz"})

    assert "SIGNED IN" in html
    assert "SIGN-IN FAILED" not in html
    assert "<!doctype html>" in html
    # Banner palette reached the page.
    assert "#e11d2e" in html
    assert "#09090b" in html


def test_success_page_never_echoes_the_authorization_code():
    """The code is a live credential — it is captured, never rendered."""
    html = render_auth_redirect_page(
        {"code": "0.AR8A-secret-auth-code", "session_state": "s"}
    )

    assert "0.AR8A-secret-auth-code" not in html
    assert "session_state" not in html


def test_error_page_shows_error_and_description():
    html = render_auth_redirect_page(
        {
            "error": "access_denied",
            "error_description": "AADSTS65004: User declined the consent prompt.",
        }
    )

    assert "SIGN-IN FAILED" in html
    assert "SIGNED IN" not in html
    assert "access_denied" in html
    assert "AADSTS65004: User declined the consent prompt." in html


def test_error_page_escapes_html():
    html = render_auth_redirect_page(
        {"error": "<script>alert(1)</script>", "error_description": 'x" onload="y'}
    )

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'x" onload="y' not in html
    assert "&quot;" in html


def test_error_page_truncates_a_very_long_description():
    html = render_auth_redirect_page(
        {"error": "server_error", "error_description": "A" * 5000}
    )

    assert "A" * 5000 not in html
    assert "…" in html


def test_error_page_joins_repeated_parameters():
    """parse_qs hands back a list when a parameter repeats."""
    html = render_auth_redirect_page({"error": ["access_denied", "invalid_grant"]})

    assert "access_denied, invalid_grant" in html
    assert "['access_denied'" not in html


def test_blank_error_takes_the_success_branch():
    """keep_blank_values=True yields error='' for a trailing '&error='."""
    html = render_auth_redirect_page({"code": "abc", "error": ""})

    assert "SIGNED IN" in html


# ---------------------------------------------------------------------------
# BrandedAuthCodeRedirectServer — the azure-identity duck-typed contract
# ---------------------------------------------------------------------------


@pytest.fixture
def redirect_server():
    """A branded redirect server bound to an ephemeral loopback port."""
    server = BrandedAuthCodeRedirectServer("localhost", 0, timeout=5)
    try:
        yield server
    finally:
        server.server_close()


def _drive(server):
    """Run wait_for_redirect() on a worker thread, as the credential does."""
    result = {}

    def _run():
        result["params"] = server.wait_for_redirect()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, result


def test_server_captures_query_params_and_serves_the_page(redirect_server):
    port = redirect_server.server_address[1]
    thread, result = _drive(redirect_server)

    with urllib.request.urlopen(
        f"http://localhost:{port}/?code=the-code&session_state=abc", timeout=5
    ) as response:
        body = response.read().decode("utf-8")
        content_type = response.headers["Content-Type"]

    thread.join(timeout=5)

    # The credential gets the params it needs to redeem the code…
    assert result["params"] == {"code": "the-code", "session_state": "abc"}
    # …and the user gets the branded page, not the stock one-liner.
    assert content_type == "text/html; charset=utf-8"
    assert "SIGNED IN" in body
    assert "Authentication complete. You can close this window." not in body
    assert "the-code" not in body


def test_server_serves_the_error_page_on_a_failed_sign_in(redirect_server):
    port = redirect_server.server_address[1]
    thread, result = _drive(redirect_server)

    with urllib.request.urlopen(
        f"http://localhost:{port}/?error=access_denied&error_description=nope",
        timeout=5,
    ) as response:
        body = response.read().decode("utf-8")

    thread.join(timeout=5)

    assert result["params"]["error"] == "access_denied"
    assert "SIGN-IN FAILED" in body
    assert "access_denied" in body


def test_favicon_request_does_not_end_the_wait(redirect_server):
    """A browser favicon probe must not be mistaken for the redirect."""
    port = redirect_server.server_address[1]
    thread, result = _drive(redirect_server)

    with urllib.request.urlopen(
        f"http://localhost:{port}/favicon.ico", timeout=5
    ) as response:
        assert response.status == 204

    assert not thread.join(timeout=0.2) and thread.is_alive(), (
        "wait_for_redirect() returned on a favicon request"
    )

    with urllib.request.urlopen(f"http://localhost:{port}/?code=c", timeout=5):
        pass
    thread.join(timeout=5)

    assert result["params"] == {"code": "c"}


# ---------------------------------------------------------------------------
# _new_interactive_credential
# ---------------------------------------------------------------------------


def test_new_interactive_credential_passes_server_class():
    from dataverse_mcp import client

    with patch.object(client, "InteractiveBrowserCredential") as MockCred:
        client._new_interactive_credential(tenant_id="t")

    _, kwargs = MockCred.call_args
    assert kwargs["_server_class"] is BrandedAuthCodeRedirectServer
    assert kwargs["tenant_id"] == "t"


def test_new_interactive_credential_falls_back_when_keyword_rejected():
    """A future azure-identity without _server_class must not break sign-in."""
    from dataverse_mcp import client

    plain = MagicMock()

    def _cred(**kwargs):
        if "_server_class" in kwargs:
            raise TypeError("unexpected keyword argument '_server_class'")
        return plain

    with patch.object(client, "InteractiveBrowserCredential", side_effect=_cred):
        result = client._new_interactive_credential(tenant_id="t")

    assert result is plain
