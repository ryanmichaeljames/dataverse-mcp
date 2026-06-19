# Design: Persistent token cache for interactive auth (issue #63)

- Status: Draft   ·   Date: 2026-06-19   ·   Related: GitHub issue #63

## Summary
`InteractiveBrowserCredential` is constructed with no cache-persistence options, so MSAL
holds refresh tokens in memory only. Every server restart discards them and forces a fresh
browser sign-in. We will make the interactive credential persist its MSAL cache to disk
(OS-encrypted by default) and reuse the cached account on restart, so a valid refresh token
silently mints a new access token instead of re-prompting. This is opt-out, default-on, and
unencrypted-storage is never enabled automatically.

## Context & problem
- `_build_credential("interactive")` returns a bare `InteractiveBrowserCredential()`
  (`client.py:214`). MSAL's token cache therefore lives only in process memory.
- The in-process `_token_cache` dict on `AppContext` (`client.py:239`) is a *separate*,
  per-process access-token cache and is irrelevant to this issue — it cannot survive a
  restart and is not where refresh tokens live. **Do not change it.**
- The fix is entirely about MSAL's credential-level persistent cache for the interactive
  credential. `azure_cli` auth is unaffected (it delegates to the `az` token cache and never
  re-prompts in-process).
- Two things are needed for silent reauth after restart:
  1. a **persistent cache** (`TokenCachePersistenceOptions`) so the refresh token is on disk;
  2. a stable **account identity** so MSAL knows which cached account to silently reuse.
     With a persistent cache but no remembered account, the credential can still prompt on
     first call after restart because it has no `AuthenticationRecord` to anchor the lookup.

## Goals / Non-goals
**Goals**
- Interactive auth survives server restarts without a new browser prompt while a refresh
  token is valid.
- Encrypted-at-rest by default on all platforms; plaintext cache only via explicit opt-in.
- Env-driven, defensive-parsing config consistent with existing patterns.

**Non-goals**
- No change to `azure_cli` auth, the in-process `_token_cache`, or the token-acquisition /
  locking path in `build_headers` / `get_bearer_token`.
- No multi-account / account-picker UX. Single cached account is sufficient.
- No new third-party dependency (see below).

## Proposed design

### API — verified against installed `azure-identity==1.25.3`
- `from azure.identity import TokenCachePersistenceOptions` — imports cleanly.
- Constructor (verified by introspection):
  `TokenCachePersistenceOptions(*, allow_unencrypted_storage: bool = False, name: str = "msal.cache")`.
- The `InteractiveBrowserCredential` keyword is **`cache_persistence_options`** (confirmed in
  the credential docstring), *not* `cache_persistence` as the issue text loosely phrased it.
  It is consumed via `**kwargs` by the MSAL client base.
- `InteractiveBrowserCredential(..., authentication_record=<AuthenticationRecord>)` is
  supported and is how we anchor silent reuse of the cached account.
- **Dependency:** already satisfied. `pyproject.toml` pins `azure-identity>=1.25.3`; cross-
  platform encrypted storage uses `msal-extensions`, which ships transitively with
  `azure-identity`. **No new dependency, no version bump required.**

### Encryption behaviour (the crux)
`TokenCachePersistenceOptions(allow_unencrypted_storage=False)` (the default) uses the OS
secret store and **raises at credential use if encryption is unavailable**:
- **Windows:** DPAPI (per-user). Available on all supported targets.
- **macOS:** Keychain. Available.
- **Linux:** libsecret / Secret Service (e.g. GNOME Keyring). Frequently **absent** on
  headless servers, containers, and minimal distros — there `allow_unencrypted_storage=False`
  fails fast.

**Decision: default to encrypted (`allow_unencrypted_storage=False`).** Never silently write
a plaintext token cache. A persisted refresh token is long-lived and high-value; an
unencrypted file (`0600` though it is) on a shared or backed-up host is a real exposure.
Failing fast on an unencrypted platform is the correct, observable default.

Provide an explicit, documented escape hatch for headless Linux:
`DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED=true`. When set, log a **warning** at startup that
tokens may be written to disk unencrypted, mirroring the existing `DATAVERSE_WHITELIST`
unset-warning style.

### Default-on vs opt-in
**Default-on** persistence for `interactive`. Re-prompting every restart is a defect, not a
feature; the secure default (encrypted cache) carries no plaintext risk. Operators who want
the old ephemeral behaviour set `DATAVERSE_TOKEN_CACHE_PERSIST=false`. This keeps a single
knob for "I don't want anything written to disk" without forcing opt-in friction on the
common, safe case.

### Persisting and reusing the account (silent reauth)
A persistent cache alone is not enough — the credential needs the `AuthenticationRecord` to
silently select the cached account on the next process. Recommended flow inside the
interactive branch of `_build_credential` (implementer's discretion on exact placement):

1. Build `TokenCachePersistenceOptions(name="dataverse-mcp.cache", allow_unencrypted_storage=<flag>)`.
2. On startup, attempt to load a previously serialized `AuthenticationRecord` from a small
   sidecar file under the OS user-config dir (the record contains **no secret** — only
   home-account id, tenant, authority, username — so it may be stored as plaintext JSON with
   `0600`). If present, pass it as `authentication_record=`.
3. Construct `InteractiveBrowserCredential(cache_persistence_options=..., authentication_record=...)`.
4. After the **first** successful interactive sign-in, call
   `credential.authenticate(scopes=[...])`, which returns an `AuthenticationRecord`; serialize
   it (`record.serialize()`) to the sidecar file for the next restart.

If step 2/4 (the record sidecar) is judged out of scope for a first pass, ship the persistent
cache alone: that still lets MSAL silently refresh for the duration of the process and removes
re-prompts within a session, and on most setups MSAL can match the single cached account on
restart. But to *reliably* eliminate the restart re-prompt — the literal issue title — the
`AuthenticationRecord` round-trip is the robust path and is the recommended implementation.

### Cache file location & name
- Cache name: `dataverse-mcp.cache` (distinct from MSAL's shared default `msal.cache` so this
  server's cache cannot collide with other Azure tools' caches).
- Let `msal-extensions` choose the platform-appropriate directory (do not hardcode paths).
- Sidecar auth-record file: under the same per-user config dir, e.g.
  `dataverse-mcp.authrecord.json`, `0600`.

## Alternatives considered
- **`allow_unencrypted_storage=True` by default** — rejected. Writes refresh tokens in
  plaintext on Linux/containers; an unacceptable silent downgrade for a security-sensitive
  artifact.
- **Opt-in (default off)** — rejected. The secure default has no plaintext downside, and
  leaving the documented defect in place by default is poor UX. A single off-switch is enough.
- **Persist the in-process `_token_cache` dict to disk** — rejected. That caches short-lived
  access tokens, not refresh tokens, and would reinvent MSAL's encrypted cache badly while
  writing bearer tokens to disk. Wrong layer.
- **Switch to `DeviceCodeCredential` / broker auth** — out of scope; changes UX and is
  unrelated to persistence.

## Affected areas & work split

**backend-developer (sole implementer — no frontend/ux/dataverse/devops needed):**
- `src/dataverse_mcp/client.py`
  - Add `TokenCachePersistenceOptions` to the `from azure.identity import ...` line.
  - Add two defensive env parsers next to `_get_auth_timeout_seconds` (same try/warn/default
    shape):
    - `DATAVERSE_TOKEN_CACHE_PERSIST` → bool, default `true`.
    - `DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED` → bool, default `false`.
  - In `_build_credential`, interactive branch: build the options and pass
    `cache_persistence_options=`; when persist is disabled, keep current bare construction.
    Log at `info` whether persistence is on and at `warning` when unencrypted storage is
    allowed.
  - (Recommended) Add `AuthenticationRecord` load-before / serialize-after-`authenticate`
    logic and the sidecar file helpers. Keep all disk paths off any committed code/test.
  - Treat `azure_cli` branch as untouched.
- `README.md` — add the two env vars to the Configuration table (with the unencrypted warning)
  and a sentence under Authentication noting interactive auth now persists across restarts.
- `CHANGELOG.md` — one entry under `[Unreleased] → Added` (and a `Security` note for the
  encrypted-by-default decision). Do not touch released sections.

## Env var contract
| Variable | Default | Behaviour |
|----------|---------|-----------|
| `DATAVERSE_TOKEN_CACHE_PERSIST` | `true` | When `true`, interactive auth persists its MSAL cache to disk (encrypted) and reuses it on restart. `false` = old in-memory-only behaviour. Invalid value → default `true` with logged warning. |
| `DATAVERSE_TOKEN_CACHE_ALLOW_UNENCRYPTED` | `false` | When `true`, permits a plaintext cache on platforms lacking an OS secret store (headless Linux). Logs a startup warning. Invalid value → default `false`. |

Parse both with the existing defensive idiom (trim, accept `true`/`false` case-insensitively,
warn-and-default on anything else). Only `interactive` reads these; ignore for `azure_cli`.

## Risks & open questions
- **Unencrypted platform fails fast (intended).** With defaults, on headless Linux without
  libsecret the *first token call* raises rather than startup. Mitigate with a clear startup
  log line stating persistence is enabled-and-encrypted, plus README guidance pointing to the
  `ALLOW_UNENCRYPTED` flag. Decide whether to probe encryption availability at startup for an
  earlier, friendlier error (nice-to-have, not required).
- **Cache corruption.** A truncated/corrupt cache file can make MSAL raise on load. The
  credential should degrade to a fresh interactive prompt, not crash the server. Implementer
  should ensure load failures are caught and fall back to no-record/fresh-auth with a warning;
  treat a corrupt sidecar auth-record the same way (ignore, re-authenticate, rewrite).
- **Concurrent processes / multiple server instances.** `msal-extensions` uses cross-process
  file locking on the cache, so two `dataverse-mcp` instances for the same user share one
  cache safely. Confirm the chosen cache `name` is intended to be shared (yes — same user,
  same app) and that lock contention is acceptable (it is; reads are brief).
- **First-run still prompts.** Expected — there is no cache yet. Only the *restart* prompt is
  eliminated. Set expectations in the changelog/README.
- **Refresh-token expiry / revocation.** After the refresh token expires or is revoked
  (conditional-access, password change), a prompt reappears. Correct and unavoidable.
- **Open question:** ship the `AuthenticationRecord` round-trip in this change, or land the
  persistent cache first and follow up? Recommendation: include it — without it the issue's
  literal "every restart" symptom may persist on some setups, undercutting the fix.

## Test approach (consistent with repo's live-integration-first philosophy)
- **Unit (where they earn their keep):** the two new env parsers — assert `true`/`false`,
  case-insensitivity, default, and warn-and-default on garbage. These are pure functions, no
  Azure needed; mirror `tests/test_odata_utils.py` style.
- **Unit (light):** that `_build_credential("interactive")` with persist enabled passes a
  `TokenCachePersistenceOptions` with the expected `name` and `allow_unencrypted_storage`
  reflecting the flag, and with persist disabled passes none — via a patched
  `InteractiveBrowserCredential` to capture kwargs. No real browser/auth.
- **Manual / live verification (primary, not automated):** run with
  `DATAVERSE_AUTH_TYPE=interactive`, sign in once, restart the server, confirm the next tool
  call does **not** open a browser and succeeds. Then delete the cache file and confirm a
  fresh prompt. Document these steps; do not commit any cache path or token.
- Do **not** add live integration tests that perform interactive browser auth (non-headless,
  not CI-runnable) — gate-and-skip would just be dead weight here.
