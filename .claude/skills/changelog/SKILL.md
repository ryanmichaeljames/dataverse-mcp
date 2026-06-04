---
name: changelog
description: Maintains CHANGELOG.md for this project.
---

# Changelog - Keep a Changelog v1.1.0

Update `CHANGELOG.md` with entries for the current changes. Always write under `## [Unreleased]`.

## Categories

Use only what applies — omit the rest:

- **Added** — new tools or new options on existing tools
- **Changed** — changed behavior of existing tools
- **Fixed** — bug fixes
- **Removed** — tools or options removed
- **Security** — security fixes

## Entry rules

- One bullet per logical change, not per file or function
- Name the tool in backticks: `` `dataverse_list_tables` ``
- Describe what it does for the user, not how it's implemented
- Omit HTTP internals, refactors, dependency bumps, and test changes
- Keep each bullet to one sentence — enough to understand the impact

## What to skip

Do not add entries for:

- Internal refactors with no user-visible effect
- Dependency version bumps
- Code style or formatting changes
- Test additions or changes
- Documentation-only changes

## Example

```markdown
## [Unreleased]

### Added
- `dataverse_get_foo` tool to retrieve foo records by ID

### Security
- `dataverse_delete_bar` now requires `DATAVERSE_ALLOW_DELETE=true` to prevent accidental deletions

## [1.2.0] - 2026-05-01

### Added
- `dataverse_list_bars` tool to list bar records with optional status filter

### Changed
- `dataverse_get_foo` now returns `displayname` in addition to `id`

### Fixed
- `dataverse_list_foos` no longer returns duplicate entries when a filter is applied

### Removed
- `dataverse_legacy_query` tool removed; use `dataverse_query_table` instead

## [1.1.0] - 2026-04-01

### Added
- `dataverse_query_table` tool to query records with OData filter and column selection
```

## Steps

1. Read `CHANGELOG.md`
2. Review the diff or description of changes
3. Identify the correct categories and write concise bullets
4. Insert them under `## [Unreleased]`, creating category headers as needed
5. Do not modify any released version sections
