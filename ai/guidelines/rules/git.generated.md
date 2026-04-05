# Git — Commits & Pull Requests
<!-- AUTO-GENERATED — do not edit directly -->
<!-- Source: lib/conventions.sh -->
<!-- Regenerate: bin/generate-git-rules -->

## Conventional Commits

- Format: `type(scope): subject`
- Scope is optional
- Allowed types: feat,fix,perf,deps,revert,docs,style,refactor,test,build,ci,chore
- Header (type + scope + subject) must be ≤ 72 characters total
- No period at the end of the subject
- Use a semicolon (`;`) to separate multiple changes in the header
- Separate header and body with a blank line
- Each body line must be ≤ 100 characters — wrap long lines
- Use bullet points for multiple changes in the body
- Subject should be concise and focus on *what* changed, not *how*
- Never add a co-author trailer (e.g. `Co-Authored-By:`)

### Allowed types

- `feat`
- `fix`
- `perf`
- `deps`
- `revert`
- `docs`
- `style`
- `refactor`
- `test`
- `build`
- `ci`
- `chore`

### Header length budgeting

Before writing, calculate available subject characters (header max is 72):

- `feat(auth): ` is 12 chars → subject ≤ 60 chars
- `fix: ` is 5 chars → subject ≤ 67 chars
- `refactor(payments): ` is 20 chars → subject ≤ 52 chars

### Examples

```
feat(auth): add OAuth2 login flow

- Support Google and GitHub providers
- Store tokens in encrypted session cookie
```

```
fix: resolve race condition in cache eviction; update TTL default
```

## Pull Requests

### Title

- Must follow conventional commit format (same as commit header)
- ≤ 72 characters

### Description template

Use this structure when no project-level PR template exists:

```markdown
## Summary

## Changes

## Testing
```

### Single-commit PRs

- Use the commit subject as the PR title
- Use the commit body as the description when present

### Issue linking

- Extract issue number from branch name (e.g. `feature/PROJ-123-description`)
- Add `Closes #<number>` at the top of the description only for numeric GitHub issues
- Omit issue linking for Jira-style keys (`PROJ-123`) — they do not auto-close on GitHub merge

### Footer

- Never append an AI-generated footer (e.g. 'Generated with Claude Code') — use only the PR template content

## Branch Naming

- Format: `username/<ISSUE or type if no ISSUE>/description_in_snake_case`
- Examples: `carlos/PROJ-42/oauth_login`, `sofia/PROJ-7/fix_cache_race`, javier/feat/add_metrics
- Use the git user name (first name, lowercase) as the username prefix
