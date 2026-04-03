# otto-workbench

Dotfiles and developer environment manager. Installs shell config, brew packages, git settings, Claude AI tooling, and editor config via a component framework.

## Stack

bash, zsh, bats-core (tests), brew (packages), jq/yq (YAML/JSON), shellcheck (lint)

## Commands

```bash
bats tests/                    # run all tests
bats tests/<file>.bats         # run one test suite
shellcheck <file>.sh           # lint a script
bin/validate-registries        # check registry YAML schema + cross-validation
bin/validate-components        # check component tier contracts
bin/validate-migrations        # check migration file conventions
bin/generate-tool-context      # regenerate tools.generated.md from registries
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

Each tool domain has a `registry.yml` describing its tools for AI context generation.

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

- Dynamic discovery over hardcoded config — glob patterns, not individual entries. Test: "does adding a new item require editing this file?" If yes, use a convention-based alternative.
- Adding a brew tool = add to Brewfile + registry.yml. No other config edits needed.
- Adding a migration = create `<component>/migrations/YYYYMMDD-slug.sh` with a `migration_YYYYMMDD_slug()` function. No registry edits needed.
- Generated files (`tools.generated.md`, `git.generated.md`) are never edited directly — edit the source and regenerate.
- Config files in `zsh/config.d/` use `# duplicate-check: <pattern>` headers to prevent overlapping concerns.
- All scripts use `#!/usr/bin/env bash` (not `#!/bin/bash`) to pick up Homebrew's modern bash on macOS. Bash 4.3+ is required.
- All scripts source `lib/ui.sh` via `_SELF` readlink pattern for portability.
- Scripts use `set -e`; all sync functions are idempotent and safe to re-run.
- Migrations are idempotent, state-tracked in `~/.config/workbench/migrations.applied`, and auto-pruned when removed.
