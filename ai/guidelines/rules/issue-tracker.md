---
description: "Issue trackers — how the provider resolves, and per-provider CLI conventions"
---

# Issue Tracker

## Which tracker this repo uses

- The SessionStart context line answers this: a configured repo shows `Issue tracker: {provider}` (e.g. `Issue tracker: github`); an unconfigured one shows `Issue tracker: not configured — ask before filing, then record the answer by running otto-workbench config set issue_tracker.provider PROVIDER`. Use it — do not infer a tracker from a branch name, a CLI that happens to be installed, or another repo's convention
- When it reads unconfigured, ask before filing, then record the answer with `otto-workbench config set issue_tracker.provider <provider>` — add `--project` to record it in this repo's `.workbench.yml` instead of for every repo on the machine. Never pick one silently
- Record it through that command rather than by editing `config.yml` or `.workbench.yml` by hand. The command refuses a key the installed workbench does not read; a hand-edit under a key you read out of a stale worktree is dropped on the next read, and nothing reports it — the value is simply gone and the question comes back days later
- For a repo other than the one this session is in, the Project Registry table in `~/.claude/machine/machine.md` names each registered repo's tracker in its `Issues` column, read from that repo's own `.workbench.yml` when the profile was written. A row reading `unset` is a repo that has never declared one — it is a question still owed, not a repo without a tracker
- Only the section below matching the resolved provider applies. The others describe CLIs this repo does not file to

## Writing an issue (any tracker)

- Describe the problem and desired outcome — not implementation details. Structure as: Context (what prompted this), Problem (what's wrong), Goal (desired outcome). Keep specific files, code snippets, and implementation steps in the plan, not the issue
- Titles follow conventional commit format: `type(scope): subject` — same rules as git commit headers (72 chars, allowed types, no period). This keeps issues, commits, and PRs consistent
- When an issue's scope expands beyond its original title/description, retitle and rewrite the description to reflect the broader scope. If the original scope was a meaningful unit of work, preserve it as a sub-issue rather than losing that context
- Prefer sub-issues over expanding a single issue's scope when the work has distinct deliverables. A parent issue should describe the initiative; sub-issues should each be independently completable. Don't nest sub-issues more than one level
- Always assign the issue to its creator — unassigned issues get lost
- Nothing is truly out of scope. Work a change defers is filed as its own issue, linked from the one deferring it, rather than described in a section and forgotten

## CLI patterns (any tracker)

- Extract the team/project key from the issue identifier — `ENG-1698` means team `ENG`, `PROJ-42` means team `PROJ`. Never run `team list` or `project list` to discover what you already have
- Read error messages before retrying — most CLI errors name the missing flag (e.g. "Could not determine team key" → add `--team`). Fix the specific problem, don't guess-and-retry
- Permission and OAuth scope errors are hard blockers — surface them to the user immediately. Running `auth status`, `auth whoami`, or other diagnostic commands cannot fix a missing OAuth scope; tell the user what scope is needed and stop
- When a CLI outputs JSON, pipe through `head -50` first to see the schema before writing `jq` filters — don't guess field paths

## Linear

Applies only when the resolved provider is `linear`.

- Create the issue before creating the branch — the branch naming convention requires the issue ID prefix, so the issue must exist first
- Assign with `--assignee self`
- Team key is always the prefix of the issue identifier. Pass it explicitly where required:

| Command | Notes |
|---------|-------|
| `linear issue view <ID>` | View issue details |
| `linear issue view <ID> --json` | JSON output — pipe through `head` before jq |
| `linear issue create --team <KEY> --assignee self --title "..." --description "..."` | `--team` and `--assignee self` are required |
| `linear issue relation add <ID> <type> <relatedID>` | Types: `blocks`, `blocked-by`, `related`, `duplicate`. Requires `write` OAuth scope |
| `linear team list` | Rarely needed — team key is in the identifier |

Parallel-safe: `issue view` + `create --help` lookups can run concurrently. Batch independent CLI calls in a single response.

## GitHub

Applies only when the resolved provider is `github`.

- Issues are addressed by repo, not by team — there is no team key to supply, and nothing should be skipped for want of one
- Assign with `--assignee @me`
- Put `Closes #<number>` at the top of a PR description to auto-close a numeric issue on merge. Jira-style keys (`PROJ-123`) do not auto-close, so omit the line for them
- Pass `--repo <owner>/<repo>` *after* the subcommand — `gh issue view --repo x/y`, never `gh --repo x/y issue view`. The permission allow list keys on per-subcommand prefixes

| Command | Notes |
|---------|-------|
| `gh issue view <N> --repo <owner>/<repo> --json title,body,comments` | Pipe through `head` before jq |
| `gh issue create --repo <owner>/<repo> --title "..." --body-file <path> --assignee @me` | `--body-file` for anything multi-line |
| `gh issue edit <N> --repo <owner>/<repo> --body-file <path>` | Replaces the body wholesale |
| `gh issue list --repo <owner>/<repo> --state open --json number,title` | |

## Jira

Applies only when the resolved provider is `jira`.

- No Jira CLI ships with this workbench. Issue links are built from `issue_tracker.jira_url`, and issue IDs are `PROJ-123` shaped — the same pattern Linear uses
- Creating and updating issues is not automated for Jira. Report what would have been filed and let the user file it
