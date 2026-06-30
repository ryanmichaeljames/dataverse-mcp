# Security Review — dataverse-mcp

- **Date:** 2026-06-30
- **Version reviewed:** 3.4.1 (`main` @ b6fd9ce)
- **Scope:** Full server source under `src/dataverse_mcp/` — authentication & secrets, input handling / injection, SSRF, write/delete safeguards, XML parsing, error handling, dependencies, TLS.
- **Method:** Read-only static audit across four adversarial dimensions; all blocking findings were independently re-verified against source before inclusion.

## Summary

The codebase is, overall, well-secured. Authentication and token handling are sound, OData string-value escaping uses `odata_quote()`, URL validation is strict (HTTPS-only, no credentials, port ≤443, IDNA canonicalization, optional host allowlist), dependencies are pinned with a `uv.lock`, TLS verification is left at httpx defaults (OS CA bundle, never disabled), and there is no use of `eval`/`exec`/`pickle`/`subprocess` or `print()`.

However, the review found **two High-severity issues** and several Medium/Low items that should be addressed. The two High issues are concrete, reachable, and have clear fixes.

| # | Severity | Area | Issue |
|---|----------|------|-------|
| 1 | **High** | Write/delete gating | Three DELETE-performing tools are gated by `@write_tool` only, bypassing `DATAVERSE_ALLOW_DELETE` |
| 2 | **High** | XML / DoS | User-supplied FetchXml/LayoutXml/FormXml parsed with stdlib `ElementTree` (billion-laughs DoS) |
| 3 | Medium | Injection | `entity_set_name` has no format validation → path traversal into other API endpoints |
| 4 | Medium | Injection | `BatchOperationItem.url` pattern `^/[^\r\n]*$` permits `?`, `#`, `&` injection |
| 5 | Medium | SSRF | With `DATAVERSE_WHITELIST` unset (default), a bearer token is minted for any caller-supplied host |
| 6 | Low | Info leak | Catch-all error handler returns raw exception string to the caller |

---

## Findings

### 1. DELETE operations bypass the `DATAVERSE_ALLOW_DELETE` safeguard — **High**

The server gates mutations with two env flags, `DATAVERSE_ALLOW_WRITE` and `DATAVERSE_ALLOW_DELETE`, enforced at registration time via `@write_tool()` / `@delete_tool()` decorators (`_app.py:76-80`). The flags are independent by design — an operator may permit writes but forbid deletes.

Three tools issue HTTP `DELETE` requests but are decorated with `@write_tool` (gated on `DATAVERSE_ALLOW_WRITE` only). With `ALLOW_WRITE=true` and `ALLOW_DELETE=false`, a caller can still perform these destructive operations:

- `dataverse_assign_app_role` — `apps.py:1042` (decorator) / `apps.py:1052` (def). Issues `DELETE` to disassociate a security role from a model-driven app when `action='remove'`.
- `dataverse_remove_security_role` — `security.py:483` / `:493`. Issues `DELETE` to strip a security role from a user/team.
- `dataverse_remove_team_members` — `security.py:615` / `:625`. Issues `DELETE` to remove users from a team.

All three also declare `destructiveHint: False`, which is untruthful per the project's own annotation rule.

**Why it matters:** these operations alter the security posture of the environment (role/team membership). They are exactly what `DATAVERSE_ALLOW_DELETE=false` is meant to block. The batch-gating fix (#116) already established the correct runtime-check pattern for dynamic-verb tools.

**Fix:** for each tool, either (a) split the dual-action tool so the remove path is its own `@delete_tool`, or (b) add a runtime `DATAVERSE_ALLOW_DELETE` check before issuing the `DELETE`, mirroring `tables.py:660-682`. Set `destructiveHint: True` on the destructive path.

### 2. User-supplied XML parsed with unsafe `ElementTree` — billion-laughs DoS — **High**

`defusedxml` is a dependency and is used correctly in some places (`forms.py:156`, `views.py:818,978`), but several parse sites that receive **caller-controlled** XML use the standard-library `xml.etree.ElementTree`, which does not defend against entity-expansion (billion-laughs) attacks:

- `views.py:424` and `views.py:477` — `_validate_view_xml(fetchxml, layoutxml)`. Per its own docstring, this validator "runs automatically before every PATCH," so it parses the FetchXml/LayoutXml a caller passes into the view create/update tools. **Reachable with attacker input.**
- `forms.py:858` — `ET.fromstring(params.formxml)` parses caller-supplied Form XML directly.

A caller can supply a recursive-entity payload (≈1 KB expanding to gigabytes) and exhaust server memory.

**Verified non-issue (do not over-fix):** `apps.py:137` (`_validate_sitemap_xml`) was flagged by one reviewer but is **not** caller-reachable — both call sites (`apps.py:632,952`) parse XML the server itself builds via `_build_sitemap_xml(...)` from structured input, not raw caller XML. Migrating it to `defusedxml` is still worthwhile for consistency, but it is not an exploitable XXE.

**Fix:** parse all caller-supplied XML with `defusedxml.ElementTree.fromstring` and handle `DefusedXmlException`, exactly as `forms.py:156` already does. Audit every stdlib `ET.fromstring` site (`views.py:324,367,424,477,...`; `forms.py:641,730,858,890,951`) and confirm whether the input originates from a caller (must be defused) or from a trusted Dataverse server response (lower risk, but defusing is cheap insurance).

### 3. `entity_set_name` lacks format validation → path traversal — **Medium**

`entity_set_name` (e.g. `models.py:802,868,902` and many metadata models) is declared `str = Field(..., description=...)` with no `pattern`. It is interpolated directly into the request path, e.g. `tables.py`:

```python
full_url = f"{base_url}/api/data/{_DATAVERSE_API_VERSION}/{entity_set}?{urlencode(...)}"
```

A value such as `accounts/../<other-endpoint>` is path-normalized by the client/server and reaches a different API path than intended — all under a valid bearer token, confined to the same Dataverse host. Lower than the SSRF case (no cross-host token leak) but still an input-validation gap that violates the project's "validate field constraints" rule.

**Fix:** add `pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"` to the `entity_set_name` fields (OData collection-name grammar), or percent-encode the segment before interpolation.

### 4. `BatchOperationItem.url` pattern is too permissive — **Medium**

`models.py` validates batch operation URLs with `pattern=r"^/[^\r\n]*$"`. This correctly blocks CRLF header injection but still permits `?`, `&`, and `#`, which are interpolated unescaped into each inner batch request (`batch.py`). A caller can append extra system query options (e.g. `/accounts?$filter=...&injected=1`) to an operation URL.

**Fix:** tighten to forbid query/fragment delimiters where they are not expected — e.g. `^/[^\s?#]*(\?[^\s#]*)?$` — or build the inner URL from validated path + separately-validated query parameters.

### 5. SSRF: token minted for any host when allowlist is unset — **Medium (operational)**

`dataverse_url` is supplied per call. `_normalize_org_url` / `normalize_dataverse_url` (`client.py:478-548`) enforce HTTPS, reject embedded credentials, reject non-443 ports, strip paths, and canonicalize the host — solid. The `DATAVERSE_WHITELIST` allowlist correctly restricts which hosts a token may be minted for, **but it is optional and off by default**. With it unset, a caller who controls `dataverse_url` can have a real bearer token minted and sent to an arbitrary HTTPS host.

This is by-design and documented, and the server logs a loud warning when the allowlist is empty. Tokens are scoped per-host so there is no cross-host token reuse.

**Recommendation:** document `DATAVERSE_WHITELIST` as a **required** hardening step for any non-local/shared deployment; consider making an empty allowlist fail-closed (or at least a more prominent warning) in multi-tenant setups.

### 6. Catch-all error handler leaks exception detail — **Low**

`client.py:843-847` (`tool_error_response`) returns `f"Unexpected error: {type(e).__name__}: {e}"` to the caller for any unhandled exception. Specific handlers above it (auth, timeout, network) already return sanitized messages, but the fallback can surface internal paths, hostnames, or other state in the raw `str(e)`.

**Fix:** return a generic message to the caller (e.g. "An unexpected error occurred; see server logs.") and keep the detail in the existing `logger.exception` call only.

---

## Verified-clean areas

- **Authentication & token cache** — scope is keyed per base URL (`client.py:600`), preventing cross-environment token reuse; `DATAVERSE_TOKEN_CACHE_PROFILE` is charset-validated to prevent collision/traversal; the `AuthenticationRecord` persisted to disk is secret-free; tokens are never logged or returned. Double-checked-locking cold-cache acquisition with per-scope `asyncio.Lock` is correct, and the lock releases on timeout. POSIX cache files are `0o600`; on Windows the per-user `LOCALAPPDATA` ACL is relied upon (acceptable for single-user; note the residual risk under elevated/admin processes).
- **OData string escaping** — `odata_quote()` is applied where the server builds quoted filter values; freeform `$filter` strings are accepted by design and URL-encoded with `safe='$,'`.
- **TLS** — `httpx.AsyncClient` is never configured with `verify=False`; no `http://` is accepted anywhere.
- **DoS limits** — page size capped at 500; `bulk_upsert` record count and chunk size capped at 1000; response size capped at 5 MB.
- **Dependencies** — `httpx>=0.20.0,<1.0`, `defusedxml>=0.7.1`, `azure-identity>=1.25.3`, `mcp[cli]>=1.27.0`; `uv.lock` present; no known CVEs in this set as of the review date.
- **No dangerous primitives** — no `eval`/`exec`/`pickle`/`os.system`/`subprocess`; no `print()`; logging is via `logging` to stderr.

---

## Recommended remediation order

1. **#1 (gating)** — small, mechanical, closes a real security-boundary bypass. Add a regression test mirroring `tests/test_batch_safeguard.py`.
2. **#2 (XML)** — swap caller-reachable parse sites to `defusedxml`; extend `tests/test_xml_dos.py` to cover view create/update and form create paths.
3. **#3, #4** — add Pydantic `pattern` constraints + validation tests.
4. **#5** — documentation / fail-closed decision for shared deployments.
5. **#6** — sanitize the catch-all error message.

## Residual risk / notes for verification

- Findings #1, #2, #3, #6 were verified directly against source during this review. #4 and #5 are reported from the dimension audits and are consistent with the code paths cited but were not re-exploited end-to-end.
- This was a static review. None of the issues were dynamically exploited against a live environment.
