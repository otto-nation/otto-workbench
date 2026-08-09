# Libraries

All shared code lives in `lib/`. Most modules are loaded through the `ui.sh` facade; some are sourced directly by specific consumers.

## Loading

Scripts source `lib/ui.sh` via `git rev-parse --show-toplevel` — depth-independent:

```bash
_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
. "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/ui.sh"
```

`ui.sh` is a facade — it sources `output.sh`, `prompts.sh`, `files.sh`, `constants.sh`, `setup.sh`, `state.sh`, and `migrations.sh`. Modules not in the facade (`registries.sh`, `summary.sh`, `conventions.sh`, `lib/ai/*`) are sourced directly by their consumers. `roots.sh` reaches the facade through `constants.sh`, and is also sourced on its own by consumers that need the roots without the rest of the framework.

## Core Modules

### constants.sh

Path and filename constants auto-derived from the workbench root. Single source of truth for `WORKBENCH_DIR`, `LOCAL_BIN_DIR`, `ZSH_CONFIG_DIR`, `CLAUDE_DIR`, `MIGRATIONS_STATE_FILE`, `INSTALLED_STATE_FILE`, `MAINTENANCE_LAST_FILE`, and all other shared paths.

No functions — constants only. Sources `roots.sh`. Loaded via `ui.sh`.

### roots.sh

The three user-level roots the workbench writes to, each resolved through the same chain:

```
WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default
```

| Constant | Holds | XDG rung | Default |
|----------|-------|----------|---------|
| `WORKBENCH_CONFIG_DIR` | Hand-authored settings: `install.yml`, `overrides/`, `mcp-tools.json` | `XDG_CONFIG_HOME` | `~/.config/workbench` |
| `WORKBENCH_STATE_DIR` | Generated machine-local data: `reviews/`, `logs/`, `usage/`, applied migrations | *(none yet)* | `~/.config/workbench` |
| `WORKBENCH_CACHE_DIR` | Recomputable data, safe to delete at any time | `XDG_CACHE_HOME` | `~/.cache/workbench` |

The state root has no `XDG_STATE_HOME` rung yet: honouring it would relocate the existing reviews and logs with nothing to carry them. The rung and the `~/.local/state/workbench` default arrive together with that migration.

Its own module rather than part of `constants.sh` because two other consumers need the roots without the rest: the `otto-ai-tools` tarball ships `roots.sh` alongside its own `ui.sh` facade, and `registries.sh` sources it directly when a caller has not loaded `constants.sh`.

Two definitions outside `lib/` express the same chain, and `tests/workbench_roots.bats` cross-validates all three:

- [`ai/lib/workbench_paths.py`](../ai/lib/workbench_paths.py) — the Python owner. Exposes `config_dir()`, `state_dir()`, `cache_dir()`, and `logs_dir(tool=None)`, resolved per call rather than frozen at import.
- [`zsh/config.d/aliases/docker.zsh`](../zsh/config.d/aliases/docker.zsh) — spelled inline, because `WORKBENCH_DIR` is unknown at shell startup and sourcing would add a file read to every shell.

### output.sh

Output helpers: colors, logging, portable sed.

| Function | Purpose |
|----------|---------|
| `info` | Blue info message with arrow |
| `success` | Green success message with checkmark |
| `warn` | Yellow warning; logs to `WORKBENCH_INSTALL_LOG` |
| `err` | Red error to stderr; logs to `WORKBENCH_INSTALL_LOG` |
| `title` | Bold blue section header |
| `skip` | Dim skip notice |
| `sed_i` | Portable in-place sed (BSD/GNU) |

Loaded via `ui.sh`.

### prompts.sh

User interaction: confirmations, menus, config reading.

| Function | Purpose |
|----------|---------|
| `confirm` | [Y/n] prompt, returns 0 for yes |
| `confirm_n` | [y/N] prompt, returns 0 for yes |
| `confirm_step` | [Y/n/a] prompt, writes result to nameref |
| `prompt_overwrite` | Overwrite/backup/skip menu for existing files |
| `select_menu` | Numbered multi-select or single-select menu |
| `select_subdirs` | Discover directories with setup.sh, present menu |
| `conf_get` | Extract key=value from config files |

Loaded via `ui.sh`.

### files.sh

File operations with idempotency: symlinks, copies, directory operations, layer merging.

| Function | Purpose |
|----------|---------|
| `install_symlink` | Create/update symlink; prompt before overwriting real files |
| `install_file` | Copy if content differs; remove stale symlinks |
| `copy_dir` | Copy files matching glob, with optional pruning |
| `symlink_dir` | Symlink files matching glob, with pruning and copy replacement |
| `list_shell_scripts` | List files whose first line is a shell shebang (used by `task lint` and pre-push) |
| `resolve_layers` | Merge base + user directories by basename (see [User Overrides](user-overrides.md)) |
| `is_disabled` | Check for `.disabled` sentinel file |
| `install_hook_dispatcher` | Write a runtime-resolving git hook dispatcher |
| `apply_config_patch` | Replace old with new in file, idempotent |

Loaded via `ui.sh`.

### setup.sh

Install workflow helpers: step registration, requirement checks, cask installs.

| Function | Purpose |
|----------|---------|
| `register_step` | Append step to global `STEPS` array |
| `run_steps` | Run each registered step with [Y/n/a] confirmation |
| `require_command` | Warn and return 1 if command not in PATH |
| `install_cask` | Install via Homebrew cask if not present |

Loaded via `ui.sh`.

### state.sh

Component installation state tracking.

| Function | Purpose |
|----------|---------|
| `state_record` | Mark a component as installed (idempotent) |
| `state_is_installed` | Check if component is installed |
| `state_remove` | Remove component from state |
| `state_file_exists` | Check if state file exists |
| `state_list` | Print all installed components |
| `state_prune_orphans` | Remove entries for deleted components |
| `state_detect_installed` | Heuristic-based detection for bootstrapping |

State file: `$INSTALLED_STATE_FILE` — `installed.components` under the [state root](#rootssh). Loaded via `ui.sh`.

### migrations.sh

Migration framework with state tracking.

| Function | Purpose |
|----------|---------|
| `run_component_migrations` | Run migrations in a single component directory |
| `run_all_migrations` | Discover and run migrations across all components, prune stale state |

State file: `$MIGRATIONS_STATE_FILE` — `migrations.applied` under the [state root](#rootssh). Loaded via `ui.sh`. See [Execution Flow — Migrations](execution-flow.md#migrations).

### components.sh

Component discovery via convention-based glob patterns.

| Function | Purpose |
|----------|---------|
| `discover_step_files` | Find all `steps.sh` files (two-level glob) |
| `discover_migration_dirs` | Find all `migrations/` directories |

Sourced by `migrations.sh` and `install.sh`.

## Registry & Config Modules

### registries.sh

Registry discovery, install-check gating, and env/auth iteration.

| Function | Purpose |
|----------|---------|
| `is_installed` | Check if command is in PATH |
| `collect_registries` | Discover all registry files (deduplicated) |
| `registry_passes_install_check` | Check if a registry should be rendered |
| `iter_registry_env` | Call callback for each env var in a registry |
| `iter_registry_auth` | Call callback for each auth block |

Sourced directly by consumers (`generate-tool-context`, `summary.sh`). Not in the `ui.sh` facade.

### conventions.sh

Git convention constants — single source of truth for commit and PR formatting.

Constants: `COMMIT_TYPES`, `COMMIT_HEADER_MAX_LEN`, `COMMIT_BODY_MAX_LEN`.

No functions. Sourced directly by `lib/ai/core.sh` and git generation scripts.

### summary.sh

Post-run summary output for install and sync.

| Function | Purpose |
|----------|---------|
| `print_workbench_summary` | Print managed files, editable configs, env setup, quick reference |
| `print_warnings_summary` | Replay collected warnings/errors |
| `run_component_summaries` | Auto-discover and call `print_<name>_summary()` |

Sourced directly by `install.sh` and `bin/otto-workbench`.

## AI Modules (`lib/ai/`)

These modules power the AI-driven git automation (commits, PRs, reviews). All are sourced directly by Taskfile tasks — none go through the `ui.sh` facade.

### ai/core.sh

Foundation module: AI command loading, GitHub token resolution with per-org routing, response handling.

| Function | Purpose |
|----------|---------|
| `load_ai_command` | Find and validate AI command; set `AI_COMMAND` |
| `load_gh_token` | Resolve `GH_TOKEN` with per-org routing (4-tier priority) |
| `load_pr_context` | Load AI command + GH token + verify GitHub auth |
| `run_ai` | Execute AI with prompt; set `AI_RESPONSE` |

### ai/commit.sh

Commit message generation with validation and automatic retry on length violations.

| Function | Purpose |
|----------|---------|
| `generate_commit_msg` | Generate commit message from diff; retry if header too long |
| `validate_commit_msg` | Validate via commitlint or fallback checks |
| `build_commit_rules` | Build rules from commitlint config or conventions |

### ai/pr.sh

PR content generation: title, description, issue linking, template loading.

| Function | Purpose |
|----------|---------|
| `load_pr` | Parse flags, load context, set branch info |
| `push_branch` | Push branch with divergence handling |
| `generate_pr_content` | Generate title and description from commits |

### ai/review.sh

Code review generation for branch changes and existing PRs.

| Function | Purpose |
|----------|---------|
| `generate_diff_review` | Review committed, staged, and unstaged changes |
| `generate_pr_review` | Review an existing PR by number |

### ai/compact_diff.sh

Diff compaction: splits diffs into per-file chunks and greedily includes as many as fit within a character budget.

| Function | Purpose |
|----------|---------|
| `_compact_diff` | Split diff, include smallest files first within budget |

### ai/prompts.sh

Prompt templates for all AI automation — pure text generation, no side effects.

| Function | Purpose |
|----------|---------|
| `prompt_commit` | Commit message prompt |
| `prompt_pr_single_commit` | PR description for single-commit branches |
| `prompt_pr_multi_commit` | PR title + description for multi-commit branches |
| `prompt_diff_review` | Review prompt for local changes |
| `prompt_pr_review` | Review prompt for existing PRs |
