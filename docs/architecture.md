# Architecture

How otto-workbench manages your developer environment — the concepts, layers, and conventions that hold it together.

## Overview

The workbench has two modes of operation:

```
install.sh (first-time, interactive)
├── Preflight: task, brew, mise — mandatory, runs first
├── Core menu: bin, git, zsh — selectable, Enter = all
└── Optional menu: brew packages, docker, terminals, editors, ai — selectable, Enter = all

otto-workbench sync (ongoing, non-interactive)
├── Runs pending migrations
├── Auto-discovers all components with steps.sh
└── Calls sync_<name>() for each — idempotent, no prompts
```

`install.sh` bootstraps a new machine with interactive menus. `otto-workbench sync` re-applies everything silently after pulling updates. Both are safe to re-run.

## Component Model

Components are organized into three tiers based on when and how they run.

**Preflight** components (`task`, `brew`, `mise`) install unconditionally before anything else — they provide the tooling other components depend on.

**Core** components (`bin`, `git`, `zsh`) are always synced. Each defines a [`sync_<name>()`](../CONTRIBUTING.md#sync_name-contract) function in its `steps.sh`. Adding a new core component requires only creating the directory with `steps.sh` — no edits to [`install.sh`](../install.sh).

**Optional** components (`brew`, `docker`, `terminals`, `editors`, `ai`) appear in the install menu. Each has a [`setup.conf`](components.md#tier-2--optional-components) for metadata and a `setup.sh` for interactive install. Components with idempotent operations also define `steps.sh` for sync coverage.

Discovery is automatic — [`install.sh`](../install.sh) globs `*/steps.sh` and skips any with a sibling `setup.conf` (those are optional). See the [Component Framework](components.md) reference for full contracts and examples.

## Configuration Layers

### Shell (ZSH)

ZSH configuration loads in layers from [`~/.config/zsh/config.d/`](../zsh/config.d/):

```
framework/  →  tools/  →  aliases/  →  prompt/
```

Order is significant — later layers can reference earlier ones. The [`loader.zsh`](../zsh/config.d/loader.zsh) script orchestrates this loading.

`~/.zshrc` is copied from [`zsh/.zshrc`](../zsh/.zshrc) on first install. It sets up oh-my-zsh (lazy-loaded), arch-aware Homebrew, and modular config loading.

### Git

Two-layer architecture:

| Layer | File | Owns |
|-------|------|------|
| Machine-specific (yours) | `~/.gitconfig` | Identity, GPG, credentials |
| Shared (workbench) | [`git/gitconfig.shared`](../git/gitconfig.shared) | Aliases, colors, behavior, hooks |

`~/.gitconfig` includes `git/gitconfig.shared` via a `[include]` stanza. `git config --global` writes to `~/.gitconfig` as expected. Global hooks live in [`git/hooks/`](../git/hooks/) and are symlinked to `~/.git-hooks/`.

### Secrets

Secrets are split between two files — use the right one:

| File | Purpose | Loaded by |
|------|---------|-----------|
| `~/.env.local` | Interactive shell secrets (API keys, cloud credentials) | Shell on every session start |
| `~/.config/task/taskfile.env` | AI automation tokens (`GH_TOKEN`, `AI_COMMAND`) | Task runner scripts only |

`~/.env.local` is created from [`zsh/.env.local.template`](../zsh/.env.local.template) on first install. Its auto-generated ENV section is updated on every sync (new vars only, never overwrites your values).

### AI Overrides

The `user/` directory (gitignored) lets you replace, extend, or disable AI config — rules, skills, agents, guidelines, and settings — without editing tracked files. Overrides are layered on top of base config during sync via `resolve_layers()`. See [User Overrides](user-overrides.md) for the full reference.

## Tool Registry

Each tooling directory owns a `registry.yml` describing the tools it provides. Registries are auto-discovered and used to generate tool documentation for AI sessions and the [Tools & Scripts](tools.md) catalog. See [Registries](registries.md) for the full schema, validation modes, and how to add entries.

## Execution Details

For step-by-step walkthroughs of install and sync, the comparison table, migrations, file operation strategies (symlink vs copy), state tracking, and generated files, see [Execution Flow](execution-flow.md).

## Key Conventions

These are the design principles that inform all workbench code:

- **Single source of truth** — every piece of data has one authoritative owner. Don't duplicate; reference.
- **Dynamic discovery** — glob patterns, not hardcoded lists. Adding a component or registry requires no edits elsewhere.
- **Idempotency** — all setup scripts, sync functions, and migrations are safe to re-run with no side effects.
- **Portability** — scripts use `#!/usr/bin/env bash`, require bash 4.3+, and auto-derive paths from their own location.

See the full list in the root [`CLAUDE.md`](../CLAUDE.md#conventions).
