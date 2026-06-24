# Linear

- When creating Linear issues, describe the problem and desired outcome — not implementation details. Structure as: Context (what prompted this), Problem (what's wrong), Goal (desired outcome). Keep specific files, code snippets, and implementation steps in the plan, not the issue.
- Linear issue titles should follow conventional commit format: `type(scope): subject` — same rules as git commit headers (72 chars, allowed types, no period). This keeps issues, commits, and PRs consistent.
- When an issue's scope expands beyond its original title/description, retitle and rewrite the description to reflect the broader scope. If the original scope was a meaningful unit of work, preserve it as a sub-issue rather than losing that context.
- Prefer sub-issues over expanding a single issue's scope when the work has distinct deliverables. A parent issue should describe the initiative; sub-issues should each be independently completable. Don't nest sub-issues more than one level.
- Always assign issues to the creator (`--assignee self`) — unassigned issues get lost
- Always create the Linear issue before creating a branch — the branch naming convention requires the issue ID prefix, so the issue must exist first

## CLI Operations

### Issue tracker CLI patterns (generic)

- Extract the team/project key from the issue identifier — `ENG-1698` means team `ENG`, `PROJ-42` means team `PROJ`. Never run `team list` or `project list` to discover what you already have
- Read error messages before retrying — most CLI errors name the missing flag (e.g. "Could not determine team key" → add `--team`). Fix the specific problem, don't guess-and-retry
- Permission and OAuth scope errors are hard blockers — surface them to the user immediately. Running `auth status`, `auth whoami`, or other diagnostic commands cannot fix a missing OAuth scope; tell the user what scope is needed and stop
- When a CLI outputs JSON, pipe through `head -50` first to see the schema before writing `jq` filters — don't guess field paths

### Linear CLI reference

Team key is always the prefix of the issue identifier. Pass it explicitly where required:

| Command | Notes |
|---------|-------|
| `linear issue view <ID>` | View issue details |
| `linear issue view <ID> --json` | JSON output — pipe through `head` before jq |
| `linear issue create --team <KEY> --assignee self --title "..." --description "..."` | `--team` and `--assignee self` are required |
| `linear issue relation add <ID> <type> <relatedID>` | Types: `blocks`, `blocked-by`, `related`, `duplicate`. Requires `write` OAuth scope |
| `linear team list` | Rarely needed — team key is in the identifier |

Parallel-safe: `issue view` + `create --help` lookups can run concurrently. Batch independent CLI calls in a single response.
