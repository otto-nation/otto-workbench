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
bin/generate-tool-context      # regenerate tools.generated.md from registries
```

## Architecture

### Components

Two tiers:
- **Tier 1 (core):** `<name>/steps.sh` with `sync_<name>()` — always installed (bin, git, task, zsh)
- **Tier 2 (optional):** `<name>/setup.conf` + `setup.sh`, listed in `install.components` — user-selectable (brew, docker, terminals, editors, ai)

### Registries

Each tool domain has a `registry.yml` describing its tools for AI context generation.

Required fields: `meta.section`, `meta.validation`, `meta.source`; per-tool: `name`, `description`, `when_to_use`.

Cross-validation modes: `brewfile` (tools must exist in Brewfile), `bindir` (must exist in directory), `zsh-comments` (must have comment in source), `none`.

### Shared libraries

- `lib/ui.sh` — colors, prompts, install helpers (`install_symlink`, `install_file`, `copy_dir`)
- `lib/registries.sh` — `collect_registries`, `iter_registry_env`, `registry_passes_install_check`
- `lib/ai/core.sh` — commit/PR conventions, constants

### Zsh config layering

`zsh/config.d/` loads in order: `framework/` -> `tools/` -> `aliases/` -> `prompt/`. Order is significant.

## Conventions

- Dynamic discovery over hardcoded config — glob patterns, not individual entries. Test: "does adding a new item require editing this file?" If yes, use a convention-based alternative.
- Adding a brew tool = add to Brewfile + registry.yml. No other config edits needed.
- Generated files (`tools.generated.md`, `git.generated.md`) are never edited directly — edit the source and regenerate.
- Config files in `zsh/config.d/` use `# duplicate-check: <pattern>` headers to prevent overlapping concerns.
- All scripts source `lib/ui.sh` via `_SELF` readlink pattern for portability.
- Scripts use `set -e`; all sync functions are idempotent and safe to re-run.
