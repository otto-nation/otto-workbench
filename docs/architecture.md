---
title: Architecture
description: How otto-workbench manages your developer environment — the concepts, layers, and conventions that hold it together.
---

# Architecture

How otto-workbench manages your developer environment — the concepts, layers, and conventions that hold it together.

## Overview

The workbench has two modes of operation:

```
otto-workbench install (first-time, interactive)
├── Bootstrap: installs Homebrew if missing
├── Core menu: bin, git, task, zsh — selectable, Enter = all
└── Optional menu: brew packages, docker, terminals, editors, ai, mise — selectable, Enter = all

otto-workbench sync (ongoing, non-interactive)
├── Runs pending migrations
├── Auto-discovers all components with steps.sh
└── Calls sync_<name>() for each — idempotent, no prompts
```

`otto-workbench install` bootstraps a new machine with interactive menus. `otto-workbench sync` re-applies everything silently after pulling updates. Both are safe to re-run.

## Component Model

Components are organized into two tiers based on when and how they run. Homebrew is installed as a bootstrap step before any components run — it's the only hard prerequisite.

**Core** components (`bin`, `git`, `task`, `zsh`) are always synced. Each defines a [`sync_<name>()`](components.md#sync_name-contract) function in its `steps.sh`. Adding a new core component requires only creating the directory with `steps.sh` — no edits to `bin/otto-workbench`.

**Optional** components (`brew`, `docker`, `terminals`, `editors`, `ai`, `mise`) appear in the install menu. Each has a [`setup.conf`](components.md#tier-2--optional-components) for metadata and a `setup.sh` for interactive install. Components with idempotent operations also define `steps.sh` for sync coverage.

Discovery is automatic — `otto-workbench install` globs `*/steps.sh` and skips any with a sibling `setup.conf` (those are optional). See the [Component Framework](components.md) reference for full contracts and examples.

## Configuration Layers

### Shell (ZSH)

ZSH configuration loads in layers from [`~/.config/zsh/config.d/`](../zsh/config.d/):

```
framework/  →  tools/  →  aliases/  →  prompt/
```

Order is significant — later layers can reference earlier ones. The [`loader.zsh`](../zsh/config.d/loader.zsh) script orchestrates this loading.

`~/.zshrc` is copied from [`zsh/.zshrc`](../zsh/.zshrc) on first install. It sets up oh-my-zsh (lazy-loaded), arch-aware Homebrew, and modular config loading.

One snippet in the `tools/` layer changes where a command runs rather than what is on `PATH`. [`tools/claude.zsh`](../zsh/config.d/tools/claude.zsh) wraps `claude` so a session started at a bare-repo container launches in a worktree instead. The container holds the bare `.git` and the worktrees as peers but no working tree of its own, so a session rooted there sees no `CLAUDE.md`, no `.claude/` rules, and no source. The wrapper asks [`resolve-worktree`](tools.md#resolve-worktree) which worktree the container stands in for, prints where it is going, and launches there in a subshell — your own shell stays where it was. An ordinary repo, a worktree, and a directory outside any repo are all launched untouched, and a container with no worktree to resolve is reported rather than guessed at. `command claude` bypasses the wrapper, which is how a deliberate container-rooted session is still possible.

### Git

Two-layer architecture:

| Layer | File | Owns |
|-------|------|------|
| Machine-specific (yours) | `~/.gitconfig` | Identity, GPG, credentials |
| Shared (workbench) | [`git/gitconfig.shared`](../git/gitconfig.shared) | Aliases, colors, behavior, hooks |

`~/.gitconfig` includes `git/gitconfig.shared` via a `[include]` stanza. `git config --global` writes to `~/.gitconfig` as expected. Global hooks live in [`git/hooks/`](../git/hooks/) and are symlinked to `~/.git-hooks/`.

Setting `core.hooksPath` globally makes git ignore every repository's own `.git/hooks/`, so [`git/hooks/pre-push`](../git/hooks/pre-push) runs the repo-local `pre-push` itself, handing it the same arguments and the same ref lines git handed the global one. A repo-local hook that refuses still refuses the push. When it does not, the refs are recorded to `push-intents.json` under the state root, and the next `pr` command asks the remote whether each one landed — so a push typed by hand is verified the way [`git/push.py`](ai-libraries.md#gitpushpy) verifies the ones the workbench issues. See [`pr/push_intent.py`](ai-libraries.md#prpush_intentpy).

The component also owns one marker-delimited `Host github.com` block in `~/.ssh/config`. That is a third ownership mode alongside the two above: not a whole file the workbench writes (`git/gitconfig.shared`) and not a whole file left to you (`~/.gitconfig`), but a fenced region inside a file that is otherwise yours and is never read. The block always carries the SSH keepalive that keeps a push from being dropped while pre-push runs; it carries the port 443 routing as well only when `github.ssh_over_443` is set, and flipping that key back takes those lines out again. See [Troubleshooting](troubleshooting.md#ssh-connect-to-host-githubcom-port-22-connection-refused) for when to reach for the routing.

### Secrets

Secrets are split between two files — use the right one:

| File | Purpose | Loaded by |
|------|---------|-----------|
| `~/.env.local` | Interactive shell secrets (API keys, cloud credentials) | Shell on every session start |
| `~/.config/task/taskfile.env` | AI automation tokens (`GH_TOKEN`, `AI_COMMAND`) | Task runner scripts only |

`~/.env.local` is created from [`zsh/.env.local.template`](../zsh/.env.local.template) on first install. The ENV marker section is auto-generated: every sync rewrites it wholesale from the registries. Your own values, which live below `ENV-END`, are never overwritten. If you set a value *inside* the markers — by uncommenting a catalogue line in place — sync rescues it before the rewrite, appends it to the end of the file, and prints a warning naming each variable it moved.

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
