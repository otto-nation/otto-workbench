---
title: Libraries
description: All shared code in lib/ — the modules loaded through the ui.sh facade and those sourced directly.
---
<!-- Generated from docs/libraries.src.md by bin/local/compose-docs — do not edit. -->

# Libraries

All shared code lives in `lib/`. Most modules are loaded through the `ui.sh` facade; some are sourced directly by specific consumers.

Each section below is the module's own header comment, rendered from `lib/` by [`generate-doc-reference`](../bin/local/generate-doc-reference) — so the prose describing a module lives beside the code it describes, and its function table is read out of the file rather than restated here.

A function's Purpose cell is the first paragraph of its doc comment, in full. Rationale that belongs to the implementation rather than the contract goes below a blank comment line, where the reader who opens the file finds it and the table does not carry it.

## Loading

Scripts source `lib/ui.sh` via `git rev-parse --show-toplevel` — depth-independent:

```bash
_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
. "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/ui.sh"
```

`ui.sh` is a facade: the modules it sources are the ones marked *Loaded via `ui.sh`* below. Every other module is sourced directly by its consumers. `roots.sh` reaches the facade through `constants.sh`, and is also sourced on its own by consumers that need the roots without the rest of the framework.

## Core Modules

### branch_state.sh

"Has this branch's work finished?", shared by the cleanup tools.

The answer comes from the issue tracker rather than from git whenever a PR
exists, because git cannot always give one: a branch descending from a
different root has no merge base with the default branch, so `git cherry` and
`git branch --merged` have nothing to compare and report it unmerged forever.
The PR state is the only signal that survives a re-rooted repo.

The lookup is batched — one `gh pr list` for the whole repo, not one `gh pr
view` per branch, which cost a sequential round trip each.

```bash
declare -A states
branch_pr_states states || echo "no tracker available"
echo "${states[feat/x]:-}"   # OPEN | MERGED | CLOSED, or empty
```

Bash-only — the state map is an associative array returned through a nameref.
Sourced directly by scripts that already load `lib/ui.sh` or on its own;
it depends only on `gh` and `jq`.

| Function | Purpose |
|----------|---------|
| `branch_gh_available` | whether gh can answer for the current repo. |
| `branch_pr_states ASSOC_ARRAY_NAME` | fill an associative array branch → state. |

### commands.sh

Subcommand documentation, declared once per script.

A script lists its commands in a `COMMANDS` array of alternating usage form
and description; the usage text and the dispatcher both read that array, so a
new subcommand cannot ship undocumented. Nested dispatchers use
`COMMANDS_PARENT` (uppercased) arrays, and handler functions follow `cmd_NAME`
at the top level or `cmd_PARENT_CHILD` when nested.

Bash-only — it uses arrays and namerefs.

| Function | Purpose |
|----------|---------|
| `commands_usage [ARRAY_NAME]` | Print formatted command list from a COMMANDS-style array. Auto-aligns description column based on the longest usage form. |

Loaded via `ui.sh`.

### components.sh

Component discovery via convention-based glob patterns.

The single source of truth for finding `steps.sh` files and migration
directories across the workbench. All discovery uses the same two-level glob —
top-level dirs plus one level of nesting — so a new component tier such as
`editors/zed/` is found without an edit here.

Sourced by `migrations.sh` and `install.sh`:

```bash
. "$WORKBENCH_DIR/lib/components.sh"
discover_step_files  _steps_arr    # populates array with steps.sh paths
discover_migration_dirs _dirs_arr  # populates array with migration dir paths
```

| Function | Purpose |
|----------|---------|
| `discover_step_files ARRAY_REF` | Populates the nameref array with paths to all steps.sh files: $WORKBENCH_DIR/*/steps.sh and $WORKBENCH_DIR/*/*/steps.sh |
| `discover_migration_dirs ARRAY_REF` | Populates the nameref array with paths to all migration directories: $WORKBENCH_DIR/*/migrations and $WORKBENCH_DIR/*/*/migrations |

### config.sh

Hand-authored settings, read from YAML — one file per scope, project first.

```bash
wb_config_get "reuse.level"           # value, or nothing
wb_config_get "reuse.level" "full"    # value, or the given default
```

A malformed file reads as absent — a bash caller wants its default, not a `yq`
parse error on stdout. Reporting a bad file is the typed loader's job. Both
filenames, the schema URL and the modeline are declared once in
[`constants.sh`](#constantssh) — as `WORKBENCH_CONFIG_FILE`,
`WORKBENCH_PROJECT_CONFIG_NAME`, `WORKBENCH_CONFIG_SCHEMA_URL` and
`WORKBENCH_CONFIG_HEADER` — and `config.sh` holds functions only.

[`ai/lib/workbench_config.py`](../ai/lib/workbench_config.py) is the typed
owner of the same two files: it deep-merges them into a `WorkbenchConfig` and
rejects an unknown enum value or phase key rather than silently dropping it. It
spells those same names a second time for Python, and `tests/config.bats` fails
when a pair drifts. The scope and key tables below are generated from the
dataclass by `bin/local/generate-config-schema`, alongside
[`config.schema.json`](../config.schema.json); `tests/test_workbench_config.py`
fails if the committed schema goes stale.

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
| `issue_tracker.provider` | `linear`, `github`, `jira` | — |
| `issue_tracker.team` | string | — |
| `issue_tracker.jira_url` | string | — |
| `github.ssh_over_443` | boolean | `false` |

`<phase>` is one of: `single`, `holistic`, `scout`, `group`, `synthesis`, `disprove`, `fix`

Both writers seed the modeline — `wb_config_ensure_file` in bash, `set_value`
in Python — and `yq -i` carries it through every later write, so completion and
enum validation work while the file is hand-edited. A `.workbench.yml` that the
workbench creates for you — recording an answer such as
`issue_tracker.provider` — is seeded the same way; paste it in yourself at the
top of one you hand-author. A file that already exists is never seeded: the
modeline is a courtesy on creation, not something sync re-imposes.

Writes go through `otto-workbench config set KEY VALUE` (`--project` for the
repo's own file), which is
[`lib/config_cli.py`](../lib/config_cli.py) over `set_value`. It refuses a key
neither this checkout nor the *installed* workbench reads: a checkout can be
weeks behind `main` and still write the file every repo on the machine shares,
and `serde` drops an unknown key on read, so a value recorded under a name the
config has moved off is gone with nothing said at either end. Both surfaces are
consulted because only the installed one can catch a stale writer using a key
that is still valid where it is standing. Hand-editing stays supported — that
is what the modeline is for — but nothing checks the key.

Five layers decide a review value, highest first:

| # | Layer | Example |
|---|-------|---------|
| 1 | Explicit flag | `--model opus`, `--effort high` |
| 2 | Phase env var | `CLAUDE_REVIEW_SCOUT_MODEL` |
| 3 | Global env var | `CLAUDE_REVIEW_MODEL` |
| 4 | Project config | `.workbench.yml` |
| 5 | Global config | `config.yml` |

Within one file a `review.phases.<phase>` entry outranks the `review.*` section
it sits under. Layers 4 and 5 deep-merge, so a project file that sets one phase
keeps every global sibling.

A repo still holding the legacy `.claude/review.yml` is converted to
`.workbench.yml` the first time a review reads its issue tracker; the old file
is left in place, since it is usually tracked in the consumer repo. The
machine-wide files — `reuse-level`, `reuse-default`, `review.yml` — are folded
into `config.yml` by `bin/migrations/20260814-unify-workbench-config.sh`.

| Function | Purpose |
|----------|---------|
| `wb_config_ensure_file [FILE]` | create FILE holding just the modeline, when it does not already exist. |
| `wb_config_get KEY [DEFAULT]` | a dotted config key, project scope first. KEY must be a literal string. |
| `wb_config_project_get DIR KEY` | a dotted config key from the project config at DIR, or nothing. DIR is a repo's work-tree root; KEY must be a literal string. |

Loaded via `ui.sh`.

### constants.sh

Path and filename constants, auto-derived from the workbench root.

The single source of truth for `WORKBENCH_DIR`, `LOCAL_BIN_DIR`,
`ZSH_CONFIG_DIR`, `CLAUDE_DIR`, `MIGRATIONS_STATE_FILE`, `INSTALL_YML_FILE`,
`INSTALLED_STATE_FILE`, `MAINTENANCE_LAST_FILE`, and every other shared path.

HOME-relative paths work on any machine without any caller setup. Workbench
source paths are derived from this file's own location, so callers never need
to set `WORKBENCH_DIR`, `DOTFILES_DIR`, `SCRIPT_DIR`, or `_AI_DIR`; any caller
may set `WORKBENCH_DIR` before sourcing to override the derived path.

No public functions — constants only. Sources `roots.sh`.

Loaded via `ui.sh`.

### conventions.sh

Git convention constants — single source of truth for commit and PR formatting.

Constants: `COMMIT_TYPES`, `COMMIT_HEADER_MAX_LEN`, `COMMIT_BODY_MAX_LEN`,
`BREAKING_CHANGE_FOOTER`, `BREAKING_CHANGE_FOOTER_ALT`, `NOT_BREAKING_FOOTER`,
`BREAKING_FOOTER_RE`, `DECLARED_FOOTER_RE`. To add a commit type, append it to
`COMMIT_TYPES` — no other change is needed.

The footer helpers answer one question — "does this message declare a breaking
change" — for all three readers that ask it: the pre-push gate
(`bin/local/check-surface-compat`), the local commit validator
(`validate_commit_msg`), and the reword path that carries an existing footer
onto a regenerated message. POSIX only: the file is sourced by `/bin/sh` on the
go-task path, so no `[[`, no `<<<`, no pattern-replacement expansion.

Sourced directly by `lib/ai/core.sh` and the git generation scripts
(`git/bin/generate-changelog`, `git/bin/local/generate-git-rules`).

| Function | Purpose |
|----------|---------|
| `has_breaking_footer MSG` | true when MSG declares a breaking change in its body. |
| `declared_footers MSG` | every declaration footer line in MSG, in order. |

### files.sh

File operations with idempotency: symlinks, copies, directory operations,
layer merging.

Bash-only — it uses `local`, arrays, and the prompt helpers.

| Function | Purpose |
|----------|---------|
| `install_symlink SOURCE TARGET [LABEL] [--no-prompt]` | Creates or updates a symlink at TARGET pointing to SOURCE. LABEL defaults to the basename of SOURCE. |
| `install_file SOURCE TARGET [LABEL]` | Copies SOURCE to TARGET if content differs. Removes stale symlinks at TARGET. Idempotent — no-op if file is already up to date. |
| `copy_dir SRC DST [GLOB] [--strip-ext] [--prune]` | Copies all files matching GLOB in SRC into DST, preserving filenames. GLOB defaults to '*'. --strip-ext removes the file extension from the display label. --prune removes stale files (or symlinks) in DST whose source counterpart is gone. |
| `symlink_dir SRC DST [GLOB] [--strip-ext] [--prune] [--replace-copies]` | Symlinks all items matching GLOB in SRC into DST, preserving filenames. GLOB defaults to '*'. |
| `sync_component_bin COMPONENT_DIR` | symlinks extensionless scripts from COMPONENT_DIR/bin/ into LOCAL_BIN_DIR. No-op if bin/ subdirectory is absent. |
| `list_shell_scripts ROOT` | prints every file under ROOT whose *first* line is a shell or bats shebang, one per line, sorted. Skips .git, ignore/, __pycache__, node_modules/, and .py. |
| `resolve_layers BASE_DIR USER_DIR GLOB RESULT_NAMEREF` | Merges two directory layers into an associative array: basename -> source_path. User dir wins for same-named files. A .disabled sentinel in user dir suppresses both. RESULT_NAMEREF must be a declared associative array in the caller. |
| `is_disabled USER_DIR NAME` | returns 0 if a .disabled sentinel exists. |
| `install_hook_dispatcher SOURCE_RELPATH TARGET [LABEL]` | Writes a thin dispatcher script that execs the hook from the current worktree. Unlike symlinks, dispatchers resolve at runtime — so worktrees always run their own branch's version of the hook, not main's. |
| `apply_config_patch FILE OLD NEW` | Replaces OLD with NEW in FILE if OLD is present. Idempotent — no-op if already patched or if FILE does not exist. Assumes OLD and NEW do not contain the \| character. |

Loaded via `ui.sh`.

### gitenv.sh

The inherited git environment, and how to stop it choosing the repository.

git reads GIT_DIR ahead of the directory `-C` moved to, and GIT_INDEX_FILE
ahead of the index inside it. A script that takes a repository path from its
caller is therefore answered by whatever repository the environment names, not
by the one it was given — and the answer looks entirely ordinary. The pre-push
hook exports GIT_DIR, so every gate under `bin/local/` that accepts a path runs
in exactly that situation.

It has no dependencies, so a caller that has not loaded the facade can source
it on its own:

```bash
git_env_clear          # then `git -C DIR ...` really means DIR
```

| Function | Purpose |
|----------|---------|
| `git_env_clear` | drop every git environment override inherited from a caller, so that `git -C DIR` and repository discovery both answer for DIR. |

Loaded via `ui.sh`.

### install.sh

Component discovery, selection, and execution — the shared half of the install
flow, so bootstrapping a machine and `otto-workbench install` walk the same
steps.

Sourced by the top-level `install.sh` and by `bin/otto-workbench`. Requires
`ui.sh` first, which provides the constants, prompts, and file helpers. See
[Execution Flow — Install Flow](execution-flow.md#install-flow).

| Function | Purpose |
|----------|---------|
| `update_path_in_shell_rc` | appends ~/.local/bin to PATH in the user's shell rc file (~/.zshrc or ~/.bashrc) if the entry is not already present. No-op on unsupported shells. |
| `platform_supported PLATFORMS` | returns 0 if the current OS matches PLATFORMS. PLATFORMS is a space-separated list of: macos, linux. Empty or "all" means always supported. |
| `validate_components REGISTRY` | lightweight fast-fail guard before any side effects run. Checks only that registered components exist on disk and that no setup.conf is orphaned. |
| `discover_components REGISTRY` | reads component metadata in registry order. Populates COMPONENT_DIRS, COMPONENT_LABELS, COMPONENT_DESCS, COMPONENT_PLATFORMS. |
| `select_components` | presents a numbered menu and populates SELECTED_COMPONENTS. Platform-incompatible components are silently skipped. With --all flag or empty selection, all eligible components are selected. Components with `depends` in setup.conf have their deps auto-included and re-sorted into install.components order so deps always run before dependents. |
| `run_components` | executes setup.sh for each selected component. If setup.conf defines a `check` command, runs it first; skips the component if it exits 0. DOTFILES_DIR is exported so check commands can reference it. |
| `resolve_known_components` | builds lookup sets of all known core and optional component names. Populates KNOWN_CORE_COMPONENTS and KNOWN_OPTIONAL_COMPONENTS. |
| `validate_install_targets TARGETS...` | checks that every target is a known component. |
| `discover_core_components` | finds core component dirs and their descriptions. Populates the nameref arrays with dirs and descriptions. |
| `select_core_components RESULT_ARRAY DIRS_ARRAY DESCS_ARRAY` | presents selection menu for core components. Reads INSTALL_ALL, INSTALL_TARGETED, INSTALL_TARGETS from caller. |
| `run_core_component COMPONENT` | runs the install or sync function for a core component. |
| `parse_install_flags ARGS...` | parses --all and component targets. Sets INSTALL_ALL, INSTALL_TARGETS, INSTALL_TARGETED in caller's scope. |
| `print_install_summary` | prints the final "All done" screen with a consolidated file listing, editable configs, and per-component summaries. |

### migrations.sh

Migration framework with state tracking.

Migration files live in `<component>/migrations/YYYYMMDD-slug.sh` and define a
single idempotent function named `migration_YYYYMMDD_slug` — dashes replaced
with underscores. Such a function returns 0 when it changed something,
`MIGRATION_NOOP` when it found nothing to do, `MIGRATION_DEFERRED` when the
target it converts does not exist yet, and anything else to fail. A change and
a no-op are recorded and never revisited; a deferral and a failure are retried
on the next sync, the deferral silently.

State file: `$MIGRATIONS_STATE_FILE` — `migrations.applied` under the [state
root](#rootssh). One line per applied migration, or one line per repo — the
key, a tab, and the repo path — for a migration marked `# project-scoped:`,
which the framework runs once per entry in the [project registry](#projectssh).
Stale entries, pointing at migration files that have since been removed, are
pruned automatically. See [Execution Flow — Migrations](execution-flow.md#migrations).

```bash
. "$WORKBENCH_DIR/lib/migrations.sh"
run_all_migrations              # discover and run across all components
run_component_migrations DIR    # run for a single component directory
```

| Function | Purpose |
|----------|---------|
| `run_component_migrations DIR` | Discovers DIR/migrations/*.sh, skips already-applied migrations, sources and runs each function, and records success. Failed migrations are not recorded and retry on the next run. Migrations must be idempotent. |
| `adopt_legacy_workbench_root` | Move a pre-split ~/.config/workbench to whichever roots now own its contents. No-op once the legacy root is gone, or when a root still resolves to it. |
| `run_all_migrations` | Adopts the legacy root, backfills the project registry, prunes stale state, then runs every component's migrations. |

### output.sh

Output helpers — colors, logging, and portable sed.

Works in both bash and zsh, so it uses no bash-only features: the zsh
component's own scripts source it, and the facade sources it outside the
bash-only guard for that reason.

The color variables — `BOLD`, `GREEN`, `BLUE`, `YELLOW`, `RED`, `CYAN`, `DIM`,
`NC` — are exported alongside the functions for callers that format their own
output.

| Function | Purpose |
|----------|---------|
| `sed_i EXPRESSION FILE` | portable in-place sed (macOS and Linux). |
| `indent PREFIX` | copy stdin to stdout with PREFIX at the start of every line. PREFIX is written literally, so it is spaces for a nested block of output and a marker word for a machine-readable one. |
| `info MESSAGE` | blue info message with an arrow. |
| `success MESSAGE` | green success message with a checkmark. |
| `warn MESSAGE` | yellow warning; also logged to WORKBENCH_INSTALL_LOG. |
| `err MESSAGE` | red error to stderr; also logged to WORKBENCH_INSTALL_LOG. |
| `title TEXT` | bold blue section header. |
| `skip [label]` | print a skip line with optional label |
| `print_version SCRIPT_NAME [COMPONENT_KEY]` | print tool and workbench version. Reads from .github/.release-please-manifest.json in WORKBENCH_DIR. |
| `sync_header LABEL` | section header for sync steps. Suppressed during sync. |
| `summary_section LABEL` | cyan section header for summaries. Suppressed during sync. |
| `summary_ok MESSAGE` | indented success line. Suppressed during sync. |
| `summary_warn MESSAGE` | indented warning; logged instead of printed during sync. |
| `summary_err MESSAGE` | indented error line. Printed even during sync. |
| `summary_info MESSAGE` | indented dim detail line. Suppressed during sync. |

Loaded via `ui.sh`.

### portable.sh

Machine readers that work on both userlands.

GNU coreutils and BSD spell the same values differently — `stat` takes
different format flags, and the load average comes from `/proc/loadavg` on one
and `sysctl vm.loadavg` on the other. Each reader here tries both forms so no
caller has to branch on the platform. For `stat` that is also enforced:
nothing outside this module calls it with a format flag, and
`bin/local/validate-stat-portability` fails the build when something does. A
hand-rolled fallback prints a filesystem report before failing on GNU, which
has already broken CI once; the header on `_stat_field` has the details.

It has no dependencies, so a caller that has not loaded the facade can source
it on its own:

```bash
file_mtime PATH   # modification time, epoch seconds
file_birth PATH   # birth time, epoch seconds (0 where the FS has none)
file_mode  PATH   # permission bits, octal — e.g. 644
load_average      # one-minute load average, e.g. 3.72
```

Each prints nothing and returns 1 when neither form resolves the value, so
callers that want a default supply it themselves:

```bash
ts=$(file_mtime "$f") || ts=0
```

| Function | Purpose |
|----------|---------|
| `file_mtime PATH` | modification time in epoch seconds. |
| `file_birth PATH` | birth (creation) time in epoch seconds. Prints 0 on filesystems that do not record one; callers must treat 0 as "unknown". |
| `file_mode PATH` | permission bits as an octal string, e.g. 644. |
| `load_average` | the machine's one-minute load average, as the kernel spells it. |

Loaded via `ui.sh`.

### projects.sh

The repos on this machine that use otto-workbench.

State file: `$PROJECTS_REGISTRY_FILE` — `projects.registry` under the [state
root](#rootssh), one absolute path per line with `#` comment lines. Text rather
than YAML for the reason `migrations.applied` is: every write is an append and
every read is a scan, and YAML would pay a `yq` fork on each of them. The
filename is declared once, in [`constants.sh`](#constantssh), and this file
holds functions only.

Membership means a workbench command actually ran in a repo. Nothing scans for
candidates — the two consumers that used to, the machine profile generator and
the project-scoped migrations, each carried their own guessed-at list of git
roots and a depth limit, so a repo cloned anywhere else was invisible and the
migration recorded itself applied all the same. Registration is an observation,
so it can only ever be late; `otto-workbench projects add` is what covers a
repo that joined after something needed to see it. The registrations are:

| Caller | Where the root comes from |
|--------|---------------------------|
| Claude's SessionStart hook (`reuse-session-start`) | already resolved for the ceiling scan |
| `pr` | `ctx.worktree_root` |
| `otto-workbench ai init` | the repo being scaffolded |
| `otto-workbench projects add [DIR]` | by hand, for a repo that uses neither |

`project_register` does no discovery of its own and forks nothing: every caller
has a resolved work-tree root in hand. A path under `$TMPDIR`, `/tmp`,
`/var/folders`, or the workbench's own state or cache root is refused — `bats`
builds throwaway repos there and runs validators and pre-commit hooks inside
them. The `/private` twins of the temp roots are listed too, because callers
hand over a path `git rev-parse --show-toplevel` already resolved and those two
are symlinks into `/private` on macOS. A bare repo's container is refused as
well, holding worktrees rather than being one. `PROJECTS_EXCLUDED_PREFIXES` is
assignable so a test can register the repos it builds, which are all temporary.

Reads drop entries whose directory is gone, which is what saves the registry
from needing a pruning job; `otto-workbench projects prune` makes the drop
permanent. Repeats are dropped on read for a related reason: registration is an
append guarded by a membership check rather than a lock, so two workbench
commands starting in one repo at the same moment can each read "absent" and
each append. Absorbing that where it is read costs nothing; a lock would tax
every hook to prevent a duplicate line.

`otto-workbench projects forget DIR` canonicalises `DIR` before matching —
entries are stored as `git rev-parse --show-toplevel` returned them, and the
comparison is an exact string, so a relative path, one holding `..`, one
reaching through a symlink, or one naming a subdirectory of the repo all have
to arrive in that form or a valid request reads as "not in the registry". A
directory that is already gone can only be normalised lexically, which is the
right answer for it: whatever entry it matches was written while it still
existed.

`seed_project_registry` backfills the repos that predate the registry, once per
machine, from the `.projects` map in `~/.claude.json` — an observation Claude
Code wrote, not another guess at where repos live. Each key is a session cwd,
so `_project_seed_roots` turns one into a work-tree root: `git rev-parse
--show-toplevel` for a normal checkout, and for a bare-repo container — a
directory that refuses `--show-toplevel` outright — the worktree checked out on
the branch the container's HEAD names, the same choice `WORKBENCH_STABLE_DIR`
makes. A container's feature worktrees are deliberately left out: they come and
go, each would be a row of its own everywhere the registry is read, and any
still around registers itself the next time a workbench command runs in it.

A `# backfilled from <path>` line inside the file records that the backfill
ran: the Python half creates the file the first time `pr` registers anything,
so a backfill keyed on the file's existence would be skipped forever on a
machine that used a tool before it next synced. Without `jq` it writes no
marker and returns — no candidates for want of a reader is indistinguishable
from a machine that has none, and recording the marker on that reading would
retire the backfill before it ever ran. It is called from `run_all_migrations`
ahead of the framework rather than written as a migration, for the reason
adoption is — see [Execution Flow —
Migrations](execution-flow.md#migrations).

[`ai/lib/workbench_projects.py`](../ai/lib/workbench_projects.py) is the Python
half — the SessionStart hook and `pr` register through it, against the same
file in the same shape. It raises nothing: registration is a side effect of a
command run for some other reason, and a hook that died on an unwritable state
file would cost a session for a bookkeeping entry. The filename is declared
once in [`constants.sh`](#constantssh) as `PROJECTS_REGISTRY_NAME` and once in
`workbench_paths.py`; `tests/workbench_roots.bats` fails when the two drift,
and `tests/projects.bats` cross-validates the halves against one file.

| Function | Purpose |
|----------|---------|
| `project_register DIR` | record DIR as a repo that uses the workbench. |
| `project_registered` | print every registered repo that still exists, one per line. |
| `project_forget DIR` | drop DIR's entry. Returns 1 when it had none. |
| `project_prune` | drop entries whose directory is gone, and repeats. Prints how many went. |
| `seed_project_registry` | backfill the repos that predate the registry, once. |

Loaded via `ui.sh`.

### prompts.sh

User interaction: confirmations, menus, and config reading.

Bash-only — `read -n 1` behaves differently in zsh.

| Function | Purpose |
|----------|---------|
| `confirm "msg"` | [Y/n]; returns 0 for yes (default), 1 for no |
| `confirm_n "msg"` | [y/N]; returns 0 for yes, 1 for no (default) |
| `confirm_step RESULT_VAR MSG` | [Y/n/a]; writes "yes", "no", or "all" to RESULT_VAR. "a" means accept this step and all remaining steps without prompting. |
| `prompt_overwrite FILE` | warns that FILE already exists and presents a single combined prompt. [o]verwrite / [b]ackup and overwrite / [s]kip (default: skip) Creates a .backup copy before overwriting when b is chosen. Returns 1 if the user skips, 0 if they choose to overwrite (with or without backup). |
| `select_menu RESULT_VAR COUNT [--default all\|skip\|require] [--single]` | Displays a numbered selection prompt and writes the result back to RESULT_VAR. Validates input against 1..COUNT; warns and ignores out-of-range numbers. 0 always means explicit skip regardless of --default. |
| `select_subdirs RESULT_VAR PARENT_DIR PROMPT [SELECT_MENU_OPTS...]` | Discovers subdirectories in PARENT_DIR that contain setup.sh, presents a numbered menu with PROMPT, and writes space-separated selected names to RESULT_VAR. Any extra arguments are forwarded to select_menu (e.g. --default all, --single). Returns 1 if no subdirectories are found. |
| `conf_get FILE KEY` | reads a key = value line from a KEY = VALUE config file. Returns the trimmed value, or empty string if the key is not found. |

Loaded via `ui.sh`.

### roots.sh

The three user-level roots the workbench writes to, each resolved through the
same chain:

```
WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default
```

| Constant | Holds | XDG rung | Default |
|----------|-------|----------|---------|
| `WORKBENCH_CONFIG_DIR` | Hand-authored settings: config.yml, overrides/ | `XDG_CONFIG_HOME` | `~/.config/workbench` |
| `WORKBENCH_STATE_DIR` | Generated, machine-local data: reviews/, trail/, usage/, install.yml, migrations.applied | `XDG_STATE_HOME` | `~/.local/state/workbench` |
| `WORKBENCH_CACHE_DIR` | Recomputable data, safe to delete at any time: vertex-quota/ | `XDG_CACHE_HOME` | `~/.cache/workbench` |

`install.yml` sits under state despite the name: `lib/state.sh` owns every
write to it, and it is what the old `installed.components` file migrated into.
It records what a sync found or installed, not anything a user chose to type.

Machines set up before the split keep everything in `~/.config/workbench`.
`adopt_legacy_workbench_root` in [`lib/migrations.sh`](../lib/migrations.sh)
carries that directory across on the next sync, entry by entry, and runs ahead
of the migration framework because `migrations.applied` is one of the files it
moves. Nothing falls back to the old path once it has run — the adoption is the
entire compatibility story.

Routing is by name and spelled out in three branches, not two plus a
fallthrough: `_LEGACY_CONFIG_ENTRIES` goes to the config root,
`_LEGACY_UNCLAIMED_ENTRIES` is skipped and left in the legacy root, and
everything else goes to the state root. That last default is deliberate — the
inventory behind the split found state files no manifest written in advance had
listed — but it must not also absorb a name no root holds any more: adoption
runs before any migration reads its bookkeeping, so an entry a completed
migration deleted on purpose (`logs/`, for one) would come back with nothing
left to take it out again. Adding one to the unclaimed list is the fix whenever
a migration prunes a top-level name from a root.

A file the new root already holds is normally kept on both sides and warned
about rather than clobbered. The exception is the append-only ledgers —
`trail.jsonl` and `usage/*.jsonl` — which are concatenated instead: their only
writers open them in append mode, and `otto-log` sorts every record by `ts`
after loading, so one history split across two files reassembles either way.
The rule is keyed on those names, not on the `.jsonl` extension, because the
review artifacts (`session.jsonl`, `post.jsonl`, `*.holistic.jsonl`) are
whole-file writes whose convention is prior-content-first.

Its own module rather than part of `constants.sh` because two other consumers
need the roots without the rest: the `otto-ai-tools` tarball ships `roots.sh`
alongside its own `ui.sh` facade (see `BASH_MODULES` in
`ai/claude/bin/build-otto-ai-tools-tarball`), and `registries.sh` sources it
directly when a caller has not loaded `constants.sh`.

Two definitions outside `lib/` express the same chain, and
`tests/workbench_roots.bats` cross-validates all three:

- [`ai/lib/workbench_paths.py`](../ai/lib/workbench_paths.py) — the Python
  owner. Exposes `config_dir()`, `state_dir()`, `cache_dir(consumer=None)`,
  `trail_dir()`, and `reviews_dir()`, resolved per call rather than frozen at
  import. `cache_dir` takes a consumer name and rejects anything but a bare
  directory name — a path would land outside the tree the root's owner globs
  over. `trail_dir()` takes nothing: every trail writer shares one root,
  `<state>/trail/`, with one file per month. `reviews_dir()` is the sole owner
  of the reviews join, so the review system and the tool that reads its output
  — `retro-scan` for the findings — cannot disagree about where a review is.
- [`zsh/config.d/aliases/docker.zsh`](../zsh/config.d/aliases/docker.zsh) —
  spelled inline, because `WORKBENCH_DIR` is unknown at shell startup and
  sourcing would add a file read to every shell.

#### Trails

Every AI script appends to `trail_dir()`, in a file named for the emitting
event's UTC month. The layout mirrors `ai_usage.ledger_dir`: rotation falls out
of the filename, `--since` drops whole files without opening them, and nothing
needs a pruning job. `_emit` takes an `fcntl.flock` on the open handle inside
the module's thread lock — one file now takes appends from concurrent
processes (`pr` and the script it spawned), and a short write (NFS, a signal,
an rlimit boundary) can split a record across two `write()` calls, letting the
other process's append land in the gap.

The `20260814-unify-trail-root` migration carried the pre-cutover review trails
into `trail/legacy.jsonl`. `otto-log` always reads a file whose stem does not
name a month, which is what keeps it visible under `--since`.

### setup.sh

Install workflow helpers: step registration, requirement checks, cask installs.

Bash-only. Used primarily by `install.sh` and component setup scripts.

| Function | Purpose |
|----------|---------|
| `register_step NAME FN` | appends a step to the STEPS array. STEPS must be declared as an array in the calling script before register_step is used. |
| `run_steps` | prints all registered steps upfront, then runs each with [Y/n/a] confirmation. Steps are read from the global STEPS array (populated via register_step). Prints a summary of ran/skipped counts when complete. |
| `require_command NAME [MESSAGE]` | returns 1 with a warning if NAME is not in PATH. Caller decides whether to exit or return: require_command foo "msg" \|\| exit 0 |
| `install_cask CMD CASK LABEL MANUAL_URL` | Installs a tool via Homebrew cask if CMD is not already in PATH. Falls back to a manual install message if brew is unavailable. |
| `run_migrations DIR` | DEPRECATED: Use run_component_migrations from lib/migrations.sh instead. This function sources a single migrations.sh file with no state tracking. Kept for backward compatibility until all callers are migrated. |

Loaded via `ui.sh`.

### state.sh

Component installation state tracking.

Records which components and sub-tools are installed in a structured YAML
file. Core components — bin, git, zsh, task — are omitted, since they always
sync.

State file: `$INSTALL_YML_FILE` — `install.yml` under the state root. The flat
`installed.components` it replaced survives only as the second half of
`state_file_exists`.

```bash
state_record "ai"           # record a component
state_record "ai/claude"    # record a sub-tool
state_is_installed "ai"     # returns 0 if installed
state_remove "ai/claude"    # remove an entry
state_file_exists           # returns 0 if state file exists
```

| Function | Purpose |
|----------|---------|
| `state_record ENTRY` | records a component or sub-tool in install.yml. Idempotent. |
| `state_is_installed ENTRY` | returns 0 if entry is recorded in install.yml. |
| `state_remove ENTRY` | removes a component or sub-tool from install.yml. |
| `state_file_exists` | returns 0 if install.yml (or legacy state file) exists. |
| `state_list` | prints all installed entries, one per line (flat format for compat). |
| `state_prune_orphans` | removes YAML entries that have no matching steps.sh. Requires lib/components.sh to be sourced (provides discover_step_files). |
| `state_detect_installed` | detects currently installed components and records them. Uses heuristics (config files, symlinks, directories) to determine what is present. Called by the initial-state migration and by `otto-workbench discover regenerate`. |
| `state_set KEY VALUE` | sets an arbitrary YAML path under components. Example: state_set "docker.runtime" "orbstack" |
| `state_clear_list KEY` | resets a YAML list to empty sequence. |
| `state_append_list KEY VALUE` | appends VALUE to a YAML list (idempotent). Example: state_append_list "brew.stacks" "infra/kubernetes" |
| `state_get KEY` | reads a YAML value. Returns empty string for missing/null keys. |
| `state_get_list KEY` | reads a YAML list, one item per line. |
| `state_load_selections STATE_KEY SCRIPT_DIR RESULT_ARRAY [AVAILABLE_ARRAY]` | Loads saved selections from YAML, validates each against SCRIPT_DIR. When AVAILABLE_ARRAY is provided, detects new tools on disk that aren't in the saved list — forces a fresh menu so the user can opt in (or deselect). |

Loaded via `ui.sh`.

### worktree.sh

Where a project artifact goes.

A file that lives inside a repository — `.claude/anatomy.md`, `.mcp.json`, a
`CLAUDE.md` — belongs in a working tree. A bare-repo container has none: it
holds the bare `.git` plus each checkout as a peer, so a file written at the
container root is tracked by nothing, covered by no `.gitignore` rule, and
reached by no review or CI check. The only way one is ever found is by hand.

Every writer therefore resolves its tree before writing rather than trusting
the current directory, and does it the same way:

```bash
root="$(project_root)" || rc=$?
```

| Function | Purpose |
|----------|---------|
| `project_root [DIR]` | prints the working tree DIR belongs in, for a caller about to write a file that lives inside a repository. DIR defaults to the current directory. Exits 0 with the path, 1 when DIR is a bare-repo container naming no worktree to write into, 2 when DIR is in no repository at all, and 64 when DIR does not exist. |

Loaded via `ui.sh`.

## Registry & Config Modules

### registries.sh

Registry discovery, install-check gating, and env/auth iteration.

The schema these functions read — the meta block, the tool entry fields, the
`*.env.yml` shape, and the cross-validation modes — is documented once, in
[Registries](registries.md#schema). `KNOWN_TOOL_FIELDS` and
`KNOWN_COMMAND_FIELDS` below are what `validate-registries` rejects unknown
keys against.

Sourced directly by its consumers — `bin/local/generate-tool-context`,
`bin/local/validate-registries`, `brew/summary.sh`, `summary.sh`, and
`ai/claude/steps.sh`. Not in the `ui.sh` facade. It loads `roots.sh` itself when
the caller has not already sourced `constants.sh`, since an
`install_check_symlink` value may name a workbench root.

| Function | Purpose |
|----------|---------|
| `is_installed NAME` | returns 0 if NAME is found in PATH |
| `collect_component_registries ARRAY_REF SCAN_DIR` | the component `registry.yml` files under a root. ARRAY_REF names the caller's array, which is replaced with the paths found one and two directories below SCAN_DIR, in glob order. SCAN_DIR is the root those globs are anchored at; a root holding none of them leaves the array empty rather than filling it with unexpanded patterns. |
| `collect_registries ARRAY_REF SCAN_DIR [BREW_DIR]` | Populates the caller's array (via nameref) with deduplicated registry paths. |
| `registry_passes_install_check FILE` | returns 0 if the registry should be rendered. Checks meta.install_check and meta.install_check_command. |
| `iter_registry_env FILE CALLBACK` | Calls CALLBACK var comment default_val setup_url prefix for each env[] entry. |
| `iter_registry_auth FILE CALLBACK` | Calls CALLBACK name env_var setup_url prefix for each tool with an auth block. |
| `collect_registry_permissions ARRAY_REF SCAN_DIR [BREW_DIR]` | Populates the caller's array (via nameref) with Claude Code Bash permission patterns derived from tools' permission field, one of the tool entry fields described in this module's header comment above. |

### summary.sh

Post-run summary output for install and sync.

Prints a consolidated view of managed files, editable configs, and quick
reference commands. Every path variable comes from `constants.sh`, and the
environment-setup block is rendered from the registries, so a new `*.env.yml`
entry appears here without an edit.

Sourced directly by the top-level `install.sh` and `bin/otto-workbench`.

| Function | Purpose |
|----------|---------|
| `print_workbench_summary` | prints the consolidated summary of what the workbench manages and what the user can edit. |
| `print_warnings_summary` | replays collected warnings and errors from the install log. No-op if WORKBENCH_INSTALL_LOG is unset or the file is empty/missing. |
| `run_component_summaries [COMPONENT...]` | auto-discovers and calls print_<name>_summary() from */summary.sh files. If COMPONENT args are given, only those are checked; otherwise all components with summary.sh are discovered via glob. |

## AI Modules (`lib/ai/`)

These modules power the AI-driven git automation (commits, PRs, reviews). All are sourced directly by Taskfile tasks — none go through the `ui.sh` facade.

### ai/commit.sh

Commit message generation with validation and automatic retry on length
violations.

Requires [`ai/core.sh`](#aicoresh) to be sourced first. Typical call sequence:

```bash
find_commitlint_config   # sets COMMITLINT_CONFIG
build_commit_rules       # sets COMMIT_RULES (derived from COMMITLINT_CONFIG)
generate_commit_msg DIFF # sets AI_MSG
validate_commit_msg MSG  # validates; returns 1 on failure
```

State set by its functions: `COMMITLINT_CONFIG`, `COMMIT_RULES`, `AI_MSG`.

| Function | Purpose |
|----------|---------|
| `find_commitlint_config` | Sets COMMITLINT_CONFIG to the first config found, or empty string if none. configuration files picked from: https://github.com/conventional-changelog/commitlint?tab=readme-ov-file#config |
| `build_commit_rules` | Requires COMMITLINT_CONFIG (set by find_commitlint_config). Sets COMMIT_RULES. Uses COMMIT_TYPES for the allowed-types list. |
| `generate_commit_msg DIFF [FILE_LIST]` | Requires AI_COMMAND and COMMIT_RULES. Sets AI_MSG. Retries once with a precise character budget if the header exceeds COMMIT_HEADER_MAX_LEN, and returns 1 if the retry also fails. |
| `validate_commit_msg MSG` | Requires COMMITLINT_CONFIG (set by find_commitlint_config). Uses commitlint when available; falls back to a basic header length check. Returns 1 on validation failure. |
| `preserve_declared_footers ORIGINAL_MSG` | Re-appends to AI_MSG every declaration footer ORIGINAL_MSG carries that the generated message does not already have. |

### ai/compact_diff.sh

Diff compaction: splits diffs into per-file chunks and greedily includes as
many as fit within a character budget.

Its only entry point is `_compact_diff`. Smallest files go in first, which
maximises how many are covered; the ones that do not fit are listed by name in
a trailing note. Requires [`ai/core.sh`](#aicoresh) to be sourced first, for
`DIFF_MAX_CHARS`.

Kept out of `core.sh` because it uses bash arrays, and `core.sh` has to stay
POSIX-compatible for the go-task path.

### ai/core.sh

Foundation module: AI command loading, GitHub token resolution with per-org
routing, response handling.

Sourced first by `commit.sh`, `pr.sh`, and `review.sh`, and by the Taskfile
tasks that drive them. It inherits the commit conventions by sourcing
[`conventions.sh`](#conventionssh), so `COMMIT_TYPES` and the length limits
have one owner across both halves.

POSIX-compatible: go-task sources it through `sh -c`, which is why the array
work lives in [`compact_diff.sh`](#aicompact_diffsh) instead.

State set by its functions: `AI_COMMAND`, `AI_RESPONSE`.

| Function | Purpose |
|----------|---------|
| `resolve_default_branch` | Resolves the remote's default branch and prints it to stdout. |
| `remote_branch_ref_exists BRANCH` | True when BRANCH has a remote-tracking ref under $GIT_REMOTE (refs/remotes/$GIT_REMOTE/BRANCH). |
| `load_ai_command` | Finds the AI config and validates the binary exists. Sets AI_COMMAND. Returns 1 on failure. |
| `load_gh_token` | Resolves GH_TOKEN with per-org routing support. Returns 1 on failure. |
| `run_ai PROMPT [AGENT_OVERRIDE] [TASK_LABEL]` | Requires AI_COMMAND. When AGENT_OVERRIDE is provided, replaces --agent <name> in AI_COMMAND so different tasks can route to the appropriate agent. TASK_LABEL names the call in the usage ledger. Sets AI_RESPONSE. |

### ai/pr.sh

PR content generation: title, description, issue linking, template loading.

Requires [`ai/core.sh`](#aicoresh) to be sourced first. Typical call sequence:

```bash
load_pr [ARGS]                     # sets SKIP_ISSUE, PR_BASE, AI_COMMAND, BRANCH, DEFAULT_BRANCH
push_branch BRANCH                 # pushes the branch if needed
generate_pr_content BRANCH DEFAULT # sets PR_TITLE, PR_DESCRIPTION
create_pr GH_ARGS...               # runs gh pr create, reports the PR URL
```

State set by its functions: `BRANCH`, `DEFAULT_BRANCH`, `SKIP_ISSUE`,
`PR_BASE`, `PR_ISSUE`, `PR_TEMPLATE`, `PR_HAS_TEMPLATE`, `PR_TITLE`,
`PR_DESCRIPTION`.

| Function | Purpose |
|----------|---------|
| `push_branch BRANCH` | Pushes BRANCH to remote, handling first-push and divergence cases. Returns 1 on any failure that should abort the caller. |
| `create_pr GH_ARGS...` | Runs `gh pr create` with the given arguments and reports the resulting PR URL. gh's exit code is the authoritative success signal; a zero exit with no parsable pull request URL is still treated as a failure. Returns 1 on any failure. |
| `load_pr_context` | Loads the AI command, resolves the current branch context and verifies the effective base has a remote-tracking ref. Sets BRANCH and DEFAULT_BRANCH. Returns 1 on failure. |
| `parse_pr_flags ARGS` | Parses PR-specific flags from the CLI_ARGS string. Sets SKIP_ISSUE, PR_DRAFT, PR_BASE, PR_TITLE_OVERRIDE, PR_BODY_OVERRIDE. Returns 1 on unknown flag or missing value. |
| `load_pr [ARGS]` | Parses PR flags from ARGS, then loads the PR context. Sets SKIP_ISSUE, PR_BASE, AI_COMMAND, BRANCH, DEFAULT_BRANCH. Returns 1 on failure. |
| `generate_pr_content BRANCH DEFAULT_BRANCH` | Requires AI_COMMAND (unless PR_TITLE_OVERRIDE and PR_BODY_OVERRIDE are set). Sets PR_TITLE and PR_DESCRIPTION. |

### ai/prompts.sh

Prompt templates for all AI automation — pure text generation, no side
effects.

Each function prints a filled prompt to stdout. Callers pass the dynamic values
as arguments; the configuration globals (`COMMIT_RULES`, `PR_TEMPLATE`,
`COMMIT_HEADER_MAX_LEN`, and the rest) are read straight from
[`ai/core.sh`](#aicoresh), which must be sourced first.

| Function | Purpose |
|----------|---------|
| `prompt_commit DIFF_CONTENT FILES_SECTION [RETRY_PREAMBLE] [SURFACE_NOTE]` | Generates the commit message prompt. RETRY_PREAMBLE is prepended to it and SURFACE_NOTE is rendered directly after COMMIT_RULES, each when non-empty. |
| `prompt_commit_retry HEADER HEADER_LEN OVER PREFIX SUBJECT_BUDGET` | Outputs a retry preamble that gives the AI the exact character budget it needs. Passed as RETRY_PREAMBLE to a second call of prompt_commit. |
| `prompt_pr_single_commit COMMIT_SUBJECT COMMIT_BODY CHANGED_FILES` | For single-commit branches where a PR template exists: asks the AI to fill the template using the commit message. Reads PR_TEMPLATE global. |
| `prompt_pr_multi_commit BRANCH ISSUE COMMITS COMMIT_COUNT CHANGED_FILES` | For multi-commit branches: asks the AI to generate a PR title and fill the template. Reads PR_TEMPLATE, PR_TITLE_MARKER, PR_DESCRIPTION_MARKER globals. |
| `prompt_diff_review CONTEXT` | CONTEXT is a pre-built string of labelled diff sections (committed, staged, unstaged). Built by generate_diff_review before calling this function. Review instructions come from the reviewer agent — this prompt provides data only. |
| `prompt_pr_review PR_NUMBER PR_TITLE PR_BODY COMPACT_DIFF` | Review instructions come from the reviewer agent — this prompt provides data only. |

### ai/review.sh

Code review generation for branch changes and existing PRs.

Requires [`ai/core.sh`](#aicoresh) to be sourced first. Each diff section is
compacted independently through `_compact_diff`, so a large file in one section
cannot crowd the others out.

State set by its functions: `AI_RESPONSE`.

| Function | Purpose |
|----------|---------|
| `generate_diff_review STAGED UNSTAGED COMMITS COMMITTED_DIFF BRANCH DEFAULT_BRANCH` | Requires AI_COMMAND. Builds a review prompt from three diff sources (committed, staged, unstaged). Each section is independently compacted via _compact_diff. Sets AI_RESPONSE. |
| `generate_pr_review PR_NUMBER PR_TITLE PR_BODY PR_DIFF` | Requires AI_COMMAND. Builds a review prompt from PR metadata and its diff. Sets AI_RESPONSE. |

### ai/session-count.sh

Session-counting helper for dream/promote cooldown checks.
