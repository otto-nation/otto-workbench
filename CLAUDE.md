# otto-workbench

Dotfiles and developer environment manager. Installs shell config, brew packages, git settings, Claude AI tooling, and editor config via a component framework.

## Stack

bash, zsh, bats-core (tests), brew (packages), jq/yq (YAML/JSON), shellcheck (lint)

## Commands

```bash
bats tests/                    # run all tests
bats tests/<file>.bats         # run one test suite
shellcheck <file>.sh           # lint a script
bin/local/validate-registries        # check registry YAML schema + cross-validation
bin/local/validate-components        # check component tier contracts
bin/local/validate-migrations        # check migration file conventions
bin/local/generate-tool-context      # regenerate tools.generated.md from registries
git/bin/local/generate-git-rules     # regenerate git.generated.md from lib/conventions.sh
otto-workbench changelog       # show recent changes from conventional commits
```

## Architecture

### Components

Three layers during `install.sh`:
- **Preflight (mandatory):** `task` and `brew` — ensures tooling is present before anything else runs
- **Core (selectable, Enter = all):** `<name>/steps.sh` with `sync_<name>()`, no `setup.conf` — currently bin, git, zsh
- **Optional (selectable, Enter = all):** `<name>/setup.conf` + `setup.sh`, listed in `install.components` — brew packages, docker, terminals, editors, ai

`otto-workbench sync` always syncs all components (no selection). Sub-menus (terminals, editors, AI tools) also default to Enter = all.

### Registries

Each tool domain has a `registry.yml` describing its tools for AI context generation. Consumer-owned `*.env.yml` files declare env vars and auth, colocated with the code that reads them.

Required fields: `meta.section`, `meta.validation`, `meta.source`; per-tool: `name`, `description`, `when_to_use`.

Cross-validation modes: `brewfile` (tools must exist in Brewfile), `bindir` (must exist in directory), `zsh-comments` (must have comment in source), `none`.

### Shared libraries

- `lib/ui.sh` — colors, prompts, install helpers (`install_symlink`, `install_file`, `copy_dir`)
- `lib/migrations.sh` — migration framework (`run_component_migrations`, `run_all_migrations`)
- `lib/registries.sh` — `collect_registries`, `iter_registry_env`, `registry_passes_install_check`
- `lib/ai/core.sh` — commit/PR conventions, constants

### Zsh config layering

`zsh/config.d/` loads in order: `framework/` -> `tools/` -> `aliases/` -> `prompt/`. Order is significant.

## Conventions

- **Single source of truth** — every piece of data or config has exactly one authoritative owner. Display logic reads from the owner; it does not duplicate or re-derive the data. Runtime choices (e.g. Docker runtime) are recorded in state files (`~/.config/workbench/`); checks should read state, not infer from binary presence. When defaults must appear in multiple formats (YAML + shell), add a cross-validation test. Registry `*.registry.yml` files own tool documentation (`tools[]`). Registry `*.env.yml` files own env var declarations (`env[]`, `auth`), colocated with the consumer code that reads them. Env vars set programmatically at runtime (e.g. DOCKER_HOST) are NOT declared in registries.
- Dynamic discovery over hardcoded config — glob patterns, not individual entries. Test: "does adding a new item require editing this file?" If yes, use a convention-based alternative.
- Adding a brew tool = add to Brewfile + registry.yml. No other config edits needed. Env vars go in a `.env.yml` next to the consumer, not in the brew registry.
- Adding a migration = create `<component>/migrations/YYYYMMDD-slug.sh` with a `migration_YYYYMMDD_slug()` function. No registry edits needed.
- Generated files (`tools.generated.md`, `git.generated.md`) are never edited directly — edit the source and regenerate.
- Config files in `zsh/config.d/` use `# duplicate-check: <pattern>` headers to prevent overlapping concerns.
- All scripts and git hooks use `#!/usr/bin/env bash` (not `#!/bin/bash`) to pick up Homebrew's modern bash on macOS. Bash 4.3+ is required. Never invoke scripts with `bash script.sh` — run them directly (`./script.sh` or `"$path/script.sh"`) so their shebang is honored.
- All scripts source `lib/ui.sh` via `_SELF` readlink pattern for portability.
- Scripts use `set -e`.
- **Idempotency is required** — all setup scripts, sync functions, and migrations must be safe to re-run. Guard installs with presence checks, use `install_symlink` (not raw `ln`), and ensure repeated execution produces the same result with no side effects.
- **Return values via `local -n` (nameref), never `printf -v`** — `printf -v "$var"` silently writes to a same-named `local` in the current scope instead of the caller's variable. Use `local -n __out=$1` and assign `__out="value"`. The `__` prefix convention prevents collisions with caller variables.
- Migrations are state-tracked in `~/.config/workbench/migrations.applied` and auto-pruned when removed.
