"""Unit tests for allowlist host canonicalization in client.py (issue #56).

Acceptance criteria:
1. Non-standard port (e.g. :8443) is rejected by _normalize_org_url /
   normalize_dataverse_url.
2. Explicit port 443 is accepted and the returned URL is port-free (no :8443
   bypass possible).
3. Trailing-dot host matches its allowlist entry without the dot.
4. Punycode and Unicode forms of the same IDN host canonicalize to equal
   values and both match a single allowlist entry.
5. A plain approved host passes allowlist enforcement.
6. A non-whitelisted host is rejected.
7. Empty whitelist permits all hosts.
8. Malformed IDNA input raises ValueError (not UnicodeError).
"""

import importlib
import sys

import pytest

import dataverse_mcp.client as client_mod
from dataverse_mcp.client import (
    _canonicalize_host,
    _normalize_org_url,
    normalize_dataverse_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_with_whitelist(monkeypatch, value: str):
    """Reload client module with DATAVERSE_WHITELIST set to *value*.

    normalize_dataverse_url is lru_cached and _URL_WHITELIST is read once at
    import time, so we must reload the module and re-import the functions.
    Returns the reloaded module.
    """
    monkeypatch.setenv("DATAVERSE_WHITELIST", value)
    reloaded = importlib.reload(client_mod)
    return reloaded


@pytest.fixture(autouse=True)
def _restore_whitelist_globals():
    """Save/restore module-level whitelist globals so mutations don't leak across tests."""
    saved_hosts = client_mod._URL_WHITELIST
    saved_require = client_mod._REQUIRE_WHITELIST
    try:
        yield
    finally:
        client_mod._URL_WHITELIST = saved_hosts
        client_mod._REQUIRE_WHITELIST = saved_require
        client_mod.normalize_dataverse_url.cache_clear()


def _patch_whitelist(hosts: frozenset[str], require: bool = False):
    """Directly patch _URL_WHITELIST / _REQUIRE_WHITELIST and clear the lru_cache."""
    client_mod._URL_WHITELIST = hosts
    client_mod._REQUIRE_WHITELIST = require
    client_mod.normalize_dataverse_url.cache_clear()


# ---------------------------------------------------------------------------
# 1. Non-standard port is hard-rejected
# ---------------------------------------------------------------------------

def test_non_standard_port_rejected():
    """_normalize_org_url raises ValueError for any port other than 443."""
    with pytest.raises(ValueError, match=r"non-standard port"):
        _normalize_org_url("https://approved.crm.dynamics.com:8443")


def test_non_standard_port_rejected_via_normalize(monkeypatch):
    """normalize_dataverse_url also rejects non-standard ports (whitelist active)."""
    _patch_whitelist(frozenset({"approved.crm.dynamics.com"}))
    with pytest.raises(ValueError, match=r"non-standard port"):
        normalize_dataverse_url("https://approved.crm.dynamics.com:8443")


# ---------------------------------------------------------------------------
# 2. Explicit port 443 is accepted; returned URL has no port component
# ---------------------------------------------------------------------------

def test_explicit_port_443_accepted():
    """Port 443 is the HTTPS default and must not cause rejection."""
    result = _normalize_org_url("https://approved.crm.dynamics.com:443")
    # No port in the returned URL
    assert ":443" not in result
    assert ":8443" not in result
    assert result == "https://approved.crm.dynamics.com"


def test_explicit_port_443_accepted_via_normalize(monkeypatch):
    """normalize_dataverse_url accepts :443 and returns a port-free canonical URL."""
    _patch_whitelist(frozenset({"approved.crm.dynamics.com"}))
    result = normalize_dataverse_url("https://approved.crm.dynamics.com:443")
    assert ":443" not in result
    assert result == "https://approved.crm.dynamics.com"


# ---------------------------------------------------------------------------
# 3. Trailing-dot host matches allowlist entry without the dot
# ---------------------------------------------------------------------------

def test_trailing_dot_matches_allowlist(monkeypatch):
    """A URL with a trailing-dot host (absolute DNS form) matches the plain allowlist entry."""
    _patch_whitelist(frozenset({"org.crm.dynamics.com"}))
    # Should NOT raise — trailing dot must be stripped before comparison
    result = normalize_dataverse_url("https://org.crm.dynamics.com.")
    assert result == "https://org.crm.dynamics.com"


def test_trailing_dot_canonicalize_host():
    """_canonicalize_host strips exactly one trailing dot."""
    assert _canonicalize_host("org.crm.dynamics.com.") == "org.crm.dynamics.com"


# ---------------------------------------------------------------------------
# 4. Punycode and Unicode forms canonicalize to the same value
# ---------------------------------------------------------------------------
# café.example.com (latin small letter e with acute) encodes to
# xn--caf-dma.example.com via the IDNA codec.  Both forms must produce equal
# canonical output and both must match a single allowlist entry.

# Use byte literals to avoid any source-file encoding dependency.
_UNICODE_IDN_HOST: str = b"caf\xe9.example.com".decode("latin-1")  # café.example.com
_PUNYCODE_IDN_HOST = "xn--caf-dma.example.com"


def test_idn_unicode_and_punycode_canonicalize_equal():
    """Unicode and punycode forms of the same hostname produce equal canonical strings."""
    canonical_unicode = _canonicalize_host(_UNICODE_IDN_HOST)
    canonical_punycode = _canonicalize_host(_PUNYCODE_IDN_HOST)
    assert canonical_unicode == canonical_punycode, (
        f"Expected equal canonical forms; got {canonical_unicode!r} vs {canonical_punycode!r}"
    )


def test_idn_unicode_form_matches_allowlist(monkeypatch):
    """Unicode IDN host passes when allowlist contains the punycode entry."""
    canonical = _canonicalize_host(_PUNYCODE_IDN_HOST)
    _patch_whitelist(frozenset({canonical}))
    result = normalize_dataverse_url(f"https://{_UNICODE_IDN_HOST}")
    assert result == f"https://{canonical}"


def test_idn_punycode_form_matches_allowlist(monkeypatch):
    """Punycode IDN host passes when allowlist contains the punycode entry."""
    canonical = _canonicalize_host(_PUNYCODE_IDN_HOST)
    _patch_whitelist(frozenset({canonical}))
    result = normalize_dataverse_url(f"https://{_PUNYCODE_IDN_HOST}")
    assert result == f"https://{canonical}"


# ---------------------------------------------------------------------------
# 5. Plain approved host passes
# ---------------------------------------------------------------------------

def test_approved_host_passes(monkeypatch):
    """A host present in the allowlist passes without error."""
    _patch_whitelist(frozenset({"approved.crm.dynamics.com"}))
    result = normalize_dataverse_url("https://approved.crm.dynamics.com")
    assert result == "https://approved.crm.dynamics.com"


# ---------------------------------------------------------------------------
# 6. Non-whitelisted host is rejected
# ---------------------------------------------------------------------------

def test_non_whitelisted_host_rejected(monkeypatch):
    """A host not in the allowlist raises ValueError."""
    _patch_whitelist(frozenset({"approved.crm.dynamics.com"}))
    with pytest.raises(ValueError, match=r"DATAVERSE_WHITELIST"):
        normalize_dataverse_url("https://notapproved.crm.dynamics.com")


# ---------------------------------------------------------------------------
# 7. Empty whitelist permits all hosts
# ---------------------------------------------------------------------------

def test_empty_whitelist_permits_all(monkeypatch):
    """When no whitelist is configured, any valid host is permitted."""
    _patch_whitelist(frozenset())
    # Both an 'approved' and a totally arbitrary host should pass
    assert normalize_dataverse_url("https://anyorg.crm.dynamics.com").startswith("https://")
    assert normalize_dataverse_url("https://another.example.com").startswith("https://")


# ---------------------------------------------------------------------------
# 7b. DATAVERSE_REQUIRE_WHITELIST fails closed when whitelist is empty (issue #122)
# ---------------------------------------------------------------------------

def test_require_whitelist_empty_fails_closed(monkeypatch):
    """Empty whitelist + require flag rejects every host (no token minted)."""
    _patch_whitelist(frozenset(), require=True)
    with pytest.raises(ValueError, match=r"DATAVERSE_REQUIRE_WHITELIST"):
        normalize_dataverse_url("https://anyorg.crm.dynamics.com")


def test_require_whitelist_with_populated_list_allows_approved(monkeypatch):
    """Require flag has no effect once the whitelist is populated: approved host passes."""
    _patch_whitelist(frozenset({"approved.crm.dynamics.com"}), require=True)
    result = normalize_dataverse_url("https://approved.crm.dynamics.com")
    assert result == "https://approved.crm.dynamics.com"


def test_require_whitelist_with_populated_list_rejects_unapproved(monkeypatch):
    """Require flag + populated whitelist still rejects an unapproved host."""
    _patch_whitelist(frozenset({"approved.crm.dynamics.com"}), require=True)
    with pytest.raises(ValueError, match=r"DATAVERSE_WHITELIST"):
        normalize_dataverse_url("https://notapproved.crm.dynamics.com")


def test_require_flag_reader_defaults_false(monkeypatch):
    """_get_require_whitelist defaults to False and parses 'true'/'false' case-insensitively."""
    monkeypatch.delenv("DATAVERSE_REQUIRE_WHITELIST", raising=False)
    assert client_mod._get_require_whitelist() is False
    monkeypatch.setenv("DATAVERSE_REQUIRE_WHITELIST", "TRUE")
    assert client_mod._get_require_whitelist() is True
    monkeypatch.setenv("DATAVERSE_REQUIRE_WHITELIST", "False")
    assert client_mod._get_require_whitelist() is False
    monkeypatch.setenv("DATAVERSE_REQUIRE_WHITELIST", "garbage")
    assert client_mod._get_require_whitelist() is False


# ---------------------------------------------------------------------------
# 8. Malformed IDNA input raises ValueError (not UnicodeError)
# ---------------------------------------------------------------------------

def test_malformed_idna_raises_value_error():
    """_canonicalize_host raises ValueError (not UnicodeError) for invalid hostnames."""
    # A label that is 64 characters long exceeds the IDNA 63-character limit.
    bad_host = "a" * 64 + ".example.com"
    with pytest.raises(ValueError, match=r"cannot IDNA-encode"):
        _canonicalize_host(bad_host)


def test_malformed_idna_not_unicode_error():
    """Ensure UnicodeError is not propagated directly — only ValueError must escape."""
    bad_host = "a" * 64 + ".example.com"
    try:
        _canonicalize_host(bad_host)
    except UnicodeError:
        pytest.fail("_canonicalize_host must not propagate UnicodeError directly")
    except ValueError:
        pass  # expected: UnicodeError was caught and re-raised as ValueError
