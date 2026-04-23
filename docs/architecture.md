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

## Tool Registry

Each tooling directory owns a [`registry.yml`](../brew/registry.yml) describing the tools it provides — name, description, when to use, usage examples, and docs URL. These are the single source of truth for tool documentation.

Registries are auto-discovered by [`lib/registries.sh`](../lib/registries.sh) via glob patterns:
- `*/registry.yml` — component registries
- `**/*.registry.yml` — brew stack registries
- `*.env.yml` — consumer-owned env/auth declarations

The generator [`bin/generate-tool-context`](../bin/generate-tool-context) combines all registries into [`tools.generated.md`](../ai/guidelines/rules/tools.generated.md) for AI sessions, and splices tables into the docs.

**Registry vs env files**: `registry.yml` owns tool documentation (`tools[]`). `*.env.yml` owns env var and auth declarations (`env[]`, `auth`), colocated with the code that reads them. Env vars set programmatically at runtime (e.g., `DOCKER_HOST`) are not declared in registries.

## Install vs Sync

| Aspect | `install.sh` | `otto-workbench sync` |
|--------|-------------|----------------------|
| When to use | First-time setup, adding optional components | After pulling workbench updates |
| Interactive | Yes — menus, prompts | No — warns and skips conflicts |
| Scope | Preflight + selected components | All components with `steps.sh` |
| Brew packages | Installs from Brewfile | Skipped |
| Docker runtime | Prompts for Colima/OrbStack | Re-symlinks existing socket |
| Templates | Creates configs from templates | Never overwrites editable configs |
| Migrations | Runs pending | Runs pending |
| Real file conflicts | Prompts for overwrite/backup | Warns and skips |

## Migrations

Migrations handle breaking changes — renamed configs, deprecated symlinks, updated defaults. Each is a shell function that runs once and is state-tracked.

**Naming**: `<component>/migrations/YYYYMMDD-slug.sh` defines `migration_YYYYMMDD_slug()`.

**Lifecycle**:
1. Create the migration file following the naming convention
2. [`run_all_migrations()`](../lib/migrations.sh) auto-discovers it via glob
3. On first run: function executes, filename recorded in `~/.config/workbench/migrations.applied`
4. On subsequent runs: skipped (already recorded)
5. If the migration file is deleted: stale entry auto-pruned from state

Migrations must be idempotent — a failed migration is not recorded, so it retries on next sync.

## Generated Files

These files are derived from source data and must never be edited directly. Edit the source and regenerate.

| File | Generator | Source | CI Enforcement |
|------|-----------|--------|----------------|
| [`tools.generated.md`](../ai/guidelines/rules/tools.generated.md) | [`generate-tool-context`](../bin/generate-tool-context) | `*/registry.yml` | Freshness diff |
| [`git.generated.md`](../ai/guidelines/rules/git.generated.md) | [`generate-git-rules`](../git/bin/generate-git-rules) | [`lib/conventions.sh`](../lib/conventions.sh) | Freshness diff |
| `docs/tools.md` (tables) | [`generate-tool-context`](../bin/generate-tool-context) | Registries | Freshness diff |
| `docs/ai-automation.md` (tables) | [`generate-tool-context`](../bin/generate-tool-context) | Skills, agents, Taskfile | Freshness diff |
| `docs/components.md` (lists) | [`generate-tool-context`](../bin/generate-tool-context) | Component discovery | Freshness diff |
| `.env.local.template` (ENV section) | [`generate-tool-context`](../bin/generate-tool-context) | `*.env.yml` | — |
| `.claude/anatomy.md` | [`generate-anatomy.sh`](../ai/claude/skills/anatomy/generate-anatomy.sh) | `git ls-files` | — |

Freshness is enforced twice: the pre-push hook runs generators and blocks if output changed, and CI runs the same check on every PR.

## Key Conventions

These are the design principles that inform all workbench code:

- **Single source of truth** — every piece of data has one authoritative owner. Don't duplicate; reference.
- **Dynamic discovery** — glob patterns, not hardcoded lists. Adding a component or registry requires no edits elsewhere.
- **Idempotency** — all setup scripts, sync functions, and migrations are safe to re-run with no side effects.
- **Portability** — scripts use `#!/usr/bin/env bash`, require bash 4.3+, and auto-derive paths from their own location.

See the full list in the root [`CLAUDE.md`](../CLAUDE.md#conventions).
