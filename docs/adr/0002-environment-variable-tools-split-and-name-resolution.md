# ADR-0002: Split environment variable tools into definition + value sets with name-based resolution

- Status: Proposed
- Date: 2026-06-22
- Supersedes: ADR-0001

## Context

ADR-0001 decided to fold environment variable *values* into four *definition* tools and
explicitly rejected separate value tools, on the reasoning that a value has no independent
meaning to a user. A partial implementation of that decision already exists
(`tools/environment_variables.py`, four tools + matching models).

A new product directive overrides that reasoning. Agents need to address the *value* record
directly — set, change, or clear the current value of an existing variable without re-stating
the definition, and target a value either by its own GUID or by resolving its parent
definition. The folded-upsert model cannot express "operate on the value record only" as a
first-class operation. The directive is non-negotiable and requires two distinct tool sets
(~8 tools).

A second force: every tool that targets a specific record by GUID is hard for an agent to
drive, because the agent rarely holds the GUID — it knows the variable by name. Both tool sets
must therefore accept a **name** (schemaname or displayname) and resolve it to a GUID
internally.

Two Web API facts from ADR-0001 remain authoritative and verified:
- The definition→value collection nav property is
  `environmentvariabledefinition_environmentvariablevalue` (lowercase).
- The value→definition bind property is `EnvironmentVariableDefinitionId` (PascalCase);
  the lookup column for filtering values is `_environmentvariabledefinitionid_value`.
- `solutioncomponent` `componenttype`: `380 = Environment Variable Definition`,
  `381 = Environment Variable Value`.

A third fact, verified for this ADR: Dataverse string columns use **case-insensitive
collation**, so OData `eq` on `schemaname`/`displayname` already matches case-insensitively.
Nested function calls such as `tolower(field)` are unreliable on Dataverse (only schema 2.1+,
not in the documented function set) and must not be used. Plain `eq` is the correct primitive.

## Decision

We will **split** the surface into two domain modules, ~8 tools, each record-targeting tool
offering a name path in addition to a GUID path.

**Definitions** stay in `tools/environment_variables.py` (keep existing 4 tools and models —
minimal change, add a `name` lookup arg; keep the value `$expand` join and the
`target=definition|value|both` delete mode):
- `dataverse_get_environment_variables` (read) — add a `name` arg matching
  schemaname/displayname; keep solution filter and the value `$expand` join.
- `dataverse_create_environment_variable` (write) — unchanged.
- `dataverse_update_environment_variable` (write) — unchanged (definition + upsert value).
- `dataverse_delete_environment_variable` (delete) — unchanged (`target` cascade mode).

**Values** go in a NEW module `tools/environment_variable_values.py` (4 new tools, 4 new
models). Each resolves its target by **value GUID**, **definition GUID**, or **definition
name**:
- `dataverse_get_environment_variable_values` (read).
- `dataverse_create_environment_variable_value` (write) — bind via
  `EnvironmentVariableDefinitionId@odata.bind`.
- `dataverse_update_environment_variable_value` (write) — PATCH existing value.
- `dataverse_delete_environment_variable_value` (delete) — DELETE the value record only.

**Shared name-resolution helper.** A single async helper
`_resolve_definition_by_name_or_id(app_ctx, base_url, headers, *, definition_id, name)`
lives in `tools/environment_variables.py` (the definitions module) and is **imported by**
`tools/environment_variable_values.py`. Import direction:
`solutions.py` ← `environment_variables.py` ← `environment_variable_values.py` — a strict
chain, no cycle. (`environment_variables.py` already imports `solutions.py` helpers, accepting
the cross-module private-helper coupling that ADR-0001 had tried to avoid; this is the prevailing
pattern and we standardise on it.) The new values module never imports `solutions.py` directly.

**Name-resolution contract** (the helper's algorithm), given a `name`:
1. Query `environmentvariabledefinitions` with
   `$filter=schemaname eq '{q}'`, `$select=environmentvariabledefinitionid,schemaname,displayname`,
   `$top=2`. `eq` is case-insensitive on Dataverse; `{q}` is `odata_quote(name)`.
   - exactly 1 row → resolved.
   - 0 rows → fall through to step 2.
   - schemaname is unique, so >1 is not expected; treat ≥2 as resolved-to-first defensively
     only if it ever occurs (it should not).
2. If schemaname found nothing, query `$filter=displayname eq '{q}'`, `$top=2`.
   - exactly 1 row → resolved.
   - 0 rows → return `{"error": true, "message": "No environment variable matched name '<name>'."}`.
   - ≥2 rows → return
     `{"error": true, "message": "Name '<name>' matched multiple definitions; use schema_name or a GUID."}`.

   schemaname takes **precedence** over displayname (exact unique key tried first); displayname
   is a fallback and is the only field that can collide. The helper returns the resolved
   definition GUID (and schemaname) or raises/returns the structured error; callers surface it
   verbatim via the standard error contract.

**Overlap resolution (delete).** Both `dataverse_delete_environment_variable`
(`target=value|both`) and the new `dataverse_delete_environment_variable_value` can remove a
value record. We keep both and document the split: the definition delete tool's `target` mode
is the *cascade* path (delete definition, or definition+value together); the new value-delete
tool is the *flexible-target* path (resolve by value GUID OR definition name/GUID, value only).
They do not conflict — different entry intents.

**solutions.py mismatch (flag for builder, not fixed here).** `COMPONENT_TYPE_NAMES` maps
`371/372/373` all to `"Connector"` and `380/381` to Definition/Value (correct). The CHANGELOG
claims `372/373` were "left as Unknown" — a doc/code mismatch. The builder reconciles the
CHANGELOG wording to match the code (`372/373 = "Connector"`); no code change to the map.

## Consequences

- Good: agents can address the value record directly; name paths make every tool drivable
  without a GUID; `eq`-based resolution is correct and cheap on Dataverse.
- Good: definitions module and its models change minimally (one new `name` arg); existing
  implementation and tests survive.
- Bad: `~8` tools instead of 4 — larger surface, two overlapping delete paths to document.
- Bad: name resolution adds one extra round trip (and a second when displayname falls back);
  accepted — it is the only way to honour the name-path UX rule.
- Bad: standardises cross-module private-helper imports (the chain), reversing ADR-0001's
  isolation goal; accepted as the prevailing repo pattern.

## Confirmation

Backend reconciles the CHANGELOG `372/373` wording to match code under `[Unreleased]`. Tools
must keep truthful annotations (read/write/delete), the `{"error":true,"message":...}` contract
for zero/multiple name matches, `count` on lists, `has_more` on paginated reads, and `top=50`
default. Writes gated by `DATAVERSE_ALLOW_WRITE`, deletes by `DATAVERSE_ALLOW_DELETE`.
