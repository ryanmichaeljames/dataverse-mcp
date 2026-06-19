# Design: Tool Description Audit & Style Guide

- Status: Draft · Date: 2026-06-19 · Related: 118 tools across `src/dataverse_mcp/tools/*.py`, param schemas in `src/dataverse_mcp/models.py`

## Summary

Tool descriptions (function docstrings) and parameter descriptions (`Field(description=...)`)
are the only signal an LLM agent has when choosing among 118 similarly-named Dataverse tools and
calling them correctly. This document audits the current descriptions, gives an actionable style
guide a builder can apply uniformly to all 118 tools, shows before/after rewrites for the
highest-risk confusable clusters, and lists prioritized changes ordered by selection impact.

Optimization priority: **(1) selection accuracy and correct usage first, (2) token efficiency
second** — never trade away a disambiguating sentence to save tokens.

## Context & problem

- FastMCP uses the **function docstring** as the tool `description` (no explicit `description=` is
  passed to the decorators). The docstring's **first sentence** dominates tool selection.
- The current corpus is generally good — most docstrings already do action-summary-first and many
  already cross-reference sibling tools ("Use dataverse_list_views to discover view IDs"). The
  problems are **inconsistency** (three different docstring shapes across modules), a few
  **selection traps** in confusable clusters, **one factual bug**, and **localized bloat**.
- Quality is uneven by module: `tables.py` / `metadata.py` / `forms.py` / `views.py` write prose
  docstrings; `plugin_registration.py` uses a terse `Required:/Optional:` schema-echo style that
  duplicates the param schema. No single template is enforced.

## Goals / Non-goals

**Goals**
- A single docstring + Field template every module can be rewritten against.
- Fix selection traps in the confusable clusters and the one factual error.
- Cut duplicated/boilerplate text that costs tokens without aiding selection.

**Non-goals**
- No tool code, signature, annotation-logic, or behavior changes beyond docstrings, `Field`
  descriptions, and the annotation-hint corrections explicitly called out.
- Not re-litigating tool granularity (e.g. whether the plugin family should be fewer tools).

## Audit findings

### A. Selection accuracy / disambiguation

**A1. (BUG, highest priority) `AggregateTableInput.entity_set_name` points agents to a non-existent
tool.** `models.py:617-620` (and the `apply` examples) tell the agent to "Use
dataverse_get_entity_sets to discover the correct name" — correct — but the surrounding
data-cluster guidance is otherwise consistent. The real defect is broader: **there is no
`dataverse_create_record` / `update_record` / `delete_record` tool.** All single-record writes must
go through `dataverse_execute_batch`, yet no read/query docstring says so. An agent asked to "create
a contact" has no tool whose first line matches and will either fail or misuse a metadata tool. This
is the single biggest selection gap in the server.

**A2. `query_table` vs `get_record` vs `count_records` vs `aggregate_table` vs `execute_batch`.**
First lines are mostly distinct, but the boundaries aren't stated:
- `count_records` ("Count records … optionally filtered") vs `query_table`'s own `count=true` param
  (`tables.py:553-559`) overlap — an agent doesn't know that `count_records` is the cheaper path
  when it wants *only* a number. Neither says "use the other when…".
- `aggregate_table` is the only way to do group-by/sum, but `count_records` and `query_table`
  don't point to it for "how many per status" style questions.
- `execute_batch` is the de-facto create/update/delete tool but its first line
  ("Execute multiple OData operations…", `tables.py:428`) reads like a bulk-only / advanced tool,
  so agents won't reach for it for a single write.

**A3. `list_views` / `list_forms` / `list_apps` / `list_tables` / `list_solutions` — inconsistent
"next step" pointers.** `list_views` and `list_forms` are well-disambiguated (they name the
table-component they list and point to their `get_*`). `list_apps` (`apps.py:398`) and
`list_solutions` (`solutions.py:394`) are fine. But these five share no consistent first-line shape
("List X … in/for a Dataverse environment/table"), so an agent scanning first lines can't pattern-
match. Low correctness risk, real consistency cost.

**A4. Choice trio: `get_choice` / `list_choices` / `list_choice_column_options`.** This is currently
**well handled** — `list_choices` says "global … use dataverse_get_choice for full options",
`get_choice` says "global … by name or MetadataId", and `list_choice_column_options` says "local
(non-global) option set … global option sets not supported". The one gap: `list_choices` /
`get_choice` titles say "Global Choice" but the **tool names** omit "global"
(`dataverse_get_choice`), so an agent picking by name alone may use `get_choice` for a column's local
options. Add one "for column/local options use dataverse_list_choice_column_options" pointer to
`get_choice`.

**A5. Plug-in registration family (29 tools).** Internally very similar (assembly/package/type/step/
step_image/sdk_message/sdk_message_filter, each with get/list/create/update/delete). The terse style
actually helps here, but two real traps:
- `sdk_message` and `sdk_message_filter` are **read-only lookup** tools that exist to resolve the
  GUIDs that `create_plugin_step` needs. Their docstrings don't say "you call these to get the
  `message_id` / `filter_id` for dataverse_create_plugin_step" — the dependency is only visible from
  the step side.
- The create→register ordering (assembly/package → type → step → image) is implied by letter
  grouping in the source but **invisible to the agent**. No docstring states the prerequisite chain.

**A6. `set_formxml` vs `add_form_control` / `remove_form_control`.** Currently **well handled** —
`set_formxml` (`forms.py:1043-1059`) explicitly says "Handles form redesign scenarios that
add_form_control / remove_form_control cannot: adding/removing tabs and sections, reordering…". This
is the model the rest of the server should follow. Keep as-is (it is verbose, see C2, but the
disambiguation earns its tokens).

**A7. `check_relationship_eligibility`.** Good defensive wording ("Only call this immediately
before … do not call it for general queries", `metadata.py:773-777`) — this is a pattern worth
generalizing for any tool an agent might over-call.

### B. Usage-correctness gotchas

**B1. Read-modify-write (full PUT) is stated inconsistently.** `update_column`,
`update_relationship`, `update_choice` all require fetching the full definition first and PUTting it
back. The **Field descriptions** state this well (`UpdateColumnInput.full_definition`,
`UpdateChoiceInput.full_definition` at `models.py:1160-1166`, `1406-1413`). The **docstrings** are
inconsistent: `update_column` (`metadata.py:1221`) says "You must provide the complete column
definition JSON" but does **not** name `dataverse_get_column` as the source; `update_choice`
(`metadata.py:1841`) and `update_relationship` (`metadata.py:1617`) omit the "fetch first" step from
the docstring entirely (it lives only in the Field). The first line is what the agent reads when
deciding *how* to call — the "fetch current definition first with dataverse_get_X" must be in the
docstring, not only the param.

**B2. `entity_set_name` (collection) vs logical name.** Handled well and consistently in the
data-cluster Field descriptions ("OData collection name … Use dataverse_get_entity_sets"). Metadata
tools correctly use `table_logical_name`. No change needed — this is the model for B-class clarity.

**B3. Publish-after-write.** Metadata create/update/delete docstrings consistently say "Call
dataverse_publish_customizations after…". Forms/views tools instead **auto-publish** and say so
("Always publishes after saving"). Both are fine but the *difference* is a usage trap: an agent that
learns "always publish after metadata writes" may redundantly call publish after a form edit. Make
the auto-publish tools' wording uniform ("Publishes automatically — no separate publish needed").

**B4. Case-sensitive `expand` / navigation properties.** `QueryTableInput.expand`
(`models.py:546-552`) flags case-sensitivity well. `associate_records` points to
`list_relationships` for the navigation property name but does **not** repeat that nav props are
case-sensitive — worth one clause since a wrong-case nav prop is a common 400.

**B5. `dataverse_url` now required on every call.** This is enforced by the shared
`DataverseEnvironmentInput` base and its Field; individual docstrings correctly stay silent on it.
Keep it out of docstrings (it would be 118× redundant) — the Field on the base class is the right
single home.

**B6. `count` cap of 5000.** Stated in `count_records` docstring, `query_table.count` Field, and
`CountRecordsInput.filter` Field — slightly redundant across three places but all correct. Acceptable.

**B7. `execute_batch` GET-only-unless-write-enabled.** The docstring doesn't mention that non-GET
operations require `DATAVERSE_ALLOW_WRITE=true` (the tool enforces it at runtime, `tables.py:443`).
Since batch is the *only* write path for records (A1), this gating belongs in the docstring.

### C. Consistency

**C1. Three docstring shapes.** (a) prose with trailing "Use X to…" pointers (tables, metadata,
forms, views, environments, solutions, apps); (b) terse `Required:/Optional:` schema-echo
(plugin_registration); (c) numbered "Steps performed" (set_formxml, validate tools). The agent sees
all three interleaved. Pick one (below) and let validate/redesign tools extend it.

**C2. First-line length varies 6–25 words.** Good: "List saved views (savedqueries) for a Dataverse
table." Over-long: `aggregate_table`'s first paragraph plus a 7-line "Common patterns" block
(`tables.py:230-247`) duplicated almost verbatim in `AggregateTableInput.apply`
(`models.py:623-634`). `set_formxml` has a 4-line "Steps performed" list that restates the
implementation.

**C3. `Required:/Optional:` echo (plugin_registration) duplicates the schema.** The agent already
receives every parameter's name, type, requiredness, and Field description in the JSON schema.
Re-listing "Required: name, plugin_type_id (GUID), message_id (GUID), stage (10/20/40), mode (0/1)"
in the docstring (`plugin_registration.py:1363-1369`) double-pays tokens. Keep only the parts the
schema can't express: the **enum meanings** (stage 10=pre-validation/20=pre-op/40=post-op,
mode 0=sync/1=async) belong in the `Field`, and the **prerequisite chain** belongs in the docstring.

### D. Token efficiency

- **`aggregate_table`**: docstring "Common patterns" (~120 tokens) is duplicated in the `apply`
  Field (~110 tokens). Keep the rich version in **one** place — the `apply` Field, since that's
  where the agent composes the value — and cut the docstring to a 2-line summary + pointer.
- **plugin_registration `Required:/Optional:` blocks** (×~25 tools): ~40–70 tokens each of pure
  schema echo → ~1,000–1,500 tokens recoverable corpus-wide with zero selection loss.
- **`quick_find_warning` prose** is repeated verbatim in three view tools' docstrings *and* in the
  response body. The docstring only needs "Quick Find filter blocks are stripped before PATCH (a
  quick_find_warning is returned)"; the full 0x80040216 explanation belongs in the response message,
  not the schema.
- **`set_formxml` / `add_form_control` "Steps performed" / publish-mechanism paragraphs**: the
  "unpublished staging layer" explanation (`forms.py:694-696`, repeated at `953-955`) is valuable
  *once* as shared knowledge but is duplicated across form-write tools. Trim to one clause.

### E. Annotation / hint review

- **`dataverse_disassociate_records`** has `idempotentHint: True` (`tables.py:335`) — DELETE of a
  $ref is genuinely idempotent, correct. But `destructiveHint: True` while its sibling
  `associate_records` is non-destructive is right. No change.
- **`dataverse_execute_batch`** `idempotentHint: False`, `destructiveHint: False`
  (`tables.py:422-424`). A batch can contain DELETEs, so `destructiveHint: False` is **arguably
  wrong** — but since batch contents are dynamic, `False` (can't guarantee destructive) is defensible.
  Recommend leaving `False` but stating the write/delete capability in the docstring (ties to B7).
- **`dataverse_merge_records`** `destructiveHint: False` (`tables.py:376`) — merge **deactivates**
  the subordinate record (not deletes), so non-destructive is defensible; the docstring already says
  "deactivated (not deleted)". OK.
- **Hints are otherwise truthful and consistent** (read tools readOnly+idempotent; deletes
  destructive; creates non-idempotent). The hints are a *correct* secondary selection signal — the
  gap is purely in the prose, not the annotations.

## Proposed design — the style guide

Apply this uniformly to all 118 tools. A builder can mechanically rewrite against it.

### Docstring template

```
<First line: ONE sentence, ≤ ~18 words. Verb-first, names the exact Dataverse object/collection.
 This is the selection signal — make it unambiguous against siblings.>

<Optional 1–2 sentences: what it returns / its scope. Only if not obvious from the first line.>

<When-to-use / when-NOT (only for confusable tools): "Use <this> for X; use <sibling> for Y.">

<Prerequisite pointer (only if there is one): "Discover the <id> with <tool>.">

<Gotcha (only if it changes how you call it): full-PUT, auto-publish, write-flag, case-sensitivity.>
```

Rules:
1. **First line is a single declarative sentence**, present tense, verb-first ("List…", "Get…",
   "Create…", "Delete…"), and it must **name the concrete object and disambiguate from the nearest
   sibling** by noun choice ("global choice" vs "choice column options", "view (savedquery)" vs
   "form (systemform)"). No "This tool…" preamble.
2. **When-to-use vs a sibling** is mandatory for any tool in a confusable cluster (the clusters in
   findings A2/A4/A5/A6). One sentence: "For <other case> use <sibling tool>." If a tool has no
   confusable sibling, omit this line.
3. **Reference parameters by name** only when stating a cross-tool dependency ("pass the SchemaName
   from dataverse_list_relationships as schema_name"). Do **not** re-list the parameter table — the
   schema already carries names/types/requiredness. Delete `Required:/Optional:` blocks.
4. **Gotchas go in the docstring only if they change the call shape**: full-PUT ("fetch the current
   definition with dataverse_get_X first, then pass it as full_definition"), auto-publish
   ("publishes automatically"), write-flag ("non-GET operations require DATAVERSE_ALLOW_WRITE=true").
   Operational error explanations (HTTP codes, 0x… codes) belong in the **error/response message**,
   not the description.
5. **Length budget**: first line ≤ ~18 words; whole docstring ≤ ~60 words for simple tools, ≤ ~110
   for genuinely complex/confusable ones (set_formxml, aggregate, execute_batch). If you exceed 110,
   move detail into a `Field` description or the response body.
6. **Voice**: imperative/declarative, no marketing ("powerful", "easily"), no emoji, no Markdown
   headers inside the docstring.

### `Field(description=…)` rules

1. State the **format** the value must take and **where to get it** (collection name vs logical name
   vs GUID vs schema name; "Use dataverse_get_entity_sets to discover").
2. Put **enum semantics** here, not the docstring (stage 10/20/40 meanings, mode 0/1, check_type
   values) — this is where the agent composes the value.
3. Put the **canonical example** of a composed value (the `$apply` examples, the `$filter`
   examples) here, in exactly **one** place — never duplicated in the docstring.
4. Keep it to what the schema can't express: a `str` field's type is already known; don't say
   "a string that…". Flag case-sensitivity, lowercase-logical-name, and capping only where it bites.
5. Length budget: ≤ ~40 words per field; the 1–2 fields where the agent most often errs (filter,
   apply, full_definition, expand) may go to ~80.

## Before / after rewrites

These show the template applied — tighter **and** more selective.

### 1. `dataverse_execute_batch` (the hidden write path — A1/B7)

Before (`tables.py:428-435`, ~70 words):
> Execute multiple OData operations in a single HTTP request using the $batch endpoint. Supports up
> to 1,000 operations per request. Operations in the same change_set_id are executed atomically — if
> any fails, all in that set are rolled back. Returns a list of per-operation results: [{index,
> status_code, body}]. Change set results are flattened into the list in order.

After:
> Create, update, delete, or read records — single or bulk — via the OData $batch endpoint. This is
> the only tool that writes records (there is no separate create/update/delete-record tool); for
> metadata/schema changes use the dataverse_create_*/update_*/delete_* tools instead. Non-GET
> operations require DATAVERSE_ALLOW_WRITE=true. Group operations with the same change_set_id to run
> them atomically (all-or-nothing). Returns per-operation results [{index, status_code, body}].

### 2. `dataverse_count_records` (A2)

Before (`tables.py:176-182`):
> Count records in a Dataverse table, optionally filtered. Returns an integer count. Counts are
> capped at 5,000 by Dataverse — if total_count equals 5000 the actual count may be higher. Use
> filter to narrow the count to matching records, e.g., "statecode eq 0" to count only active records.

After:
> Count records in a table (optionally filtered) and return only the integer total. Use this instead
> of dataverse_query_table when you need a number, not rows; use dataverse_aggregate_table for
> per-group counts (e.g. count by status). The total is capped at 5,000 by Dataverse.

### 3. `dataverse_update_column` (B1 — docstring must name the read step)

Before (`metadata.py:1221-1226`):
> Update an existing column's metadata via full PUT replacement. The Dataverse metadata API does not
> support partial updates (PATCH) on attribute definitions. You must provide the complete column
> definition JSON. Call dataverse_publish_customizations after updating columns.

After:
> Update a column's metadata. First fetch the current definition with dataverse_get_column, change
> the fields you need, then pass the whole object as full_definition — the metadata API requires a
> full PUT, not a partial update. Publish with dataverse_publish_customizations afterward.

(Apply the identical "fetch current with dataverse_get_X first" shape to `update_relationship` →
`dataverse_get_relationship` and `update_choice` → `dataverse_get_choice`.)

### 4. `dataverse_get_choice` (A4 — point to the column/local tool)

Before (`metadata.py:724-730`):
> Get a specific global choice (option set) definition by name or MetadataId. Returns all option
> values, integer codes, and labels for the choice. Use this when you need the full option set for a
> global choice before filtering records or building picklist column definitions. Provide either
> name … or metadata_id …

After:
> Get one GLOBAL choice (option set) — all option values, codes, and labels — by name or MetadataId.
> For the options of a specific column's LOCAL choice, use dataverse_list_choice_column_options
> instead. Provide either name or metadata_id (name wins if both are given).

### 5. `dataverse_aggregate_table` (C2/D — move detail to the Field)

Before: docstring (`tables.py:230-247`, ~140 words with a 7-line patterns block) **plus** the same
patterns in the `apply` Field.

After (docstring only):
> Group and aggregate records with an OData $apply expression — count/sum/avg/min/max,
> countdistinct, and group-by. Use this for "per-group" questions (e.g. count by status);
> use dataverse_count_records for a single total and dataverse_query_table for raw rows. Works on
> up to 50,000 records. See the apply parameter for expression examples.

(The 6 worked examples and the "use countdistinct not count / no lookups in groupby / no $orderby on
aliases" gotchas stay in `AggregateTableInput.apply` — that Field is already correct; just stop
duplicating it.)

### 6. `dataverse_create_plugin_step` (C3 — drop the schema echo, keep the chain)

Before (`plugin_registration.py:1361-1370`):
> Register a plug-in step against a message (and optional entity filter).
> Required: name, plugin_type_id (GUID of the plugintype), message_id (GUID of the sdkmessage;
> resolve via dataverse_get_sdk_message), stage (10/20/40), mode (0/1).
> Optional: filter_id …, rank …, filtering_attributes …, supported_deployment …, async_auto_delete
> …, configuration …, description, dataverse_url.
> Requires DATAVERSE_ALLOW_WRITE=true.

After:
> Register a processing step that runs a plug-in type on an SDK message. Prerequisite chain: create
> the assembly (or package) → plug-in type → this step. Resolve message_id with
> dataverse_get_sdk_message and the optional entity-scoping filter_id with
> dataverse_get_sdk_message_filter. Requires DATAVERSE_ALLOW_WRITE=true.

(Move stage 10/20/40 and mode 0/1 enum meanings into the `stage` / `mode` Field descriptions; delete
the rest of the Required/Optional echo.)

### 7. `dataverse_get_sdk_message` (A5 — state why it exists)

Before (`plugin_registration.py:971`): "Resolve an SDK message by name or ID."

After:
> Resolve an SDK message (e.g. 'Create', 'Update', 'Delete') to its sdkmessageid. Call this to get
> the message_id required by dataverse_create_plugin_step.

### 8. Param `Field` rewrite — `CreatePluginStepInput.stage` (C3, enum semantics)

Before: stage carried only in the docstring's "stage (10/20/40)".

After (`Field`):
> description="Pipeline stage: 10 = pre-validation (outside the DB transaction), 20 = pre-operation
> (in transaction, before main op), 40 = post-operation (after main op). Not mutable after
> registration."

## Affected areas & work split

Documentation-only change to docstrings and `Field` descriptions — no logic. Disjoint scopes:

- **backend-developer (single owner — these are Python source edits, outside the architect's write
  scope):**
  - *Batch 0 (correctness, do first):* rewrite `execute_batch` (A1/B7) and add the "no
    create/update/delete-record tool; use execute_batch" pointer to `query_table` and `get_record`
    first lines. Fix the docstrings of `update_column` / `update_relationship` / `update_choice` to
    name the `get_*` read step (B1).
  - *Batch 1 (clusters):* apply the when-to-use sentence to the data cluster (A2), the choice trio
    (A4), the form-write trio (A6 — verify only; largely done), and add the prerequisite-chain /
    "why this exists" lines to the plug-in family (A5).
  - *Batch 2 (consistency + tokens):* normalize all 118 first lines to the template; strip the
    `Required:/Optional:` echo from plugin_registration and move enum semantics into `Field`s (C3/D);
    de-duplicate the `aggregate_table` and `quick_find_warning` text (D); unify auto-publish wording
    (B3).
- **No frontend / ux / dataverse-developer / devops involvement** — there is no UI or infra surface.

Suggested sequencing: Batch 0 → Batch 1 → Batch 2. Batch 0 is the only one that fixes *wrong agent
behavior*; Batches 1–2 improve hit-rate and cost.

## Prioritized recommendations (highest selection-impact first)

| # | Change | Impact | Token effect |
| --- | --- | --- | --- |
| 1 | Make `execute_batch` discoverable as THE record write/CRUD tool; add the "no create/update/delete-record tool" pointer to read tools (A1/B7) | **Critical** — fixes outright wrong/failed selection for all record writes | +~40 tokens (worth it) |
| 2 | Put "fetch current with dataverse_get_X first, full PUT" in the docstrings of update_column/relationship/choice (B1) | High — prevents malformed PUT calls | ~neutral |
| 3 | Add when-to-use sentences across the data cluster: count vs query vs aggregate (A2) | High — common, easily-confused reads | +~30 tokens total |
| 4 | Plug-in family: state prerequisite chain + why sdk_message/sdk_message_filter exist (A5) | High — 29 internally-similar tools | +~80, offset by #7 |
| 5 | get_choice → point to list_choice_column_options for local options (A4) | Medium | +~15 tokens |
| 6 | Normalize all 118 first lines to the template (C1/C2) | Medium — improves scan/pattern-match across modules | ~neutral |
| 7 | Strip `Required:/Optional:` echo, move enum meanings to Fields (C3/D) | Medium (token) | **−~1,200 tokens** corpus-wide |
| 8 | De-dup aggregate "Common patterns" and quick_find_warning prose (D) | Low (token) | **−~300 tokens** |
| 9 | Unify auto-publish vs manual-publish wording (B3) | Low — avoids redundant publish calls | ~neutral |

Net: large selection-accuracy gain, and roughly **−1,500 tokens** of schema sent on every request
once #7–#8 land — while items #1–#5 *add* the few hundred tokens that actually disambiguate. That is
the intended trade: spend tokens where they change the choice, reclaim them where they only echo.

## Risks & open questions

- **Risk:** ADRs/docstrings drift from behavior if code changes later. Mitigation: the "fetch
  first / full PUT / auto-publish / write-flag" facts are stable invariants per CLAUDE.md, so the
  drift surface is small.
- **Risk:** Over-trimming the plug-in family loses the only place enum meanings live. Mitigation:
  enum semantics must land in `Field`s in the *same* change that strips the docstring echo (#7).
- **Open question (for the maintainer, not blocking):** Should record CRUD get dedicated thin tools
  (`dataverse_create_record` etc.) instead of relying on `execute_batch`? That is a *tool-surface*
  decision beyond this description audit, but it is the root cause of finding A1 — recommendation #1
  is the cheap fix; dedicated tools would be the durable one. Flagging for a future ADR.
- **Verification:** none automated here (docstrings aren't unit-testable). A lightweight CI check
  could assert every `@mcp.tool/@write_tool/@delete_tool` function has a non-empty docstring whose
  first line is ≤ N words — optional, low priority.
