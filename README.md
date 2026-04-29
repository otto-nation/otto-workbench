# otto-workbench

Personal developer environment manager. Manages shell config, git settings, brew packages, editor preferences, and AI coding tools through a component framework that keeps everything reproducible and in sync.

Run `install.sh` once on a new machine and your entire dev environment is configured. Pull updates and run `otto-workbench sync` to stay current.

## Quick Start

```bash
git clone https://github.com/otto-nation/otto-workbench ~/otto-workbench
cd ~/otto-workbench
./install.sh
exec zsh
```

The installer symlinks scripts, zsh configs, git config, and the global Taskfile, then presents an optional component menu (<!-- COMPONENT-MENU-START -->Homebrew packages, Docker, Terminals, Editors, AI tools<!-- COMPONENT-MENU-END -->).

## After Install

<!-- AFTER-INSTALL-START -->
1. **Reload your shell**: `exec zsh`
2. **Docker** (if installed): start your runtime — `colima start` or launch OrbStack
3. **AI tools** (if installed): run `task --global ai:setup` to configure your `AI_COMMAND` and tokens
<!-- AFTER-INSTALL-END -->

Secrets and machine-specific env vars go in `~/.env.local` — sourced first by the shell loader, never committed. See [`zsh/.env.local.template`](zsh/.env.local.template).

## Keeping in Sync

<!-- WORKBENCH-COMMANDS-START -->
| Command | Scope | Description |
|---------|-------|-------------|
| `otto-workbench sync` | All components | Re-applies all workbench config — runs pending migrations, re-symlinks scripts and configs, regenerates tool context, and syncs AI settings. Safe to re-run at any time. |
| `otto-workbench claude` | Claude only | Syncs machine-level Claude config, then scaffolds a `.claude/` directory in the current git repo (if one doesn't exist) with stack-detected rules and a project anatomy file. |
| `otto-workbench claude --force` | Claude only | Re-scaffolding an existing project's `.claude/` |
| `otto-workbench changelog` | Git history | Reviewing recent changes from conventional commits |
<!-- WORKBENCH-COMMANDS-END -->

## What's Included

- **[Scripts](docs/tools.md#scripts)** — workbench utilities for environment management, validation, and code generation
- **Shell** — ZSH configuration with [modular config layers](docs/architecture.md#shell-zsh), Starship prompt, and lazy-loaded plugin management
- **Git** — [two-layer gitconfig](docs/architecture.md#git), global hooks (secret scanning, linting), and conventional commit conventions
- **[Tools](docs/tools.md#installed-tools)** — CLI tools managed via Homebrew, organized by domain (shell, infra, languages, dev)
- **[AI](docs/ai-automation.md)** — Claude Code integration with skills, agents, guidelines, and AI-powered git automation
- **[Task automation](docs/ai-automation.md#task-automation)** — global Taskfile for AI-powered commits, PRs, and reviews

## How It Works

The workbench uses a [component framework](docs/architecture.md#component-model) with three tiers:

1. **Preflight** (`task`, `brew`, `mise`) — mandatory tooling, runs first
2. **Core** (`bin`, `git`, `zsh`) — always synced on every machine
3. **Optional** (`brew`, `docker`, `terminals`, `editors`, `ai`) — opt-in via install menu

`install.sh` bootstraps interactively. `otto-workbench sync` re-applies everything non-interactively. Both auto-discover components via glob patterns — adding a new component requires no edits to the installer.

See [Architecture](docs/architecture.md) for the full picture: configuration layers, registries, migrations, and generated files.

## File Layout

Both `install.sh` and `otto-workbench sync` print a summary of everything below.

### Managed files (updated by `otto-workbench sync`)

These are owned by the workbench and updated every time you sync. Do not edit directly.

| Target | Source | Method |
|--------|--------|--------|
| `~/.local/bin/*` | `bin/` | symlinked |
| `~/.config/zsh/config.d/*/` | `zsh/config.d/*/` | copied |
| `~/.config/zsh/config.d/loader.zsh` | `zsh/config.d/loader.zsh` | copied |
| `~/.config/starship.toml` | `zsh/starship.toml` | copied |
| `~/.gitconfig` | includes `git/gitconfig.shared` | include stanza |
| `~/.git-hooks/*` | `git/hooks/` | symlinked |
| `~/.config/task/{Taskfile.yml,lib/}` | `Taskfile.global.yml`, `lib/` | symlinked |
| `~/.claude/*` | `ai/claude/` | mixed (merge/copy/symlink) |

### Editable configs (yours — never overwritten)

These are created once (from templates or by first-time setup) and never modified by sync.

| File | Purpose | Bootstrap |
|------|---------|-----------|
| `~/.gitconfig` | Git identity, GPG, credentials | `git/gitconfig.template` |
| `~/.env.local` | Shell secrets, API keys, env overrides | `zsh/.env.local.template` |
| `~/.config/task/taskfile.env` | AI automation tokens (`GH_TOKEN`, `AI_COMMAND`) | `task --global ai:setup` |
| `~/.zshrc` | Shell rc file | `zsh/.zshrc` |
| `~/.config/ghostty/config` | Terminal config | `terminals/ghostty/config.template` |

## Requirements

- macOS or Linux
- bash (to run `install.sh`)
- git (to clone the repo)

Everything else — Task, gh, Docker, and language tooling — is either auto-installed by `install.sh` or available through the optional component menu.

## Learn More

- [Getting Started](docs/getting-started.md) — installation walkthrough, first sync, reading path
- [Architecture](docs/architecture.md) — system design: component model, config layers
- [Execution Flow](docs/execution-flow.md) — what happens during install and sync, step by step
- [Component Framework](docs/components.md) — tier contracts, discovery, adding components
- [Registries](docs/registries.md) — tool registry schema, validation, adding entries
- [Libraries](docs/libraries.md) — all lib/ modules: purpose and key functions
- [Tools & Scripts](docs/tools.md) — full catalog of installed tools and workbench scripts
- [AI Automation](docs/ai-automation.md) — Claude Code setup, skills, agents, and task automation
- [User Overrides](docs/user-overrides.md) — customizing AI config without editing tracked files
- [Troubleshooting](docs/troubleshooting.md) — common issues and solutions
- [Contributing](CONTRIBUTING.md) — dev setup, testing, and code conventions
