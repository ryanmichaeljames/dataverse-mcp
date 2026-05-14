---
name: changelog
description: Maintain CHANGELOG.md entries for this repo using Keep a Changelog format. Focus on manual, notable updates under Unreleased.
---

# Changelog Maintenance

Instructions for agent maintaining `CHANGELOG.md` in this repository.

## Standards References

- Keep a Changelog: https://keepachangelog.com/en/1.1.0/
- Semantic Versioning: https://semver.org/spec/v2.0.0.html

## Use This Skill When

- User asks to update changelog.
- PR/commit adds notable behavior change.
- Release prep needs Unreleased cleanup.

## Repository Rules

- Update entries only under `[Unreleased]` unless user explicitly asks for release cut.
- Add entries only under existing Keep a Changelog sections:
  - `Added`
  - `Changed`
  - `Deprecated`
  - `Removed`
  - `Fixed`
  - `Security`
- If needed section missing under `[Unreleased]`, create it in canonical order above.
- Do not edit past released versions.
- Do not reorder historical entries.
- Keep bullets short, specific, user-facing.
- Skip noise: refactors, formatting, test-only, CI-only, dependency-only (unless user-facing fix/security impact).

## Agent Workflow

1. Inspect diff/commits for notable behavior changes.
2. Map each notable change to one changelog section.
3. Write one bullet per change in past tense.
4. Include scope nouns from repo domain (`tools`, `metadata`, `solutions`, `tables`, `environments`) when helpful.
5. Edit `CHANGELOG.md` minimally; preserve style and spacing.

## Writing Style

- Good: `- Added dataverse_list_choices support for client-side top limiting.`
- Good: `- Fixed dataverse_query_table error handling for HTTP status failures.`
- Bad: `- Updated code.`
- Bad: `- Misc fixes and cleanup.`

## Release Cut Guidance (Only If Asked)

- Move `[Unreleased]` entries into new version heading: `## [x.y.z] - YYYY-MM-DD`.
- Recreate empty `[Unreleased]` section at top.
- Do not change older version text.

## Guardrails

- Never fabricate changes not present in diff/history.
- Prefer fewer accurate bullets over broad vague summary.
- If no notable change exists, state that and leave `CHANGELOG.md` unchanged.
