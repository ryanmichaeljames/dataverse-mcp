"""Shared Dataverse auth helpers and lifespan management."""

import asyncio
import functools
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from azure.identity import AzureCliCredential, InteractiveBrowserCredential

logger = logging.getLogger(__name__)

SUPPORTED_AUTH_TYPES = ("interactive", "azure_cli")
_DATAVERSE_API_VERSION = "v9.2"
_TOKEN_REFRESH_BUFFER_SECONDS = 300
_MAX_ERROR_MESSAGE_LENGTH = 2000

# Dataverse service-protection limits return 429; 502/503/504 are transient
# gateway failures. Anything else is returned to the caller untouched.
_RETRYABLE_STATUS_CODES = (429, 502, 503, 504)
_MAX_RETRY_AFTER_SECONDS = 30.0
_DEFAULT_RETRY_AFTER_SECONDS = 2.0
# Dataverse pages are capped server-side at 5,000; we never ask for more than
# 500 per page to keep individual responses small.
_MAX_PAGE_SIZE = 500

# Hostname suffixes accepted for Dataverse organization URLs. Tokens are minted
# for whatever host a tool call supplies, so unknown hosts must be rejected.
_DEFAULT_ALLOWED_HOST_SUFFIXES = (
    ".dynamics.com",
    ".dynamics-int.com",
    ".crm.dynamics.cn",
    ".microsoftdynamics.us",
    ".microsoftdynamics.de",
)


def _load_allowed_host_suffixes() -> tuple[str, ...]:
    """Combine default Dataverse host suffixes with DATAVERSE_ALLOWED_HOST_SUFFIXES."""
    raw = os.environ.get("DATAVERSE_ALLOWED_HOST_SUFFIXES", "")
    extra = tuple(
        s if s.startswith(".") else f".{s}"
        for s in (part.strip().lower() for part in raw.split(","))
        if s
    )
    return _DEFAULT_ALLOWED_HOST_SUFFIXES + extra


# Read once at import: normalize_dataverse_url is lru_cached, so the allowlist
# must not vary per call.
_ALLOWED_HOST_SUFFIXES = _load_allowed_host_suffixes()

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
        return InteractiveBrowserCredential()

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
    fallback_dataverse_url: str | None
    http_client: httpx.AsyncClient
    _token_cache: dict[str, tuple[str, float]] = field(default_factory=dict)
    _token_locks: dict[str, asyncio.Lock] = field(default_factory=dict)


@functools.lru_cache(maxsize=64)
def normalize_dataverse_url(url: str) -> str:
    """Validate and normalize a Dataverse organization URL."""
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
    if parsed.path not in ("", "/"):
        raise ValueError("dataverse_url must not include a path")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(
            "dataverse_url must not include params, query strings, or fragments"
        )

    hostname = parsed.hostname.lower()
    if not hostname.endswith(_ALLOWED_HOST_SUFFIXES):
        raise ValueError(
            f"dataverse_url host '{hostname}' is not an allowed Dataverse domain. "
            f"Allowed suffixes: {', '.join(_ALLOWED_HOST_SUFFIXES)}. "
            "Set DATAVERSE_ALLOWED_HOST_SUFFIXES to permit additional domains."
        )

    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    return f"https://{netloc}"


def resolve_base_url(app_ctx: AppContext, dataverse_url: str | None) -> str:
    """Resolve the effective base URL, raising ValueError if none configured."""
    url = dataverse_url or app_ctx.fallback_dataverse_url
    if not url:
        raise ValueError(
            "No Dataverse environment URL was provided. Supply dataverse_url on the "
            "tool input, or set DATAVERSE_URL as a legacy fallback."
        )
    return normalize_dataverse_url(url)


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
            token = _get_cached_bearer_token(app_ctx, scope)
            if token is None:
                token = await asyncio.to_thread(get_bearer_token, app_ctx, scope)
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
    - 502/503/504 retry with exponential backoff (1s, 2s, 4s)
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


@asynccontextmanager
async def dataverse_lifespan(server) -> AsyncIterator[AppContext]:
    """FastMCP lifespan that initializes shared auth state."""
    fallback_dataverse_url = os.environ.get("DATAVERSE_URL")
    if fallback_dataverse_url:
        fallback_dataverse_url = normalize_dataverse_url(fallback_dataverse_url)
        logger.info(
            "Configured legacy DATAVERSE_URL fallback for %s", fallback_dataverse_url
        )
    else:
        logger.info("No DATAVERSE_URL fallback configured; tools must provide dataverse_url")

    auth_type = os.environ.get("DATAVERSE_AUTH_TYPE", "azure_cli").lower().strip()
    logger.info("Initializing Dataverse credential (auth: %s)", auth_type)

    credential = _build_credential(auth_type)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    ) as http_client:
        app_ctx = AppContext(
            credential=credential,
            auth_type=auth_type,
            fallback_dataverse_url=fallback_dataverse_url,
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
