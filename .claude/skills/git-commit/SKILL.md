---
name: git-commit
description: Create a conventional commit by analysing the staged/unstaged diff, inferring type and scope, and executing the commit. Invoke with /git-commit.
allowed-tools: Bash
---

# Git Commit — Conventional Commits v1.0.0

## Specification (all 16 rules)

1. Commits **must** be prefixed with a type (a noun: `feat`, `fix`, etc.), optional scope, optional `!`, then a required colon and single space.
2. `feat` **must** be used when a commit introduces a new feature (SemVer MINOR).
3. `fix` **must** be used when a commit patches a bug (SemVer PATCH).
4. A scope **may** follow the type as a noun in parentheses: `fix(parser):`.
5. The description **must** immediately follow the colon-space: `feat: add login`.
6. A body **may** follow after one blank line; it is free-form and may span multiple paragraphs.
7. One or more footers **may** follow after one blank line; each has a token, a separator (`: ` or ` #`), and a value.
8. Footer tokens use hyphens for internal whitespace except for the special token `BREAKING CHANGE`.
9. Footer values may contain spaces and newlines.
10. Breaking changes **must** be indicated either in the footer (`BREAKING CHANGE: <desc>`) or with `!` before the colon.
11. `BREAKING CHANGE` in a footer **must** be uppercase (SemVer MAJOR).
12. `BREAKING-CHANGE` is synonymous with `BREAKING CHANGE` as a footer token.
13. Types beyond `feat` and `fix` are permitted; recommended set below.
14. All parts are case-insensitive **except** `BREAKING CHANGE`, which must remain uppercase.
15. `!` **may** be appended to any type/scope to draw attention to a breaking change (can appear alongside a `BREAKING CHANGE` footer).

## Format

```
<type>[(<scope>)][!]: <description>

[body]

[footer(s)]
```

## Commit types

| Type       | Purpose                                    | SemVer impact |
| ---------- | ------------------------------------------ | ------------- |
| `feat`     | New feature                                | MINOR         |
| `fix`      | Bug fix                                    | PATCH         |
| `build`    | Build system or external dependency change | –             |
| `chore`    | Maintenance tasks not touching src/tests   | –             |
| `ci`       | CI pipeline / config changes               | –             |
| `docs`     | Documentation only                         | –             |
| `perf`     | Performance improvement                    | –             |
| `refactor` | Code change that is neither feat nor fix   | –             |
| `revert`   | Reverts a previous commit                  | –             |
| `style`    | Formatting, whitespace (no logic change)   | –             |
| `test`     | Add or update tests                        | –             |

## Breaking change examples

```
# Bang notation
feat!: drop support for Node 6

# Footer notation
feat(auth): replace session tokens with JWTs

BREAKING CHANGE: session token format changed; existing tokens are invalid

# Both
refactor(api)!: rename /users endpoint to /accounts

BREAKING CHANGE: all callers must update base URL
```

## Workflow

### 1. Inspect the diff

```bash
git status --porcelain
git diff --staged    # if anything is staged
git diff             # if nothing is staged
```

### 2. Stage files (when nothing is staged)

Stage by logical grouping — one commit per concern. Never stage secrets (`.env`, credential files, private keys).

```bash
git add path/to/file1 path/to/file2
git add 'src/components/*'
```

### 3. Infer type, scope, and description from the diff

- **Type** — pick the most specific type from the table above.
- **Scope** — the module, solution, or layer affected (e.g. `api`, `plugins`, `webresources`). Omit if the change is repo-wide.
- **Description** — imperative mood, present tense, ≤72 characters, no trailing period.

### 4. Execute the commit

```bash
# Simple one-liner
git commit -m "fix(api): resolve null ref in request handler"

# With body and/or footer
git commit -m "$(cat <<'EOF'
feat(portal): add document upload to credit form

Adds a drag-and-drop file input that uploads to SharePoint via the
existing document service. Validates MIME type and size client-side.

Closes #412
EOF
)"
```

## Safety rules

- Never amend a published commit — create a new one.
- Never skip hooks (`--no-verify`) unless the user explicitly asks.
- Never force-push to `master`/`main`.
- If a pre-commit hook fails, fix the issue and create a **new** commit; do not `--amend`.
- Never touch `git config`.
