# Getting Started

## Prerequisites

- macOS or Linux
- bash (to run `install.sh`)
- git (to clone the repo)

Everything else — Task, Homebrew, Docker, language tooling — is either auto-installed or available through the optional component menu.

## Installation

```bash
git clone https://github.com/otto-nation/otto-workbench ~/otto-workbench
cd ~/otto-workbench
./install.sh
```

The installer runs in three stages:

1. **Preflight** — installs `task` runner and Homebrew if not present
2. **Core components** — presents a menu of always-synced components (`bin`, `git`, `zsh`). Press Enter to install all.
3. **Optional components** — presents a menu of opt-in components (Homebrew packages, Docker, Terminals, Editors, AI tools). Press Enter to install all.

You can also skip menus: `./install.sh --all` installs everything, `./install.sh brew docker` installs only named components.

Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

## After Install

1. **Reload your shell**: `exec zsh`
2. **Docker** (if installed): start your runtime — `colima start` or launch OrbStack
3. **AI tools** (if installed): run `task --global ai:setup` to configure your `AI_COMMAND` and tokens

Secrets and machine-specific env vars go in `~/.env.local` — sourced first by the shell loader, never committed. See [`zsh/.env.local.template`](../zsh/.env.local.template) for the documented starting point.

## Your First Sync

After pulling workbench updates:

```bash
otto-workbench sync
```

This re-applies all workbench config: runs pending migrations, re-symlinks scripts and configs, regenerates tool context, and syncs AI settings. It's non-interactive and safe to run at any time.

For details on what sync does step-by-step, see [Execution Flow](execution-flow.md).

## Where to Go Next

| If you want to... | Read |
|---|---|
| Understand the system design | [Architecture](architecture.md) — component model, config layers |
| Know what happens during install/sync | [Execution Flow](execution-flow.md) — step-by-step walkthrough |
| Add or modify a component | [Component Framework](components.md) — tier contracts, discovery |
| Add a tool or registry entry | [Registries](registries.md) — schema, validation, discovery |
| See what's installed | [Tools & Scripts](tools.md) — full catalog |
| Set up AI tooling | [AI Automation](ai-automation.md) — Claude Code, skills, agents |
| Customize AI config | [User Overrides](user-overrides.md) — replace, extend, or disable |
| Understand the shared libraries | [Libraries](libraries.md) — all lib/ modules and their functions |
| Fix something broken | [Troubleshooting](troubleshooting.md) — common issues |
| Contribute changes | [Contributing](../CONTRIBUTING.md) — dev setup, tests, conventions |
