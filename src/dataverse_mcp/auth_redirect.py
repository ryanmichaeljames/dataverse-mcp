"""Branded landing page for the interactive sign-in redirect.

``InteractiveBrowserCredential`` starts a throwaway HTTP server on
``http://localhost:{8400..8999}`` and parks the browser there while it captures
the ``?code=`` (or ``?error=``) query string Entra ID redirects back with.  The
stock azure-identity handler answers that request with a single unstyled line of
text — ``Authentication complete. You can close this window.`` — which is the
last thing a user sees after signing in to this server.

This module replaces that response with a page styled after
``assets/dataverse-mcp-banner.svg`` (near-black canvas, 80px grid, #e11d2e
accent) while keeping the credential's contract intact.

**The server contract is duck-typed, not inherited.**  azure-identity's
``InteractiveBrowserCredential`` accepts a ``_server_class`` keyword and uses it
as ``_server_class(hostname, port, timeout=…)``, then calls
``wait_for_redirect()`` and expects the parsed query params back (an empty
mapping on timeout).  :class:`BrandedAuthCodeRedirectServer` implements exactly
that surface on top of ``http.server.HTTPServer`` rather than subclassing
``azure.identity._internal.AuthCodeRedirectServer``, so an upstream refactor of
that private module cannot break the page at sign-in time.

**Nothing from the query string is echoed except the error fields.**  The
``code`` parameter is a live authorization code: it is captured for the
credential and never rendered.  ``error``/``error_description`` are rendered
HTML-escaped and length-capped, because they are the only way a user can see why
a failed sign-in failed.
"""

from __future__ import annotations

import logging
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)

# Entra's error_description carries the message plus a correlation id and
# timestamp; anything past this is noise and would only stretch the card.
_MAX_ERROR_CHARS = 600

# Palette lifted from assets/dataverse-mcp-banner.svg.
_BG = "#09090b"
_GRID = "#111114"
_CARD = "#0d0d10"
_BORDER = "#1e1e23"
_TEXT = "#f5f5f4"
_MUTED = "#8b8b91"
_ACCENT = "#e11d2e"
_ACCENT_DEEP = "#7f101a"

# The banner's layered diamond, reduced to two nested rings.
_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'%3E"
    "%3Crect%20width='32'%20height='32'%20rx='6'%20fill='%2309090b'/%3E"
    "%3Cpolygon%20points='16,4%2029,16%2016,28%203,16'%20fill='none'"
    "%20stroke='%23f5f5f4'%20stroke-width='2'/%3E"
    "%3Cpolygon%20points='16,10%2022,16%2016,22%2010,16'%20fill='%237f101a'"
    "%20stroke='%23e11d2e'%20stroke-width='2'/%3E"
    "%3C/svg%3E"
)

_CHECK_MARK = (
    f'<svg class="mark" viewBox="0 0 48 48" aria-hidden="true">'
    f'<circle cx="24" cy="24" r="20" fill="none" stroke="{_TEXT}" stroke-width="3"/>'
    f'<path d="M15 24.5 L21 30.5 L33.5 18" fill="none" stroke="{_ACCENT}" '
    f'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
    f"</svg>"
)

_CROSS_MARK = (
    f'<svg class="mark" viewBox="0 0 48 48" aria-hidden="true">'
    f'<circle cx="24" cy="24" r="20" fill="{_ACCENT_DEEP}" stroke="{_ACCENT}" '
    f'stroke-width="3"/>'
    f'<path d="M17 17 L31 31 M31 17 L17 31" fill="none" stroke="{_TEXT}" '
    f'stroke-width="4" stroke-linecap="round"/>'
    f"</svg>"
)

_STYLE = f"""
*, *::before, *::after {{ box-sizing: border-box; }}
html, body {{ height: 100%; }}
body {{
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
  background-color: {_BG};
  background-image:
    linear-gradient(to right, {_GRID} 1px, transparent 1px),
    linear-gradient(to bottom, {_GRID} 1px, transparent 1px);
  background-size: 80px 80px;
  color: {_TEXT};
  font-family: ui-sans-serif, system-ui, "Segoe UI", "DejaVu Sans", sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.card {{
  position: relative;
  width: 100%;
  max-width: 560px;
  padding: 44px 44px 36px;
  background: {_CARD};
  border: 1px solid {_BORDER};
  border-radius: 14px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.55);
}}
.card::before, .card::after {{
  content: "";
  position: absolute;
  width: 26px;
  height: 3px;
  background: {_ACCENT};
}}
.card::before {{ top: -1px; left: 28px; }}
.card::after {{ bottom: -1px; right: 28px; }}
.eyebrow {{
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 30px;
}}
.eyebrow .bar {{
  width: 6px;
  height: 30px;
  background: {_ACCENT};
  flex: none;
}}
.eyebrow .label {{
  font-family: ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
  font-size: 13px;
  letter-spacing: 2.5px;
  color: {_MUTED};
}}
.status {{
  display: flex;
  align-items: center;
  gap: 18px;
}}
.mark {{ width: 48px; height: 48px; flex: none; }}
h1 {{
  margin: 0;
  font-size: 38px;
  font-weight: 700;
  letter-spacing: -0.5px;
  line-height: 1.05;
}}
.rule {{
  width: 132px;
  height: 7px;
  border-radius: 4px;
  background: {_ACCENT};
  margin: 26px 0 20px;
}}
p {{
  margin: 0;
  font-size: 16px;
  line-height: 1.6;
  color: {_MUTED};
}}
p + p {{ margin-top: 12px; }}
.detail {{
  margin-top: 20px;
  padding: 14px 16px;
  border: 1px solid {_BORDER};
  border-left: 3px solid {_ACCENT};
  border-radius: 6px;
  background: {_BG};
  font-family: ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #a1a1a8;
  overflow-wrap: anywhere;
}}
.detail .key {{ color: {_MUTED}; }}
footer {{
  margin-top: 34px;
  padding-top: 18px;
  border-top: 1px solid {_BORDER};
  font-family: ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
  font-size: 11px;
  letter-spacing: 2px;
  color: #55555d;
}}
@media (max-width: 520px) {{
  .card {{ padding: 32px 26px 28px; }}
  h1 {{ font-size: 30px; }}
}}
"""

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title} &middot; dataverse-mcp</title>
<link rel="icon" href="{favicon}">
<style>{style}</style>
</head>
<body>
<main class="card">
  <div class="eyebrow"><span class="bar"></span><span class="label">DATAVERSE MCP</span></div>
  <div class="status">{mark}<h1>{heading}</h1></div>
  <div class="rule"></div>
  <p>{body}</p>
  {detail}
  <footer>AI AGENTS &nbsp;&#8596;&nbsp; MICROSOFT DATAVERSE</footer>
</main>
</body>
</html>
"""


def _clean(value: Any) -> str:
    """Escape *value* for HTML and cap its length.

    ``parse_qs`` can hand back a list when a parameter repeats; join those so a
    duplicated ``error`` renders instead of printing a Python repr.
    """
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    text = str(value).strip()
    if len(text) > _MAX_ERROR_CHARS:
        text = text[:_MAX_ERROR_CHARS] + "…"
    return escape(text)


def render_auth_redirect_page(params: Mapping[str, Any]) -> str:
    """Render the sign-in landing page for a redirect's *params*.

    Success and failure are told apart by the presence of ``error`` — Entra
    redirects back with ``code`` on success and ``error``/``error_description``
    on failure.  ``code`` is never rendered.
    """
    error = params.get("error")

    if error:
        description = params.get("error_description")
        rows = [f'<div><span class="key">error:</span> {_clean(error)}</div>']
        if description:
            rows.append(
                f'<div><span class="key">description:</span> {_clean(description)}</div>'
            )
        detail = '<div class="detail">' + "".join(rows) + "</div>"
        return _PAGE.format(
            title="Sign-in failed",
            favicon=_FAVICON,
            style=_STYLE,
            mark=_CROSS_MARK,
            heading="SIGN-IN FAILED",
            body=(
                "Microsoft Entra ID did not return a token. Close this window "
                "and retry the tool call that started the sign-in."
            ),
            detail=detail,
        )

    return _PAGE.format(
        title="Signed in",
        favicon=_FAVICON,
        style=_STYLE,
        mark=_CHECK_MARK,
        heading="SIGNED IN",
        body=(
            "The MCP server has your token. You can close this window and "
            "return to your editor."
        ),
        detail="",
    )


class BrandedAuthCodeRedirectHandler(BaseHTTPRequestHandler):
    """Capture the redirect's query params and answer with the branded page."""

    # Keeps the response line from advertising the Python version.
    server_version = "dataverse-mcp"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if self.path.endswith("/favicon.ico"):
            self.send_response(204)
            self.end_headers()
            return

        query = self.path.split("?", 1)[-1] if "?" in self.path else ""
        parsed = parse_qs(query, keep_blank_values=True)
        params = {
            k: v[0] if isinstance(v, list) and len(v) == 1 else v
            for k, v in parsed.items()
        }
        # Publish to the server last: wait_for_redirect() spins on this, so the
        # response must be fully written before the credential can race ahead
        # and close the socket.
        body = render_auth_redirect_page(params).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

        self.server.query_params = params  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence the default stderr access log.

        Stdout is reserved for MCP stdio transport; the base class writes to
        stderr, but a redirect hit is noise either way.
        """


class BrandedAuthCodeRedirectServer(HTTPServer):
    """Drop-in replacement for azure-identity's ``AuthCodeRedirectServer``.

    Implements the ``_server_class`` contract by duck typing: constructed as
    ``(hostname, port, timeout=…)``, driven by ``wait_for_redirect()``, and
    returning an empty mapping if the user never completes the sign-in.
    """

    query_params: Mapping[str, Any] = {}

    def __init__(self, hostname: str, port: int, timeout: int) -> None:
        HTTPServer.__init__(self, (hostname, port), BrandedAuthCodeRedirectHandler)
        self.timeout = timeout

    def wait_for_redirect(self) -> Mapping[str, Any]:
        while not self.query_params:
            try:
                self.handle_request()
            except (OSError, ValueError):
                # The socket was closed, most likely by handle_timeout.
                break

        # No-op when the socket is already closed.
        self.server_close()

        # An empty dict here means the wait timed out.
        return self.query_params

    def handle_timeout(self) -> None:
        """Break the request-handling loop by tearing the server down."""
        self.server_close()
