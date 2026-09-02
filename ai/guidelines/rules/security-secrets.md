# Security

## Secrets and Credentials

- Never write secrets, tokens, API keys, passwords, or credentials to any tracked file
- Machine-specific git identity (name, email, signingKey, credential helpers) belongs in `~/.gitconfig` — not in `git/gitconfig.shared` which is committed
- If you need to reference a secret in a script, read it from the environment — never hardcode it
- When editing `git/gitconfig.shared` or any tracked config: only add settings that are safe on any machine

## Secret File Model

The workbench uses two separate files for machine-specific secrets — use the right one:

| File | Purpose | Loaded by |
|------|---------|-----------|
| `~/.env.local` | Secrets for the **interactive shell**: MCP API keys, cloud credentials, Docker overrides | Shell on every session start (all subprocesses inherit) |
| `~/.config/task/taskfile.env` | Secrets for **AI automation tasks** only: `GH_TOKEN`, `ANTHROPIC_API_KEY` (if isolating billing) | Task runner scripts only (`task pr:*`, `task commit`, etc.) |

## `~/.env.local` — interactive shell secrets
- Sourced first by the workbench loader, before all config layers
- Use for: `CONTEXT7_API_KEY`, `JIRA_API_TOKEN`, `AWS_PROFILE`, `COLIMA_*` overrides
- See `zsh/.env.local.template` for a documented starting point
- **Optional** — `ANTHROPIC_API_KEY`, for `pr review`/`pr fix`: every agent the `ai/claude` pipeline runs uses `claude -p --bare`, and per `claude --help` `--bare` accepts only `ANTHROPIC_API_KEY` or an `apiKeyHelper` — OAuth and keychain are never read there. On a machine authenticated only via `claude login` (the normal state), `pr review`/`pr fix` fail every agent with `"Not logged in · Please run /login"` until this is set. No code change is needed to pick it up — `ai_backend_claude.py`'s subprocess calls default to `env=None`, which inherits the shell's environment as-is, `ANTHROPIC_API_KEY` included. Setting it here (not a dedicated file) is a deliberate trade: it switches *all* interactive `claude` usage in that shell to metered API billing too, not just the review pipeline — there is no isolation for this one. `claude setup-token`'s long-lived OAuth token does not work here; `--bare` rejects it categorically.

## `~/.config/task/taskfile.env` — AI automation credentials
- Read by `load_ai_command()` and `load_gh_token()` in `lib/ai/core.sh`
- `GH_TOKEN`: **required** for `task pr:*` — must be a fine-grained PAT scoped to specific repos with Contents (read/write) and Pull requests (read/write) only. Never use your full interactive gh session token for automation.
- `GH_TOKEN__<ORG>`: optional per-org overrides — `load_gh_token()` detects the current repo's GitHub org from `origin` and looks for `GH_TOKEN__<NORMALIZED_ORG>` first (e.g. `GH_TOKEN__OTTO_NATION` for repos under `otto-nation`). Org names are uppercased with hyphens replaced by underscores. Each PAT should be scoped to that org's repos only. Falls back to `GH_TOKEN` when no org-specific token matches.
- `ANTHROPIC_API_KEY`: optional — set to a separate key to isolate automation API usage from interactive Claude billing
- Run `task --global ai:setup` to scaffold this file with instructions
