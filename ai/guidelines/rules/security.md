# Security

## Secrets and Credentials

- Never write secrets, tokens, API keys, passwords, or credentials to any tracked file
- Machine-specific git identity (name, email, signingKey, credential helpers) belongs in `~/.gitconfig.local` — not in `git/gitconfig` which is committed
- If you need to reference a secret in a script, read it from the environment — never hardcode it
- When editing `git/gitconfig` or any tracked config: only add settings that are safe on any machine

## Two-File Secret Model

The workbench uses two separate files for machine-specific secrets — use the right one:

| File | Purpose | Loaded by |
|------|---------|-----------|
| `~/.env.local` | Secrets for the **interactive shell**: MCP API keys, cloud credentials, Docker overrides | Shell on every session start (all subprocesses inherit) |
| `~/.config/task/taskfile.env` | Secrets for **AI automation tasks** only: `GH_TOKEN`, `ANTHROPIC_API_KEY` (if isolating billing) | Task runner scripts only (`task pr:*`, `task commit`, etc.) |

### `~/.env.local` — interactive shell secrets
- Sourced first by the workbench loader, before all config layers
- Use for: `CONTEXT7_API_KEY`, `JIRA_API_TOKEN`, `AWS_PROFILE`, `COLIMA_*` overrides
- See `zsh/.env.local.template` for a documented starting point

### `~/.config/task/taskfile.env` — AI automation credentials
- Read by `load_ai_command()` and `load_gh_token()` in `lib/ai/core.sh`
- `GH_TOKEN`: **required** for `task pr:*` — must be a fine-grained PAT scoped to specific repos with Contents (read/write) and Pull requests (read/write) only. Never use your full interactive gh session token for automation.
- `ANTHROPIC_API_KEY`: optional — set to a separate key to isolate automation API usage from interactive Claude billing
- Run `task --global ai:setup` to scaffold this file with instructions
