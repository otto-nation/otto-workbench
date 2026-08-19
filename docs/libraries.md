---
title: Libraries
description: All shared code in lib/ — the modules loaded through the ui.sh facade and those sourced directly.
---

# Libraries

All shared code lives in `lib/`. Most modules are loaded through the `ui.sh` facade; some are sourced directly by specific consumers.

## Loading

Scripts source `lib/ui.sh` via `git rev-parse --show-toplevel` — depth-independent:

```bash
_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
. "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/ui.sh"
```

`ui.sh` is a facade — it sources `output.sh`, `prompts.sh`, `files.sh`, `constants.sh`, `setup.sh`, `state.sh`, `projects.sh`, and `migrations.sh`. Modules not in the facade (`registries.sh`, `summary.sh`, `conventions.sh`, `lib/ai/*`) are sourced directly by their consumers. `roots.sh` reaches the facade through `constants.sh`, and is also sourced on its own by consumers that need the roots without the rest of the framework.

## Core Modules

### constants.sh

Path and filename constants auto-derived from the workbench root. Single source of truth for `WORKBENCH_DIR`, `LOCAL_BIN_DIR`, `ZSH_CONFIG_DIR`, `CLAUDE_DIR`, `MIGRATIONS_STATE_FILE`, `INSTALL_YML_FILE`, `INSTALLED_STATE_FILE`, `MAINTENANCE_LAST_FILE`, and all other shared paths.

No functions — constants only. Sources `roots.sh`. Loaded via `ui.sh`.

### roots.sh

The three user-level roots the workbench writes to, each resolved through the same chain:

```
WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default
```

<!-- LIB-ROOTS-START -->
| Constant | Holds | XDG rung | Default |
|----------|-------|----------|---------|
| `WORKBENCH_CONFIG_DIR` | Hand-authored settings: config.yml, overrides/, mcp-tools.json | `XDG_CONFIG_HOME` | `~/.config/workbench` |
| `WORKBENCH_STATE_DIR` | Generated, machine-local data: reviews/, trail/, usage/, install.yml, migrations.applied | `XDG_STATE_HOME` | `~/.local/state/workbench` |
| `WORKBENCH_CACHE_DIR` | Recomputable data, safe to delete at any time: vertex-quota/ | `XDG_CACHE_HOME` | `~/.cache/workbench` |
<!-- LIB-ROOTS-END -->

`install.yml` sits under state despite the name: `lib/state.sh` owns every write to it, and it is what the old `installed.components` file migrated into. It records what a sync found or installed, not anything a user chose to type.

Machines set up before the split keep everything in `~/.config/workbench`. `adopt_legacy_workbench_root` in [`lib/migrations.sh`](../lib/migrations.sh) carries that directory across on the next sync, entry by entry, and runs ahead of the migration framework because `migrations.applied` is one of the files it moves. Nothing falls back to the old path once it has run — the adoption is the entire compatibility story.

Routing is by name and spelled out in three branches, not two plus a fallthrough: `_LEGACY_CONFIG_ENTRIES` goes to the config root, `_LEGACY_UNCLAIMED_ENTRIES` is skipped and left in the legacy root, and everything else goes to the state root. That last default is deliberate — the #624 inventory found state files no manifest written in advance had listed — but it must not also absorb a name no root holds any more: adoption runs before any migration reads its bookkeeping, so an entry a completed migration deleted on purpose (`logs/`, removed by #730) would come back with nothing left to take it out again. Adding one to the unclaimed list is the fix whenever a migration prunes a top-level name from a root.

A file the new root already holds is normally kept on both sides and warned about rather than clobbered. The exception is the append-only ledgers — `trail.jsonl` and `usage/*.jsonl` — which are concatenated instead: their only writers open them in append mode, and `otto-log` sorts every record by `ts` after loading, so one history split across two files reassembles either way. The rule is keyed on those names, not on the `.jsonl` extension, because the review artifacts (`session.jsonl`, `post.jsonl`, `*.holistic.jsonl`) are whole-file writes whose convention is prior-content-first.

Its own module rather than part of `constants.sh` because two other consumers need the roots without the rest: the `otto-ai-tools` tarball ships `roots.sh` alongside its own `ui.sh` facade, and `registries.sh` sources it directly when a caller has not loaded `constants.sh`.

Two definitions outside `lib/` express the same chain, and `tests/workbench_roots.bats` cross-validates all three:

- [`ai/lib/workbench_paths.py`](../ai/lib/workbench_paths.py) — the Python owner. Exposes `config_dir()`, `state_dir()`, `cache_dir(consumer=None)`, `trail_dir()`, and `reviews_dir()`, resolved per call rather than frozen at import. `cache_dir` takes a consumer name and rejects anything but a bare directory name — a path would land outside the tree the root's owner globs over. `trail_dir()` takes nothing: every trail writer shares one root, `<state>/trail/`, with one file per month. `reviews_dir()` is the sole owner of the reviews join, so the review system and the tool that reads its output — `retro-scan` for the findings — cannot disagree about where a review is.
- [`zsh/config.d/aliases/docker.zsh`](../zsh/config.d/aliases/docker.zsh) — spelled inline, because `WORKBENCH_DIR` is unknown at shell startup and sourcing would add a file read to every shell.

#### Trails

Every AI script appends to `trail_dir()`, in a file named for the emitting
event's UTC month. The layout mirrors `ai_usage.LEDGER_DIR`: rotation falls out
of the filename, `--since` drops whole files without opening them, and nothing
needs a pruning job. `_emit` takes an `fcntl.flock` on the open handle inside
the module's thread lock — one file now takes appends from concurrent
processes (`pr` and the script it spawned), and a short write (NFS, a signal,
an rlimit boundary) can split a record across two `write()` calls, letting the
other process's append land in the gap.

The `20260814-unify-trail-root` migration carried the pre-cutover review trails
into `trail/legacy.jsonl`. `otto-log` always reads a file whose stem does not
name a month, which is what keeps it visible under `--since`.

### output.sh

Output helpers: colors, logging, portable sed.

<!-- LIB-FUNCTIONS:output.sh-START -->
| Function | Purpose |
|----------|---------|
| `sed_i EXPRESSION FILE` | portable in-place sed (macOS and Linux). |
| `info MESSAGE` | blue info message with an arrow. |
| `success MESSAGE` | green success message with a checkmark. |
| `warn MESSAGE` | yellow warning; also logged to WORKBENCH_INSTALL_LOG. |
| `err MESSAGE` | red error to stderr; also logged to WORKBENCH_INSTALL_LOG. |
| `title TEXT` | bold blue section header. |
| `skip [label]` | print a skip line with optional label |
| `print_version SCRIPT_NAME [COMPONENT_KEY]` | print tool and workbench version. |
| `sync_header LABEL` | section header for sync steps. Suppressed during sync. |
| `summary_section LABEL` | cyan section header for summaries. Suppressed during sync. |
| `summary_ok MESSAGE` | indented success line. Suppressed during sync. |
| `summary_warn MESSAGE` | indented warning; logged instead of printed during sync. |
| `summary_err MESSAGE` | indented error line. Printed even during sync. |
| `summary_info MESSAGE` | indented dim detail line. Suppressed during sync. |
<!-- LIB-FUNCTIONS:output.sh-END -->

Loaded via `ui.sh`.

### portable.sh

File-metadata readers that work on both userlands. GNU and BSD `stat` spell the same fields with different flags, and a hand-rolled fallback prints a filesystem report before failing on GNU — so nothing outside this module calls `stat` with a format flag, and `bin/local/validate-stat-portability` enforces that.

<!-- LIB-FUNCTIONS:portable.sh-START -->
| Function | Purpose |
|----------|---------|
| `file_mtime PATH` | modification time in epoch seconds. |
| `file_birth PATH` | birth (creation) time in epoch seconds. Prints 0 on filesystems that do not record one; callers must treat 0 as "unknown". |
| `file_mode PATH` | permission bits as an octal string, e.g. 644. |
<!-- LIB-FUNCTIONS:portable.sh-END -->

Sourced directly, or through `ui.sh` for scripts that already load the facade.

### prompts.sh

User interaction: confirmations, menus, config reading.

<!-- LIB-FUNCTIONS:prompts.sh-START -->
| Function | Purpose |
|----------|---------|
| `confirm "msg"` | [Y/n]; returns 0 for yes (default), 1 for no |
| `confirm_n "msg"` | [y/N]; returns 0 for yes, 1 for no (default) |
| `confirm_step RESULT_VAR MSG` | [Y/n/a]; writes "yes", "no", or "all" to RESULT_VAR. |
| `prompt_overwrite FILE` | warns that FILE already exists and presents a single combined prompt. |
| `select_menu RESULT_VAR COUNT [--default all\|skip\|require] [--single]` | Displays a numbered selection prompt and writes the result back to RESULT_VAR. |
| `select_subdirs RESULT_VAR PARENT_DIR PROMPT [SELECT_MENU_OPTS...]` | Discovers subdirectories in PARENT_DIR that contain setup.sh, presents a numbered menu with PROMPT, and writes space-separated selected names to RESULT_VAR. |
| `conf_get FILE KEY` | reads a key = value line from a KEY = VALUE config file. |
<!-- LIB-FUNCTIONS:prompts.sh-END -->

Loaded via `ui.sh`.

### files.sh

File operations with idempotency: symlinks, copies, directory operations, layer merging.

<!-- LIB-FUNCTIONS:files.sh-START -->
| Function | Purpose |
|----------|---------|
| `install_symlink SOURCE TARGET [LABEL] [--no-prompt]` | Creates or updates a symlink at TARGET pointing to SOURCE. |
| `install_file SOURCE TARGET [LABEL]` | Copies SOURCE to TARGET if content differs. Removes stale symlinks at TARGET. |
| `copy_dir SRC DST [GLOB] [--strip-ext] [--prune]` | Copies all files matching GLOB in SRC into DST, preserving filenames. |
| `symlink_dir SRC DST [GLOB] [--strip-ext] [--prune] [--replace-copies]` | Symlinks all items matching GLOB in SRC into DST, preserving filenames. |
| `sync_component_bin COMPONENT_DIR` | symlinks extensionless scripts from COMPONENT_DIR/bin/ into LOCAL_BIN_DIR. |
| `list_shell_scripts ROOT` | prints every file under ROOT whose *first* line is a shell or bats shebang, one per line, sorted. |
| `resolve_layers BASE_DIR USER_DIR GLOB RESULT_NAMEREF` | Merges two directory layers into an associative array: basename -> source_path. |
| `is_disabled USER_DIR NAME` | returns 0 if a .disabled sentinel exists. |
| `install_hook_dispatcher SOURCE_RELPATH TARGET [LABEL]` | Writes a thin dispatcher script that execs the hook from the current worktree. |
| `apply_config_patch FILE OLD NEW` | Replaces OLD with NEW in FILE if OLD is present. Idempotent — no-op if already patched or if FILE does not exist. |
<!-- LIB-FUNCTIONS:files.sh-END -->

Loaded via `ui.sh`.

### setup.sh

Install workflow helpers: step registration, requirement checks, cask installs.

<!-- LIB-FUNCTIONS:setup.sh-START -->
| Function | Purpose |
|----------|---------|
| `register_step NAME FN` | appends a step to the STEPS array. |
| `run_steps` | prints all registered steps upfront, then runs each with [Y/n/a] confirmation. |
| `require_command NAME [MESSAGE]` | returns 1 with a warning if NAME is not in PATH. |
| `install_cask CMD CASK LABEL MANUAL_URL` | Installs a tool via Homebrew cask if CMD is not already in PATH. |
| `run_migrations DIR` | DEPRECATED: Use run_component_migrations from lib/migrations.sh instead. |
<!-- LIB-FUNCTIONS:setup.sh-END -->

Loaded via `ui.sh`.

### state.sh

Component installation state tracking.

<!-- LIB-FUNCTIONS:state.sh-START -->
| Function | Purpose |
|----------|---------|
| `state_record ENTRY` | records a component or sub-tool in install.yml. Idempotent. |
| `state_is_installed ENTRY` | returns 0 if entry is recorded in install.yml. |
| `state_remove ENTRY` | removes a component or sub-tool from install.yml. |
| `state_file_exists` | returns 0 if install.yml (or legacy state file) exists. |
| `state_list` | prints all installed entries, one per line (flat format for compat). |
| `state_prune_orphans` | removes YAML entries that have no matching steps.sh. |
| `state_detect_installed` | detects currently installed components and records them. |
| `state_set KEY VALUE` | sets an arbitrary YAML path under components. |
| `state_clear_list KEY` | resets a YAML list to empty sequence. |
| `state_append_list KEY VALUE` | appends VALUE to a YAML list (idempotent). |
| `state_get KEY` | reads a YAML value. Returns empty string for missing/null keys. |
| `state_get_list KEY` | reads a YAML list, one item per line. |
| `state_load_selections STATE_KEY SCRIPT_DIR RESULT_ARRAY [AVAILABLE_ARRAY]` | Loads saved selections from YAML, validates each against SCRIPT_DIR. |
<!-- LIB-FUNCTIONS:state.sh-END -->

State file: `$INSTALL_YML_FILE` — `install.yml` under the [state root](#rootssh). The flat `installed.components` it replaced survives only as the second half of `state_file_exists`. Loaded via `ui.sh`.

### projects.sh

The repos on this machine that use otto-workbench.

<!-- LIB-FUNCTIONS:projects.sh-START -->
| Function | Purpose |
|----------|---------|
| `project_register DIR` | record DIR as a repo that uses the workbench. |
| `project_registered` | print every registered repo that still exists, one per line. |
| `project_forget DIR` | drop DIR's entry. Returns 1 when it had none. |
| `project_prune` | drop entries whose directory is gone, and repeats. Prints how many went. |
| `seed_project_registry` | backfill the repos that predate the registry, once. |
<!-- LIB-FUNCTIONS:projects.sh-END -->

State file: `$PROJECTS_REGISTRY_FILE` — `projects.registry` under the [state root](#rootssh), one absolute path per line with `#` comment lines. Text rather than YAML for the reason `migrations.applied` is: every write is an append and every read is a scan, and YAML would pay a `yq` fork on each of them. Loaded via `ui.sh`.

Membership means a workbench command actually ran in a repo. Nothing scans for candidates — the two consumers that used to, the machine profile generator and the project-scoped migrations, each carried their own guessed-at list of git roots and a depth limit, so a repo cloned anywhere else was invisible and the migration recorded itself applied all the same (#780). The registrations are:

| Caller | Where the root comes from |
|--------|---------------------------|
| Claude's SessionStart hook (`reuse-session-start`) | already resolved for the ceiling scan |
| `pr` | `ctx.worktree_root` |
| `otto-workbench ai init` | the repo being scaffolded |
| `otto-workbench projects add [DIR]` | by hand, for a repo that uses neither |

`project_register` does no discovery of its own and forks nothing: every caller has a resolved work-tree root in hand. A path under `$TMPDIR`, `/tmp`, `/var/folders`, or the workbench's own state or cache root is refused — `bats` builds throwaway repos there and runs validators and pre-commit hooks inside them. The `/private` twins of the temp roots are listed too, because callers hand over a path `git rev-parse --show-toplevel` already resolved and those two are symlinks into `/private` on macOS. A bare repo's container is refused as well, holding worktrees rather than being one. `PROJECTS_EXCLUDED_PREFIXES` is assignable so a test can register the repos it builds, which are all temporary.

Reads drop entries whose directory is gone, which is what saves the registry from needing a pruning job; `otto-workbench projects prune` makes the drop permanent. Repeats are dropped on read for a related reason: registration is an append guarded by a membership check rather than a lock, so two workbench commands starting in one repo at the same moment can each read "absent" and each append. Absorbing that where it is read costs nothing; a lock would tax every hook to prevent a duplicate line.

`otto-workbench projects forget DIR` canonicalises `DIR` before matching — entries are stored as `git rev-parse --show-toplevel` returned them, and the comparison is an exact string, so a relative path, one holding `..`, one reaching through a symlink, or one naming a subdirectory of the repo all have to arrive in that form or a valid request reads as "not in the registry". A directory that is already gone can only be normalised lexically, which is the right answer for it: whatever entry it matches was written while it still existed.

[`ai/lib/workbench_projects.py`](../ai/lib/workbench_projects.py) is the Python half — the SessionStart hook and `pr` register through it, against the same file in the same shape. It raises nothing: registration is a side effect of a command run for some other reason, and a hook that died on an unwritable state file would cost a session for a bookkeeping entry. The filename is declared once in [`constants.sh`](#constantssh) as `PROJECTS_REGISTRY_NAME` and once in `workbench_paths.py`; `tests/workbench_roots.bats` fails when the two drift, and `tests/projects.bats` cross-validates the halves against one file.

`seed_project_registry` backfills the repos that predate the registry, once per machine, from the `.projects` map in `~/.claude.json` — an observation Claude Code wrote, not another guess at where repos live. Each key is a session cwd, routinely a bare repo's container, so every one is resolved through `git rev-parse --show-toplevel` first. A `# backfilled from <path>` line inside the file records that it ran: the Python half creates the file the first time `pr` registers anything, so a backfill keyed on the file's existence would be skipped forever on a machine that used a tool before it next synced. Without `jq` it writes no marker and returns — no candidates for want of a reader is indistinguishable from a machine that has none, and recording the marker on that reading would retire the backfill before it ever ran. It is called from `run_all_migrations` ahead of the framework rather than written as a migration, for the reason adoption is — see [Execution Flow — Migrations](execution-flow.md#migrations).

### config.sh

Hand-authored settings, read from YAML — one file per scope, project first.

<!-- LIB-FUNCTIONS:config.sh-START -->
| Function | Purpose |
|----------|---------|
| `wb_config_ensure_file [FILE]` | create FILE holding just the modeline, when it does not already exist. |
| `wb_config_get KEY [DEFAULT]` | a dotted config key, project scope first. |
<!-- LIB-FUNCTIONS:config.sh-END -->

A malformed file reads as absent — a bash caller wants its default, not a `yq` parse error on stdout. Reporting a bad file is the typed loader's job. Loaded via `ui.sh`. Both filenames, the schema URL and the modeline are declared once in [`constants.sh`](#constantssh) — as `WORKBENCH_CONFIG_FILE`, `WORKBENCH_PROJECT_CONFIG_NAME`, `WORKBENCH_CONFIG_SCHEMA_URL` and `WORKBENCH_CONFIG_HEADER` — and `config.sh` holds functions only.

[`ai/lib/workbench_config.py`](../ai/lib/workbench_config.py) is the typed owner of the same two files: it deep-merges them into a `WorkbenchConfig` and rejects an unknown enum value or phase key rather than silently dropping it. It spells those same names a second time for Python, and `tests/config.bats` fails when a pair drifts. Everything below is generated from the dataclass by `bin/local/generate-config-schema`, alongside [`config.schema.json`](../config.schema.json); `tests/test_workbench_config.py` fails if either committed copy goes stale.

<!-- CONFIG-REFERENCE-START -->
<!-- AUTO-GENERATED — do not edit directly -->
<!-- Regenerate: bin/local/generate-config-schema -->

| Scope | File |
|-------|------|
| Global | `config.yml` under the [config root](#rootssh) |
| Project | `.workbench.yml` at a repo toplevel |

A new config file is born holding one line, the modeline that points an editor's YAML language server at [`config.schema.json`](../config.schema.json):

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/otto-nation/otto-workbench/main/config.schema.json
```

Every key both files accept:

| Key | Values | Default |
|-----|--------|---------|
| `reuse.level` | `lite`, `full`, `ultra` | — |
| `reuse.default` | `lite`, `full`, `ultra` | `full` |
| `review.model` | string | — |
| `review.thinking` | `low`, `medium`, `high` | — |
| `review.provider` | string | — |
| `review.effort` | `low`, `medium`, `high` | — |
| `review.phases.<phase>.model` | string | — |
| `review.phases.<phase>.thinking` | `low`, `medium`, `high` | — |
| `review.issue_tracker.provider` | `linear`, `github`, `jira` | `linear` |
| `review.issue_tracker.team` | string | — |
| `review.issue_tracker.jira_url` | string | — |

`<phase>` is one of: `single`, `holistic`, `scout`, `group`, `synthesis`, `disprove`, `fix`
<!-- CONFIG-REFERENCE-END -->

Both writers seed the modeline — `wb_config_ensure_file` in bash, `set_value` in Python — and `yq -i` carries it through every later write, so completion and enum validation work while the file is hand-edited. Paste it at the top of a `.workbench.yml` to get the same in a project. A file that already exists is never seeded: the modeline is a courtesy on creation, not something sync re-imposes.

Five layers decide a review value, highest first:

| # | Layer | Example |
|---|-------|---------|
| 1 | Explicit flag | `--model opus`, `--effort high` |
| 2 | Phase env var | `CLAUDE_REVIEW_SCOUT_MODEL` |
| 3 | Global env var | `CLAUDE_REVIEW_MODEL` |
| 4 | Project config | `.workbench.yml` |
| 5 | Global config | `config.yml` |

Within one file a `review.phases.<phase>` entry outranks the `review.*` section it sits under. Layers 4 and 5 deep-merge, so a project file that sets one phase keeps every global sibling.

A repo still holding the pre-#626 `.claude/review.yml` is converted to `.workbench.yml` the first time a review reads its issue tracker; the old file is left in place, since it is usually tracked in the consumer repo. The machine-wide files — `reuse-level`, `reuse-default`, `review.yml` — are folded into `config.yml` by `bin/migrations/20260814-unify-workbench-config.sh`.

### migrations.sh

Migration framework with state tracking.

<!-- LIB-FUNCTIONS:migrations.sh-START -->
| Function | Purpose |
|----------|---------|
| `run_component_migrations DIR` | Discovers DIR/migrations/*.sh, skips already-applied migrations, sources and runs each function, and records success. |
| `adopt_legacy_workbench_root` | Move a pre-#624 ~/.config/workbench to whichever roots now own its contents. |
| `run_all_migrations` | Adopts the legacy root, backfills the project registry, prunes stale state, then runs every component's migrations. |
<!-- LIB-FUNCTIONS:migrations.sh-END -->

State file: `$MIGRATIONS_STATE_FILE` — `migrations.applied` under the [state root](#rootssh). Loaded via `ui.sh`. See [Execution Flow — Migrations](execution-flow.md#migrations).

### components.sh

Component discovery via convention-based glob patterns.

<!-- LIB-FUNCTIONS:components.sh-START -->
| Function | Purpose |
|----------|---------|
| `discover_step_files ARRAY_REF` | Populates the nameref array with paths to all steps.sh files |
| `discover_migration_dirs ARRAY_REF` | Populates the nameref array with paths to all migration directories |
<!-- LIB-FUNCTIONS:components.sh-END -->

Sourced by `migrations.sh` and `install.sh`.

### install.sh

Component discovery, selection, and execution — the shared half of the install flow, so bootstrapping a machine and `otto-workbench install` walk the same steps.

<!-- LIB-FUNCTIONS:install.sh-START -->
| Function | Purpose |
|----------|---------|
| `update_path_in_shell_rc` | appends ~/.local/bin to PATH in the user's shell rc file (~/.zshrc or ~/.bashrc) if the entry is not already present. |
| `platform_supported PLATFORMS` | returns 0 if the current OS matches PLATFORMS. |
| `validate_components REGISTRY` | lightweight fast-fail guard before any side effects run. |
| `discover_components REGISTRY` | reads component metadata in registry order. |
| `select_components` | presents a numbered menu and populates SELECTED_COMPONENTS. |
| `run_components` | executes setup.sh for each selected component. |
| `resolve_known_components` | builds lookup sets of all known core and optional component names. |
| `validate_install_targets TARGETS...` | checks that every target is a known component. |
| `discover_core_components` | finds core component dirs and their descriptions. |
| `select_core_components RESULT_ARRAY DIRS_ARRAY DESCS_ARRAY` | presents selection menu for core components. |
| `run_core_component COMPONENT` | runs the install or sync function for a core component. |
| `parse_install_flags ARGS...` | parses --all and component targets. |
| `print_install_summary` | prints the final "All done" screen with a consolidated file listing, editable configs, and per-component summaries. |
<!-- LIB-FUNCTIONS:install.sh-END -->

Sourced by the top-level `install.sh` and by `bin/otto-workbench`. Requires `ui.sh` first. See [Execution Flow — Install Flow](execution-flow.md#install-flow).

### commands.sh

Subcommand documentation, declared once per script. A script lists its commands in a `COMMANDS` array of alternating usage form and description; the usage text and the dispatcher both read that array, so a new subcommand cannot ship undocumented.

<!-- LIB-FUNCTIONS:commands.sh-START -->
| Function | Purpose |
|----------|---------|
| `commands_usage [ARRAY_NAME]` | Print formatted command list from a COMMANDS-style array. |
<!-- LIB-FUNCTIONS:commands.sh-END -->

Bash-only — it uses arrays and namerefs. Sourced directly by the scripts that dispatch subcommands.

## Registry & Config Modules

### registries.sh

Registry discovery, install-check gating, and env/auth iteration.

<!-- LIB-FUNCTIONS:registries.sh-START -->
| Function | Purpose |
|----------|---------|
| `is_installed NAME` | returns 0 if NAME is found in PATH |
| `collect_registries ARRAY_REF SCAN_DIR [BREW_DIR]` | Populates the caller's array (via nameref) with deduplicated registry paths. |
| `registry_passes_install_check FILE` | returns 0 if the registry should be rendered. |
| `iter_registry_env FILE CALLBACK` | Calls CALLBACK var comment default_val setup_url prefix for each env[] entry. |
| `iter_registry_auth FILE CALLBACK` | Calls CALLBACK name env_var setup_url prefix for each tool with an auth block. |
| `collect_registry_permissions ARRAY_REF SCAN_DIR [BREW_DIR]` | Populates the caller's array (via nameref) with Claude Code Bash permission patterns derived from tools' permission field. |
<!-- LIB-FUNCTIONS:registries.sh-END -->

Sourced directly by consumers (`generate-tool-context`, `summary.sh`). Not in the `ui.sh` facade.

### conventions.sh

Git convention constants — single source of truth for commit and PR formatting.

Constants: `COMMIT_TYPES`, `COMMIT_HEADER_MAX_LEN`, `COMMIT_BODY_MAX_LEN`.

No functions. Sourced directly by `lib/ai/core.sh` and git generation scripts.

### summary.sh

Post-run summary output for install and sync.

<!-- LIB-FUNCTIONS:summary.sh-START -->
| Function | Purpose |
|----------|---------|
| `print_workbench_summary` | prints the consolidated summary of what the workbench manages and what the user can edit. |
| `print_warnings_summary` | replays collected warnings and errors from the install log. |
| `run_component_summaries [COMPONENT...]` | auto-discovers and calls print_<name>_summary() from */summary.sh files. |
<!-- LIB-FUNCTIONS:summary.sh-END -->

Sourced directly by `install.sh` and `bin/otto-workbench`.

## AI Modules (`lib/ai/`)

These modules power the AI-driven git automation (commits, PRs, reviews). All are sourced directly by Taskfile tasks — none go through the `ui.sh` facade.

### ai/core.sh

Foundation module: AI command loading, GitHub token resolution with per-org routing, response handling.

<!-- LIB-FUNCTIONS:ai/core.sh-START -->
| Function | Purpose |
|----------|---------|
| `load_ai_command` | Finds the AI config and validates the binary exists. |
| `load_gh_token` | Resolves GH_TOKEN with per-org routing support. |
| `run_ai PROMPT [AGENT_OVERRIDE] [TASK_LABEL]` | Requires AI_COMMAND. |
<!-- LIB-FUNCTIONS:ai/core.sh-END -->

### ai/commit.sh

Commit message generation with validation and automatic retry on length violations.

<!-- LIB-FUNCTIONS:ai/commit.sh-START -->
| Function | Purpose |
|----------|---------|
| `find_commitlint_config` | Sets COMMITLINT_CONFIG to the first config found, or empty string if none. |
| `build_commit_rules` | Requires COMMITLINT_CONFIG (set by find_commitlint_config). |
| `generate_commit_msg DIFF [FILE_LIST]` | Requires AI_COMMAND and COMMIT_RULES. |
| `validate_commit_msg MSG` | Requires COMMITLINT_CONFIG (set by find_commitlint_config). |
<!-- LIB-FUNCTIONS:ai/commit.sh-END -->

### ai/pr.sh

PR content generation: title, description, issue linking, template loading.

<!-- LIB-FUNCTIONS:ai/pr.sh-START -->
| Function | Purpose |
|----------|---------|
| `push_branch BRANCH` | Pushes BRANCH to remote, handling first-push and divergence cases. |
| `create_pr GH_ARGS...` | Runs `gh pr create` with the given arguments and reports the resulting PR URL. |
| `load_pr_context` | Loads the AI command and resolves the current branch context. |
| `parse_pr_flags ARGS` | Parses PR-specific flags from the CLI_ARGS string. |
| `load_pr [ARGS]` | Parses PR flags from ARGS, then loads the PR context. |
| `generate_pr_content BRANCH DEFAULT_BRANCH` | Requires AI_COMMAND (unless PR_TITLE_OVERRIDE and PR_BODY_OVERRIDE are set). |
<!-- LIB-FUNCTIONS:ai/pr.sh-END -->

### ai/review.sh

Code review generation for branch changes and existing PRs.

<!-- LIB-FUNCTIONS:ai/review.sh-START -->
| Function | Purpose |
|----------|---------|
| `generate_diff_review STAGED UNSTAGED COMMITS COMMITTED_DIFF BRANCH DEFAULT_BRANCH` | Requires AI_COMMAND. |
| `generate_pr_review PR_NUMBER PR_TITLE PR_BODY PR_DIFF` | Requires AI_COMMAND. |
<!-- LIB-FUNCTIONS:ai/review.sh-END -->

### ai/compact_diff.sh

Diff compaction: splits diffs into per-file chunks and greedily includes as many as fit within a character budget.

Its only entry point is `_compact_diff`.

### ai/prompts.sh

Prompt templates for all AI automation — pure text generation, no side effects.

<!-- LIB-FUNCTIONS:ai/prompts.sh-START -->
| Function | Purpose |
|----------|---------|
| `prompt_commit DIFF_CONTENT FILES_SECTION [RETRY_PREAMBLE]` | Generates the commit message prompt. When RETRY_PREAMBLE is provided it is prepended with a blank line separator so the AI sees the failure context first. |
| `prompt_commit_retry HEADER HEADER_LEN OVER PREFIX SUBJECT_BUDGET` | Outputs a retry preamble that gives the AI the exact character budget it needs. |
| `prompt_pr_single_commit COMMIT_SUBJECT COMMIT_BODY CHANGED_FILES` | For single-commit branches where a PR template exists: asks the AI to fill the template using the commit message. |
| `prompt_pr_multi_commit BRANCH ISSUE COMMITS COMMIT_COUNT CHANGED_FILES` | For multi-commit branches: asks the AI to generate a PR title and fill the template. |
| `prompt_diff_review CONTEXT` | CONTEXT is a pre-built string of labelled diff sections (committed, staged, unstaged). |
| `prompt_pr_review PR_NUMBER PR_TITLE PR_BODY COMPACT_DIFF` | Review instructions come from the reviewer agent — this prompt provides data only. |
<!-- LIB-FUNCTIONS:ai/prompts.sh-END -->
