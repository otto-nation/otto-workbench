# Git — Commits & Pull Requests
<!-- AUTO-GENERATED — do not edit directly -->
<!-- Source: lib/conventions.sh -->
<!-- Regenerate: git/bin/generate-git-rules -->

## Conventional Commits

- Format: `type(scope): subject`
- Scope is optional
- Allowed types: feat,fix,perf,deps,revert,docs,style,refactor,test,build,ci,chore
- Header (type + scope + subject) must be ≤ 72 characters total
- No period at the end of the subject
- Use a semicolon (`;`) to separate multiple changes in the header
- Each body line must be ≤ 100 characters — wrap long lines
- Use bullet points for multiple changes in the body
- Subject should be concise and focus on *what* changed, not *how*
- Never add a co-author trailer (e.g. `Co-Authored-By:`)

## Pull Requests

### Title

- Must follow conventional commit format (same as commit header)
- ≤ 72 characters

### Description template

- Always use the repo's PR template (`.github/PULL_REQUEST_TEMPLATE.md`) when one exists
- Fall back to this structure only when no project-level PR template exists:

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

### Assignee

- Always assign PRs to the person opening them (`--assignee @me`)

### Footer

- Never append an AI-generated footer (e.g. 'Generated with Claude Code') — use only the PR template content

## Branch Naming

- Always branch from `origin/main`, never local `main` — local main may be stale
- Format: `username/<ISSUE or type if no ISSUE>/description_in_snake_case`
- Examples: `carlos/PROJ-42/oauth_login`, `sofia/PROJ-7/fix_cache_race`, javier/feat/add_metrics
- Use the git user name (first name, lowercase) as the username prefix
