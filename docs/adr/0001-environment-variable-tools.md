# ADR-0001: Environment Variable definition + value tool surface

- Status: Superseded by ADR-0002
- Date: 2026-06-22

## Context

We need full CRUD over Dataverse environment variables. An environment variable is two
records: a **definition** (`environmentvariabledefinitions`, holds `schemaname`, `type`,
`defaultvalue`, etc.) and an optional **value** (`environmentvariablevalues`, holds the
current `value`, bound to the definition). The definition's effective value is the value
record if one exists, otherwise `defaultvalue`. A definition may have zero or one value
record per environment.

Two facts had to be resolved against the Microsoft Web API reference (verified
2026-02-06) because getting them wrong fails silently:

1. **Solution-component type.** The repo's `solutions.py` `COMPONENT_TYPE_NAMES` maps
   `372`/`373` to EVD/EVV — this is **wrong**. The authoritative `solutioncomponent`
   `componenttype` option set is `380 = Environment Variable Definition`,
   `381 = Environment Variable Value` (`371`/`372` are both "Connector"). Filtering
   `solutioncomponents` by `372` silently returns empty.
2. **Expand nav property.** `environmentvariabledefinition_environmentvariablevalue`
   (lowercase) is the 1:N collection nav from definition to value. The value's bind
   property is `EnvironmentVariableDefinitionId` (PascalCase).

## Decision

We will add **four** tools to a new module `tools/environment_variables.py`, folding the
value into the variable tools rather than exposing separate `..._value` tools — the value
has no independent meaning to a user managing a variable, and the create-if-missing-else-PATCH
behavior is cleaner hidden behind one update tool.

- `dataverse_get_environment_variables` (read) — list definitions, each joined to its
  current value via `$expand=environmentvariabledefinition_environmentvariablevalue($select=value,environmentvariablevalueid)`;
  surface `current_value` (value record's `value`, else `defaultvalue`) and `value_id`.
  Solution filter (by `solution_id` or `solution_unique_name`) resolves the solution, then
  queries `solutioncomponents` for `componenttype eq 380` to get definition GUIDs.
- `dataverse_create_environment_variable` (write) — POST definition; if `value` supplied,
  POST a value record bound via `EnvironmentVariableDefinitionId@odata.bind`.
- `dataverse_update_environment_variable` (write) — PATCH definition fields if any; for the
  value, look up the existing value record by `_environmentvariabledefinitionid_value` —
  PATCH if found, else POST a new one (upsert).
- `dataverse_delete_environment_variable` (delete) — delete the value record, the definition,
  or both, controlled by a `target` discriminator.

We will **replicate** the solution-filter locally rather than import `solutions.py` helpers
(`_resolve_solution_record` is private and importing it couples two domain modules); but we
will reuse the public client helpers. We define a module-local `_EV_DEFINITION_COMPONENT_TYPE
= 380`.

## Consequences

- Good: one mental model per tool; the definition↔value join and upsert complexity stay
  hidden from the caller.
- Good: `$expand` join needs one round trip for the list (no N+1).
- Bad: `solutions.py`'s component-type map is now known-wrong; this ADR does not fix it
  (flagged as a separate backend task).
- Bad: replicating the solution-resolve logic duplicates ~15 lines; accepted to avoid
  importing private helpers across domain modules.

## Considered Options

- **Separate value tools** (`dataverse_*_environment_variable_value`) — rejected: doubles the
  tool count for a sub-entity users never address independently, and splits the upsert logic.
- **Separate value query instead of `$expand`** — kept as documented fallback only; `$expand`
  is one round trip and the nav property is verified.
- **Reuse `solutions.py` helpers** — rejected: they are private (`_`-prefixed) and would
  couple the modules; the duplicated logic is trivial.

## Confirmation

Backend should add `380`/`381` correctly when touching env-var code and is asked to fix the
stale `372`/`373` entries in `solutions.py` `COMPONENT_TYPE_NAMES` under a separate change.
