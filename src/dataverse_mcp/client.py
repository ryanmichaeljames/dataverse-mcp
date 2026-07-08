"""Shared Dataverse auth helpers and lifespan management."""

import asyncio
import functools
import json
import logging
import os
import pathlib
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import (
    AuthenticationRecord,
    AzureCliCredential,
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
)

logger = logging.getLogger(__name__)

SUPPORTED_AUTH_TYPES = ("interactive", "azure_cli")
_DATAVERSE_API_VERSION = "v9.2"
_TOKEN_REFRESH_BUFFER_SECONDS = 300
_MAX_ERROR_MESSAGE_LENGTH = 2000

# Dataverse service-protection limits return 429; 502/503/504 are transient
# gateway failures. Anything else is returned to the caller untouched.
_RETRYABLE_STATUS_CODES = (429, 502, 503, 504)
# Only safe-to-repeat methods are retried on 5xx. POST/PATCH are excluded
# because a 5xx may arrive after the server already committed the write.
_IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "DELETE"})
_MAX_RETRY_AFTER_SECONDS = 30.0
_DEFAULT_RETRY_AFTER_SECONDS = 2.0
# Tool responses above the warn threshold are logged; above the cap they are
# replaced with an actionable error so a single call can't flood the client.
_RESPONSE_WARN_BYTES = 1_000_000
_RESPONSE_MAX_BYTES = 5_000_000
# Dataverse pages are capped server-side at 5,000; we never ask for more than
# 500 per page to keep individual responses small.
_MAX_PAGE_SIZE = 500
# Default timeout (seconds) for blocking credential acquisition in get_bearer_token.
# Overridden via DATAVERSE_AUTH_TIMEOUT_SECONDS. Must be positive; non-positive or
# non-numeric values fall back to this default with a warning.
_DEFAULT_AUTH_TIMEOUT_SECONDS = 30.0

# Optional allowlist of Dataverse environment hostnames. Tokens are minted for
# whatever host a tool call supplies, so an explicit whitelist confines requests
# (and the bearer tokens they carry) to environments the operator has approved.
# When empty, every environment is permitted (see the README security warning).


def _canonicalize_host(host: str) -> str:
    """Return a canonical, comparable form of *host*.

    Steps applied in order:
    1. Strip exactly one trailing dot (DNS absolute form -> relative form).
    2. Lowercase before IDNA encoding (the ``idna`` codec is case-sensitive on
       already-encoded labels; lowercasing first ensures deterministic results).
    3. IDNA-encode so that Unicode / punycode forms of the same hostname are
       equal after canonicalization.
    4. Lowercase the ASCII result defensively (punycode labels are already
       lower, but belt-and-suspenders).

    Raises:
        ValueError: When the host cannot be IDNA-encoded (e.g., labels that are
            too long, contain invalid characters, or are otherwise malformed).
            Callers must not catch ``UnicodeError`` directly; this helper always
            re-raises as ``ValueError`` with an actionable message.
    """
    # Strip a single trailing dot (absolute DNS notation -> bare hostname).
    if host.endswith("."):
        host = host[:-1]

    # Lowercase first: the idna codec is case-sensitive on already-encoded
    # punycode labels, so normalise case before encoding.
    host = host.lower()

    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(
            f"Invalid hostname {host!r}: cannot IDNA-encode — {exc}. "
            "Ensure the hostname contains only valid DNS label characters."
        ) from exc

    # Defensive final lowercase (punycode output is already lowercase, but
    # keeps the contract explicit and stable across Python versions).
    return host.lower()


def _normalize_whitelist_host(entry: str) -> str:
    """Reduce a DATAVERSE_WHITELIST entry to a canonical hostname.

    Accepts plain hosts (``org.crm.dynamics.com``); a scheme or trailing path is
    tolerated and stripped so comparisons are always host-to-host.  The result
    is passed through :func:`_canonicalize_host` so trailing dots, Unicode, and
    punycode forms all reduce to the same canonical value.
    """
    candidate = entry.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    host = urlparse(candidate).hostname
    if not host:
        raise ValueError("could not parse a hostname")
    return _canonicalize_host(host)


def _load_url_whitelist() -> frozenset[str]:
    """Parse DATAVERSE_WHITELIST into a set of allowed environment hostnames.

    Accepts a comma-separated list of hostnames (e.g.
    ``org-one.crm.dynamics.com,org-two.crm.dynamics.com``). Invalid entries are
    skipped with a warning rather than failing startup. An empty/unset value
    yields an empty set, which disables whitelist enforcement.
    """
    raw = os.environ.get("DATAVERSE_WHITELIST", "")
    whitelist: set[str] = set()
    for part in raw.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            whitelist.add(_normalize_whitelist_host(candidate))
        except ValueError as e:
            logger.warning("Ignoring invalid DATAVERSE_WHITELIST entry %r: %s", candidate, e)
    return frozenset(whitelist)


def _get_auth_timeout_seconds() -> float:
    """Return the credential-acquisition timeout from DATAVERSE_AUTH_TIMEOUT_SECONDS.

    Falls back to _DEFAULT_AUTH_TIMEOUT_SECONDS and logs a warning when the env
    var is present but non-numeric or non-positive, mirroring the defensive
    parsing pattern used for _parse_retry_after_seconds.
    """
    raw = os.environ.get("DATAVERSE_AUTH_TIMEOUT_SECONDS", "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
            logger.warning(
                "DATAVERSE_AUTH_TIMEOUT_SECONDS=%r is non-positive; "
                "using default %.1fs",
                raw,
                _DEFAULT_AUTH_TIMEOUT_SECONDS,
            )
        except ValueError:
            logger.warning(
                "DATAVERSE_AUTH_TIMEOUT_SECONDS=%r is not a valid number; "
                "using default %.1fs",
                raw,
                _DEFAULT_AUTH_TIMEOUT_SECONDS,
            )
    return _DEFAULT_AUTH_TIMEOUT_SECONDS


def _get_token_cache_persist() -> bool:
    """Return whether interactive token cache persistence is enabled.

    Reads DATAVERSE_TOKEN_CACHE_PERSIST (default: true).  Accepts 'true' or
    'false' case-insensitively; any other non-empty value is rejected with a
    logged warning and falls back to the default (true), matching the defensive
    idiom used by _get_auth_timeout_seconds.
    """
    raw = os.environ.get("DATAVERSE_TOKEN_CACHE_PERSIST", "").strip().lower()
    if not raw:
        return True
    if raw == "true":
        return True
    if raw == "false":
        return False
    logger.warning(
        "DATAVERSE_TOKEN_CACHE_PERSIST=%r is not 'true' or 'false'; "
        "using default (true)",
        os.environ.get("DATAVERSE_TOKEN_CACHE_PERSIST", ""),
    )
    return True


def _get_token_cache_allow_unencrypted() -> bool:
    """Return whether unencrypted token cache storage is permitted.

    Reads DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED (default: false).  Accepts
    'true' or 'false' case-insensitively; any other non-empty value is rejected
    with a logged warning and falls back to the default (false).  When true, a
    startup warning is emitted by the caller because tokens may be written to
    disk without OS-level encryption.
    """
    raw = os.environ.get("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", "").strip().lower()
    if not raw:
        return False
    if raw == "true":
        return True
    if raw == "false":
        return False
    logger.warning(
        "DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED=%r is not 'true' or 'false'; "
        "using default (false)",
        os.environ.get("DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED", ""),
    )
    return False


def _get_token_cache_profile() -> str:
    """Return a filename-safe profile suffix for the token cache and sidecar.

    Reads DATAVERSE_TOKEN_CACHE_PROFILE (default: empty).  When set, the value
    is appended to both the MSAL cache name and the AuthenticationRecord sidecar
    filename so that two server processes connecting to different
    tenants/accounts on the same host do not share (and overwrite) each other's
    cache and pinned account.  An empty/unset value preserves the original
    single-profile filenames for backwards compatibility.

    Only ``[A-Za-z0-9_-]`` are permitted.  Any other character raises
    ``ValueError`` rather than being silently dropped: sanitizing would let two
    distinct profiles (e.g. ``a/b`` and ``a.b``) collapse to the same value and
    secretly share one cache — the exact cross-tenant collision this option
    exists to prevent.  Failing fast at startup forces the operator to pick an
    unambiguous, filesystem-safe name.
    """
    raw = os.environ.get("DATAVERSE_TOKEN_CACHE_PROFILE", "").strip()
    if not raw:
        return ""
    allowed = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789-_"
    )
    if any(c not in allowed for c in raw):
        raise ValueError(
            f"DATAVERSE_TOKEN_CACHE_PROFILE={raw!r} contains characters outside "
            "[A-Za-z0-9_-]. Choose a profile name using only letters, digits, "
            "dashes, or underscores so it is an unambiguous, filesystem-safe "
            "cache identifier."
        )
    return raw


def _get_user_config_dir() -> pathlib.Path:
    """Return a per-user config directory for dataverse-mcp.

    Uses LOCALAPPDATA on Windows, XDG_CONFIG_HOME (or ~/.config) elsewhere,
    mirroring the convention used by most CLI tools.  Does not create the
    directory — callers must mkdir(parents=True, exist_ok=True) before writing.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(pathlib.Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "dataverse-mcp"


def _load_auth_record(record_path: pathlib.Path) -> AuthenticationRecord | None:
    """Load a serialized AuthenticationRecord from *record_path*, or return None.

    Returns None on any read/parse error and logs a warning so the caller falls
    back to a fresh interactive prompt rather than crashing.
    """
    if not record_path.exists():
        return None
    try:
        data = record_path.read_text(encoding="utf-8")
        record = AuthenticationRecord.deserialize(data)
        logger.debug("Loaded AuthenticationRecord from %s", record_path)
        return record
    except Exception as exc:
        logger.warning(
            "Could not load AuthenticationRecord from %s (%s); "
            "a fresh interactive sign-in will be required",
            record_path, exc,
        )
        return None


def _save_auth_record(record: AuthenticationRecord, record_path: pathlib.Path) -> None:
    """Serialize *record* to *record_path* (mode 0o600), creating parent dirs.

    Errors are logged as warnings and swallowed — failure to persist the record
    is non-fatal; the user will just be re-prompted on the next restart.
    """
    try:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(record.serialize(), encoding="utf-8")
        # Restrict to owner read/write on POSIX; Windows ACLs are per-user by
        # default for LOCALAPPDATA so no chmod needed.
        if os.name != "nt":
            record_path.chmod(0o600)
        logger.debug("Saved AuthenticationRecord to %s", record_path)
    except Exception as exc:
        logger.warning(
            "Could not save AuthenticationRecord to %s (%s); "
            "a fresh interactive sign-in will be required on the next restart",
            record_path, exc,
        )


# Common Azure CLI installation paths that may not be on the system PATH when
# the MCP server process is launched (e.g., from VS Code without a login shell).
_AZ_CLI_CANDIDATE_PATHS = [
    r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin",
    r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin",
]


def _ensure_az_cli_on_path() -> None:
    """Add known Azure CLI install directories to PATH if az is not already found."""
    if shutil.which("az"):
        return

    if os.name != "nt":
        logger.warning(
            "Azure CLI not found. "
            "Ensure Azure CLI is installed and available on PATH."
        )
        return

    current_path = os.environ.get("PATH", "")
    existing_dirs = {
        os.path.normcase(os.path.normpath(p))
        for p in current_path.split(os.pathsep)
        if p
    }
    additions = [
        p
        for p in _AZ_CLI_CANDIDATE_PATHS
        if os.path.isdir(p) and os.path.normcase(os.path.normpath(p)) not in existing_dirs
    ]
    if additions:
        os.environ["PATH"] = os.pathsep.join(additions) + os.pathsep + current_path
        logger.info("Added Azure CLI path(s) to PATH: %s", additions)
    else:
        logger.warning(
            "Azure CLI not found on PATH and no known Windows install directories exist. "
            "Ensure Azure CLI is installed and on PATH."
        )


def _build_credential(auth_type: str):
    """Build an Azure TokenCredential based on the configured auth type.

    Args:
        auth_type: One of 'interactive' or 'azure_cli'.

    Returns:
        A TokenCredential instance for authenticating with Dataverse.

    Raises:
        ValueError: If the auth type is not supported.
    """
    if auth_type == "interactive":
        logger.info("Using InteractiveBrowserCredential for authentication")
        persist = _get_token_cache_persist()
        if not persist:
            logger.info(
                "DATAVERSE_TOKEN_CACHE_PERSIST=false: "
                "interactive credential uses in-memory token cache only"
            )
            return InteractiveBrowserCredential()

        allow_unencrypted = _get_token_cache_allow_unencrypted()
        if allow_unencrypted:
            logger.warning(
                "DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED=true: "
                "the MSAL token cache may be written to disk without OS-level encryption. "
                "Refresh tokens are long-lived credentials; only enable this on trusted, "
                "access-controlled hosts where an OS secret store is unavailable."
            )

        # Optional profile suffix isolates the cache + sidecar per
        # tenant/account so concurrent sessions on one host do not collide.
        profile = _get_token_cache_profile()
        cache_name = "dataverse-mcp.cache" if not profile else f"dataverse-mcp.{profile}.cache"
        record_filename = (
            "dataverse-mcp.authrecord.json"
            if not profile
            else f"dataverse-mcp.{profile}.authrecord.json"
        )
        if profile:
            logger.info("Token cache profile active: %r (cache name=%s)", profile, cache_name)

        cache_opts = TokenCachePersistenceOptions(
            name=cache_name,
            allow_unencrypted_storage=allow_unencrypted,
        )
        logger.info(
            "Interactive token cache persistence enabled (encrypted=%s)",
            not allow_unencrypted,
        )

        # Load a previously serialized AuthenticationRecord (secret-free JSON).
        # This anchors silent account selection on restart so MSAL can silently
        # mint a new access token from the persisted refresh token without
        # re-prompting.  Corruption or absence degrades to a fresh prompt.
        config_dir = _get_user_config_dir()
        record_path = config_dir / record_filename
        auth_record = _load_auth_record(record_path)

        credential = InteractiveBrowserCredential(
            cache_persistence_options=cache_opts,
            authentication_record=auth_record,
        )

        if auth_record is None:
            # No prior record: after the first interactive sign-in, serialize
            # the returned record so subsequent restarts can reuse it silently.
            # We do this by replacing get_token with a one-shot wrapper that
            # calls authenticate() on the underlying credential after the first
            # successful token acquisition, then removes itself.
            #
            # Note: when a sidecar record IS loaded (auth_record is not None),
            # no wrapper is installed.  A stale record for a *different* user
            # signing in interactively would therefore never be refreshed — this
            # edge case is accepted; normal use is single-user per host.
            _original_get_token = credential.get_token

            def _get_token_and_record(*args, **kwargs):
                # NOTE: this wrapper only handles *args (positional scopes);
                # **kwargs are not forwarded to authenticate().  This is safe
                # because the sole caller (get_bearer_token) passes exactly one
                # positional scope string and no kwargs.
                token = _original_get_token(*args, **kwargs)
                # Restore BEFORE calling authenticate() so that authenticate()'s
                # internal self.get_token() call hits the real method and does
                # not re-enter this wrapper (which would cause unbounded
                # recursion swallowed by the broad except below).
                credential.get_token = _original_get_token  # type: ignore[method-assign]
                try:
                    # authenticate() returns the AuthenticationRecord; scopes
                    # come from the first get_token call args.
                    record = credential.authenticate(scopes=list(args))
                    _save_auth_record(record, record_path)
                except Exception as exc:
                    logger.warning(
                        "Could not obtain AuthenticationRecord after sign-in (%s); "
                        "restart re-prompts will still occur",
                        exc,
                    )
                return token

            credential.get_token = _get_token_and_record  # type: ignore[method-assign]

        return credential

    if auth_type == "azure_cli":
        _ensure_az_cli_on_path()
        logger.info("Using AzureCliCredential for authentication")
        return AzureCliCredential()

    raise ValueError(
        f"Unsupported DATAVERSE_AUTH_TYPE: '{auth_type}'. "
        f"Supported values: {', '.join(SUPPORTED_AUTH_TYPES)}"
    )


class DataverseConnectionError(Exception):
    """Raised when a Dataverse host cannot be reached after retries are exhausted."""


@dataclass
class AppContext:
    """Application context holding shared auth state and HTTP client."""

    credential: Any
    auth_type: str
    http_client: httpx.AsyncClient
    _token_cache: dict[str, tuple[str, float]] = field(default_factory=dict)
    _token_locks: dict[str, asyncio.Lock] = field(default_factory=dict)


def _normalize_org_url(url: str) -> str:
    """Validate and normalize a Dataverse organization URL (no whitelist check).

    Only port 443 (or no explicit port) is accepted; any other port causes a
    hard ``ValueError`` so that non-standard ports cannot be used to bypass
    allowlist matching.  The hostname is canonicalized via
    :func:`_canonicalize_host` (trailing-dot stripping, IDNA encoding,
    consistent lowercasing).  The returned URL never contains an explicit port
    component.
    """
    normalized_input = url.strip()
    if not normalized_input:
        raise ValueError("dataverse_url must not be empty")

    parsed = urlparse(normalized_input)
    if parsed.scheme.lower() != "https":
        raise ValueError(
            "dataverse_url must use https (e.g., 'https://yourorg.crm.dynamics.com')"
        )
    if parsed.username or parsed.password:
        raise ValueError("dataverse_url must not include credentials")
    if not parsed.hostname:
        raise ValueError(
            "dataverse_url must include a hostname "
            "(e.g., 'https://yourorg.crm.dynamics.com')"
        )
    if parsed.port is not None and parsed.port != 443:
        raise ValueError(
            f"dataverse_url must not specify a non-standard port (got :{parsed.port}). "
            "Only the default HTTPS port (443) is permitted."
        )
    if parsed.path not in ("", "/"):
        raise ValueError("dataverse_url must not include a path")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(
            "dataverse_url must not include params, query strings, or fragments"
        )

    canonical_host = _canonicalize_host(parsed.hostname)
    return f"https://{canonical_host}"


def _get_require_whitelist() -> bool:
    """Return whether an empty DATAVERSE_WHITELIST should fail closed.

    Reads DATAVERSE_REQUIRE_WHITELIST (default: false).  Accepts 'true' or
    'false' case-insensitively; any other non-empty value is rejected with a
    logged warning and falls back to the default (false).  When true, tool
    calls are rejected whenever DATAVERSE_WHITELIST is empty, so a bearer token
    is never minted for an unapproved host — the recommended posture for
    shared/multi-tenant deployments.
    """
    raw = os.environ.get("DATAVERSE_REQUIRE_WHITELIST", "").strip().lower()
    if not raw:
        return False
    if raw == "true":
        return True
    if raw == "false":
        return False
    logger.warning(
        "DATAVERSE_REQUIRE_WHITELIST=%r is not 'true' or 'false'; using default (false)",
        os.environ.get("DATAVERSE_REQUIRE_WHITELIST", ""),
    )
    return False


# Read once at import: normalize_dataverse_url is lru_cached, so the whitelist
# must not vary per call.
_URL_WHITELIST = _load_url_whitelist()
_REQUIRE_WHITELIST = _get_require_whitelist()


@functools.lru_cache(maxsize=64)
def normalize_dataverse_url(url: str) -> str:
    """Validate, normalize, and authorize a Dataverse organization URL.

    When DATAVERSE_WHITELIST is configured, URLs whose host is outside the
    whitelist are rejected. When it is empty, any environment is permitted
    unless DATAVERSE_REQUIRE_WHITELIST is set, which fails closed (rejects
    every call) so a token is never minted for an unapproved host.

    The host extracted from the already-canonical normalized URL is compared
    against the canonical allowlist set.  Both sides went through
    :func:`_canonicalize_host`, so trailing-dot and IDN forms match correctly.
    """
    normalized = _normalize_org_url(url)
    if not _URL_WHITELIST:
        if _REQUIRE_WHITELIST:
            raise ValueError(
                "DATAVERSE_REQUIRE_WHITELIST is set but DATAVERSE_WHITELIST is empty; "
                "refusing to mint a token for an unapproved host. Populate "
                "DATAVERSE_WHITELIST with the allowed environment hostname(s)."
            )
        return normalized
    # _normalize_org_url already canonicalizes the host and drops the port,
    # so urlparse(normalized).hostname is already canonical.  Pass it
    # through _canonicalize_host once more to keep the comparison provably
    # consistent without relying on implementation ordering.
    host = _canonicalize_host(urlparse(normalized).hostname or "")
    if host not in _URL_WHITELIST:
            raise ValueError(
                f"dataverse_url host '{host}' is not in the configured DATAVERSE_WHITELIST. "
                "Add it to DATAVERSE_WHITELIST to permit access to this environment."
            )
    return normalized


def resolve_base_url(dataverse_url: str) -> str:
    """Normalize and return the per-call Dataverse base URL.

    Pydantic validates ``dataverse_url`` on every input model before this
    function is reached, so a falsy value here indicates a programming error
    rather than a normal user omission.
    """
    if not dataverse_url:
        raise ValueError(
            "dataverse_url is required; supply it on the tool input."
        )
    return normalize_dataverse_url(dataverse_url)


def get_bearer_token(app_ctx: AppContext, scope: str) -> str:
    """Acquire a bearer token with per-scope caching to avoid redundant credential round-trips."""
    cached = app_ctx._token_cache.get(scope)
    if cached:
        token_str, expires_on = cached
        if time.time() < expires_on - _TOKEN_REFRESH_BUFFER_SECONDS:
            return token_str
    access_token = app_ctx.credential.get_token(scope)
    app_ctx._token_cache[scope] = (access_token.token, float(access_token.expires_on))
    return access_token.token


def _get_cached_bearer_token(app_ctx: AppContext, scope: str) -> str | None:
    """Return a cached bearer token when present and not near expiry."""
    cached = app_ctx._token_cache.get(scope)
    if not cached:
        return None
    token_str, expires_on = cached
    if time.time() < expires_on - _TOKEN_REFRESH_BUFFER_SECONDS:
        return token_str
    return None


async def build_headers(
    app_ctx: AppContext,
    base_url: str,
    *,
    include_content_type: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build standard Dataverse Web API headers with a cached Bearer token.

    Token acquisition runs in a thread on cache miss to avoid blocking the event loop.
    A per-scope lock ensures concurrent cold-cache calls trigger a single acquisition.
    """
    scope = f"{base_url}/.default"
    token = _get_cached_bearer_token(app_ctx, scope)
    if token is None:
        # Lazy lock creation is race-free on a single-threaded event loop.
        lock = app_ctx._token_locks.setdefault(scope, asyncio.Lock())
        async with lock:
            # Re-check after acquiring lock: a concurrent caller may have already
            # populated the cache while we were waiting.
            token = _get_cached_bearer_token(app_ctx, scope)
            if token is None:
                auth_timeout = _get_auth_timeout_seconds()
                try:
                    # NOTE: asyncio.to_thread cannot be cancelled — the underlying
                    # worker thread (running get_bearer_token / credential.get_token)
                    # may outlive this timeout. The timeout's purpose is to stop
                    # serializing OTHER callers waiting on this per-scope lock, not
                    # to kill the worker thread itself. The lock is released on any
                    # exception (including asyncio.TimeoutError) because we are
                    # inside `async with lock`, so subsequent callers are unblocked.
                    token = await asyncio.wait_for(
                        asyncio.to_thread(get_bearer_token, app_ctx, scope),
                        timeout=auth_timeout,
                    )
                except asyncio.TimeoutError as exc:
                    raise ClientAuthenticationError(
                        message=(
                            f"Credential acquisition timed out after {auth_timeout:.0f}s. "
                            "Check your Azure CLI session (`az login`) or the "
                            "DATAVERSE_AUTH_TYPE / credential configuration."
                        )
                    ) from exc
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }
    if include_content_type:
        headers["Content-Type"] = "application/json"
    if extra:
        headers.update(extra)
    return headers


def _parse_retry_after_seconds(response: httpx.Response) -> float:
    """Parse a Retry-After header as seconds, falling back to a safe default."""
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            logger.debug("Unparseable Retry-After header: %r", raw)
    return _DEFAULT_RETRY_AFTER_SECONDS


async def request_with_retry(
    http_client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_attempts: int = 3,
    **kwargs: Any,
) -> httpx.Response:
    """Issue a Dataverse request, retrying throttled and transient failures.

    - 429 sleeps for Retry-After (capped at 30s; 2s when absent) and retries
      for ALL HTTP methods (the request was rejected before processing)
    - 502/503/504 retry with exponential backoff (1s, 2s, 4s) ONLY for
      idempotent methods (GET, PUT, DELETE); POST, PATCH, and other
      non-idempotent methods return the 5xx immediately because the server may
      have already committed the write before the gateway error was returned
    - Timeouts and connection failures retry for GET requests only; timeouts
      re-raise unchanged (callers have specific handlers), connection failures
      raise DataverseConnectionError
    - Any other status returns immediately; callers keep their
      raise_for_status() flow
    """
    method_upper = method.upper()
    response: httpx.Response | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await http_client.request(
                method_upper, url, headers=headers, **kwargs
            )
        except httpx.ConnectError as e:
            if method_upper != "GET" or attempt == max_attempts:
                host = urlparse(url).netloc or url
                raise DataverseConnectionError(
                    f"Could not reach {host}: {e}"
                ) from e
            delay = float(2 ** (attempt - 1))
            logger.warning(
                "Connection error on %s %s: %s; retrying in %.1fs (attempt %d/%d)",
                method_upper, url, e, delay, attempt, max_attempts,
            )
            await asyncio.sleep(delay)
            continue
        except httpx.TimeoutException as e:
            if method_upper != "GET" or attempt == max_attempts:
                raise
            delay = float(2 ** (attempt - 1))
            logger.warning(
                "Timeout on %s %s: %s; retrying in %.1fs (attempt %d/%d)",
                method_upper, url, e, delay, attempt, max_attempts,
            )
            await asyncio.sleep(delay)
            continue
        if response.status_code not in _RETRYABLE_STATUS_CODES or attempt == max_attempts:
            return response
        # 5xx gateway errors are only safe to retry for idempotent methods.
        # POST/PATCH may have already been committed by the server before the
        # error was returned; returning the response lets the caller decide.
        if response.status_code != 429 and method_upper not in _IDEMPOTENT_METHODS:
            return response
        if response.status_code == 429:
            delay = min(_parse_retry_after_seconds(response), _MAX_RETRY_AFTER_SECONDS)
        else:
            delay = float(2 ** (attempt - 1))
        logger.warning(
            "Dataverse returned %d for %s %s; retrying in %.1fs (attempt %d/%d)",
            response.status_code, method_upper, url, delay, attempt, max_attempts,
        )
        await asyncio.sleep(delay)
    assert response is not None  # loop always returns, raises, or sets response
    return response


async def paginate_records(
    url: str,
    headers: dict[str, str],
    top: int | None,
    http_client: httpx.AsyncClient,
) -> list[dict]:
    """Asynchronously fetch pages from a Dataverse collection, stopping at top records.

    Follows @odata.nextLink until top records are collected or no more pages remain.
    When top is set, asks the server for right-sized pages via odata.maxpagesize.
    Uses the shared AsyncClient for connection reuse.
    """
    request_headers = headers
    if top is not None:
        page_size_pref = f"odata.maxpagesize={min(top, _MAX_PAGE_SIZE)}"
        existing_prefer = headers.get("Prefer")
        request_headers = {
            **headers,
            "Prefer": f"{existing_prefer},{page_size_pref}"
            if existing_prefer
            else page_size_pref,
        }
    records: list[dict] = []
    next_url: str | None = url
    while next_url:
        if top is not None and len(records) >= top:
            break
        response = await request_with_retry(
            http_client, "GET", next_url, headers=request_headers
        )
        response.raise_for_status()
        body = response.json()
        for item in body.get("value", []):
            records.append(item)
            if top is not None and len(records) >= top:
                break
        next_url = body.get("@odata.nextLink")
    return records


def odata_quote(value: str) -> str:
    """Escape a value for use inside an OData single-quoted string literal."""
    return value.replace("'", "''")


def encode_odata_literal(value: str) -> str:
    """Escape *value* for a single-quoted OData literal embedded in a request URL.

    Two independent layers are required and BOTH matter:

    1. ``odata_quote`` doubles single quotes — the OData string-literal escape.
    2. ``quote(..., safe="")`` percent-encodes the result for the URL.

    Percent-encoding alone is NOT sufficient: Dataverse percent-decodes the whole
    URL *before* the OData expression is parsed, so a lone ``%27`` decodes back to
    ``'`` and terminates the literal early, letting a caller-supplied value break
    out of a key predicate such as ``EntityDefinitions(LogicalName='...')`` and
    navigate to an arbitrary resource. Doubling the quote first means the decoded
    form is ``''`` (an escaped quote) and stays inside the literal.
    """
    return quote(odata_quote(value), safe="")


def _truncate_message(text: str) -> str:
    """Cap error messages so large response bodies never flood tool output."""
    if len(text) > _MAX_ERROR_MESSAGE_LENGTH:
        return text[:_MAX_ERROR_MESSAGE_LENGTH] + "… (truncated)"
    return text


def extract_error_message(response: httpx.Response) -> str:
    """Extract a human-readable error message from an OData error response."""
    try:
        body = response.json()
        err = body.get("error", {})
        code = err.get("code", "")
        message = err.get("message", "") or response.text
        if code:
            return _truncate_message(f"[{code}] {message}")
        return _truncate_message(message) or f"HTTP {response.status_code}"
    except Exception as e:
        logger.debug("Could not parse error response as OData JSON: %s", e)
        return _truncate_message(response.text) or f"HTTP {response.status_code}"


def get_app_ctx(ctx: Any) -> AppContext:
    """Return the application context from a FastMCP request context."""
    return ctx.request_context.lifespan_context


def tool_error_response(e: Exception, tool_name: str) -> str:
    """Map an exception to the standard {"error": true, ...} JSON tool contract.

    Call from a tool's except block; ordering mirrors the specificity of the
    exception hierarchy (HTTP status first, broad Exception last).
    """
    if isinstance(e, httpx.HTTPStatusError):
        msg = extract_error_message(e.response)
        logger.error(
            "Dataverse HTTP %d in %s: %s", e.response.status_code, tool_name, msg
        )
        return json.dumps({
            "error": True,
            "message": f"Dataverse returned HTTP {e.response.status_code}: {msg}",
        })
    if isinstance(e, httpx.TimeoutException):
        logger.warning("Timeout in %s: %s", tool_name, e)
        return json.dumps({
            "error": True,
            "is_transient": True,
            "message": (
                "The request timed out before the server responded. The "
                "operation may still have completed on the server; verify "
                "before retrying."
            ),
        })
    if isinstance(e, (DataverseConnectionError, httpx.RequestError)):
        logger.error("Network error in %s: %s", tool_name, e)
        return json.dumps({"error": True, "message": str(e) or f"Network error: {type(e).__name__}"})
    if isinstance(e, ValueError):
        return json.dumps({"error": True, "message": str(e)})
    if isinstance(e, ClientAuthenticationError):
        # Log the detail to stderr (may contain provider-specific diagnostics)
        # but keep the user-facing message free of token/cred data.
        logger.error("Authentication error in %s: %s", tool_name, e)
        return json.dumps({
            "error": True,
            "message": (
                "Authentication failed. Run `az login` to refresh your Azure CLI "
                "session, or check DATAVERSE_AUTH_TYPE and your credential "
                "configuration."
            ),
        })
    logger.exception("Unexpected error in %s", tool_name)
    return json.dumps({
        "error": True,
        "message": f"Unexpected error: {type(e).__name__}: {e}",
    })


def finalize_response(payload: dict, *, max_bytes: int = _RESPONSE_MAX_BYTES) -> str:
    """Serialize a success payload, guarding against oversized responses."""
    text = json.dumps(payload)
    size = len(text)
    if size > max_bytes:
        logger.warning("Tool response of %d bytes exceeds cap (%d)", size, max_bytes)
        return json.dumps({
            "error": True,
            "message": (
                f"Response too large ({size / 1_000_000:.1f} MB). "
                "Narrow the query with select/top/filter."
            ),
        })
    if size > _RESPONSE_WARN_BYTES:
        logger.warning("Large tool response: %.1f MB", size / 1_000_000)
    return text


@asynccontextmanager
async def dataverse_lifespan(server) -> AsyncIterator[AppContext]:
    """FastMCP lifespan that initializes shared auth state."""
    if _URL_WHITELIST:
        logger.info(
            "DATAVERSE_WHITELIST active: restricting tool calls to %d environment(s): %s",
            len(_URL_WHITELIST),
            ", ".join(sorted(_URL_WHITELIST)),
        )
    elif _REQUIRE_WHITELIST:
        logger.error(
            "DATAVERSE_REQUIRE_WHITELIST is set but DATAVERSE_WHITELIST is empty; "
            "ALL tool calls will be rejected until DATAVERSE_WHITELIST is populated "
            "with approved environment hostname(s)."
        )
    else:
        logger.warning(
            "DATAVERSE_WHITELIST is not set; tool calls may target ANY environment URL "
            "and bearer tokens will be minted for it. Set DATAVERSE_WHITELIST to restrict "
            "access to approved Dataverse environments, and DATAVERSE_REQUIRE_WHITELIST=true "
            "to fail closed on shared/multi-tenant deployments."
        )

    auth_type = os.environ.get("DATAVERSE_AUTH_TYPE", "interactive").lower().strip()
    logger.info("Initializing Dataverse credential (auth: %s)", auth_type)

    credential = _build_credential(auth_type)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    ) as http_client:
        app_ctx = AppContext(
            credential=credential,
            auth_type=auth_type,
            http_client=http_client,
        )
        try:
            logger.info("Dataverse credential and HTTP client initialized")
            yield app_ctx
        finally:
            logger.info("Shutting down Dataverse MCP server")
            close_credential = getattr(credential, "close", None)
            if callable(close_credential):
                close_credential()
