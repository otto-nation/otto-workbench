---
title: Execution Flow
description: What happens when you run install.sh or otto-workbench sync, step by step.
---

# Execution Flow

What happens when you run `install.sh` or `otto-workbench sync`, step by step.

## Install Flow

`install.sh` bootstraps a new machine interactively. It runs in six stages:

```
1. Bootstrap         installs Homebrew if missing
2. Core components   bin, git, task, zsh — selectable menu (Enter = all)
3. Path setup        adds ~/.local/bin to shell rc if needed
4. Migrations        runs any pending migration scripts
5. Optional components  brew, docker, terminals, editors, ai, mise — selectable menu (Enter = all)
6. Summary           prints file inventory, warnings, next steps
```

**Component discovery:** core components are auto-discovered by globbing `*/steps.sh` and excluding directories with a `setup.conf` (those are optional). Optional components are listed in [`install.components`](../install.components) with metadata in each component's `setup.conf`.

**Dependency expansion:** optional components can declare `depends` in their `setup.conf`. Dependencies are auto-included and re-sorted to run in order.

**Flags:** `--all` skips menus. Named arguments (`install.sh brew docker`) run only those components.

## Sync Flow

`otto-workbench sync` re-applies everything non-interactively:

```
1. Migrations       prune stale entries, run pending migrations
2. State pruning    remove orphan entries for deleted components
3. Component sync   discover all steps.sh, call sync_<name>() for each
4. Summary          print changes, detect uninstalled components
```

**State gating:** sync only runs components that are recorded as installed, with one exception — infrastructure components (`bin`, `task`, `git`, `zsh`) always sync regardless of state.

**No prompts:** sync runs in `SYMLINK_MODE=no-prompt` — if a real file conflicts with a symlink, it warns and skips instead of prompting. Run `install.sh` for interactive resolution.

## Install vs Sync

| Aspect | `install.sh` | `otto-workbench sync` |
|--------|-------------|----------------------|
| When to use | First-time setup, adding optional components | After pulling workbench updates |
| Interactive | Yes — menus, prompts | No — warns and skips conflicts |
| Scope | Bootstrap + selected components | All installed components |
| Brew packages | Installs from Brewfile | Skipped |
| Docker runtime | Prompts for Colima/OrbStack | Re-symlinks existing socket |
| Templates | Creates configs from templates | Never overwrites editable configs |
| Migrations | Runs pending | Runs pending |
| Real file conflicts | Prompts for overwrite/backup | Warns and skips |

## Migrations

Migrations handle breaking changes — renamed configs, deprecated symlinks, updated defaults. Each is a shell function that runs once and is state-tracked.

**Naming:** `<component>/migrations/YYYYMMDD-slug.sh` defines `migration_YYYYMMDD_slug()`.

**Lifecycle:**
1. Create the migration file following the naming convention
2. [`run_all_migrations()`](../lib/migrations.sh) auto-discovers it via glob
3. On first run: function executes, filename recorded in `$MIGRATIONS_STATE_FILE` (`~/.local/state/workbench/migrations.applied` by default — see [Libraries — roots.sh](libraries.md#rootssh)); a project-scoped migration records one line per repo instead, see below
4. On subsequent runs: skipped (already recorded)
5. If the migration file is deleted: stale entry auto-pruned from state

Migrations must be idempotent — a failed migration is not recorded, so it retries on next sync.

**Dispatch:** the framework sources the file and then calls the function itself, so the file must define the function and nothing else — a statement at file scope runs on the sourcing pass, ahead of the call the framework makes, where its exit status decides nothing. A file that invokes its own function is the usual way that happens. `bin/local/validate-migrations` rejects anything at file scope but the shebang, comments, blanks, `set` lines, and function definitions, stripping function bodies by brace-counting so a call inside a helper is not mistaken for one.

`_source_migration` reads each file twice. A verdict pass runs it in a fresh `bash -e`, where a failing file-scope statement stops the file and is reported; this cannot be done in-process, because bash suppresses errexit — and the `ERR` trap with it — for everything a subshell or function runs when a caller up the chain is an `if`, a `!`, or an `||`, which the framework's own call site is. A load pass then sources the file for its definitions, neutralising its `set -e` and restoring the caller's own setting exactly as found. A file that fails the verdict pass is warned about and retried on the next run; neither it nor the `set -e` a migration file carries can abort the sync.

**Timing:** migrations run before component syncs, ensuring old state is cleaned up before new config is applied.

**Adoption-sensitive migrations:** `adopt_legacy_workbench_root` runs ahead of the framework and carries a legacy `~/.config/workbench` into the config and state roots. A migration that drains a path under one of those roots is undone by an adoption that happens later — the data lands back in the drained path, and the state file already records the migration as applied, so nothing empties it again. A migration in that position declares itself with a `# adoption-sensitive: <reason>` line in its header; a real adoption (one that moved at least one entry) then drops those state entries so the framework gives them another pass. Only marked migrations are forgotten: re-running one that removed something on purpose would put the removal back and undo an operator who had deliberately restored it.

**Project-scoped migrations:** a migration that has to visit every repo on the machine reads the [project registry](libraries.md#projectssh) rather than scanning for `.claude` directories under a list of likely git roots. `seed_project_registry` backfills that registry and, like adoption, is called from `run_all_migrations` ahead of the framework — migrations run in filename order, so a backfill written as a migration of its own would sort after some consumer, which would then read an empty registry, do nothing, and record itself as applied. It is a no-op after the first run on a machine.

The loop over the registry belongs to the framework, not to the migration. A migration declares itself with a `# project-scoped: <reason>` line in its header; the framework then calls its function once per registered repo with that repo's path as the only argument, and records one state line per repo — the ordinary key, a tab, and the repo path. "Applied" for such a migration is a fact about a repo rather than about the machine, and that is what lets a repo which registers late still receive it: registration is an observation, so it can only ever be late, and a repo the state file does not yet name is simply one the next sync visits. A migration that ran its own loop could only record the single machine-wide line the framework asked it for, so the next sync skipped it outright and every repo cloned since kept the shape the migration existed to replace — silently, with the state file reporting the work long done. A worktree is a registry entry of its own and is visited on its own, which is right: it has its own `.claude/` to fix.

Per-repo state is per-repo retry too — a repo whose run fails is the only one not recorded, so the next sync retries that repo rather than the whole machine. Everything that rewrites the state file splits a line on its first tab and compares the key ahead of it, so a per-repo entry is recognised by pruning and by adoption's forgetting alike. `_prune_stale_migration_state` also reconciles those entries with the registry: one naming a repo that has left is dropped, and so is one written in the shape the other scope records — which is what lets a migration change scope without needing a new date.

`bin/local/validate-migrations` checks that the header and the signature agree — a marked migration must read the path it is handed, and an unmarked one must read no argument, because the framework calls it with none. Both mismatches are silent at runtime: a marked function that ignores the path does the same global thing once per repo, and an unmarked one that reads `$1` works on an empty string.

A migration corrected after it has already recorded itself applied gets a new date in its filename. `_prune_stale_migration_state` drops the state entry for a file that no longer exists, so the rename is what gives the corrected version a run; leaving the date alone would ship the fix as dead code on every machine that had run the broken one. `ai/claude/migrations/20260819-context-to-architecture.sh` is dated later than the rename it performs for exactly that reason.

## File Operations

The workbench uses different strategies depending on whether a file should track upstream changes or be user-editable.

### Symlinks (`install_symlink`, `symlink_dir`)

Used for files that should always reflect the workbench source: executable scripts, git hooks, Taskfile.

- If symlink already points to the correct source → no-op
- If a real file exists → prompt (install) or warn and skip (sync)
- Stale symlinks are replaced silently

### Copies (`install_file`, `copy_dir`)

Used for config files that may diverge per-machine: zsh config layers, starship config.

- Content-based comparison — only copies if content differs
- Removes stale symlinks before copying (enables migration from symlink → copy)
- Never overwrites unless content actually changed

### Layer merging (`resolve_layers`)

Used for AI config (rules, agents, skills) where users can override or disable items. See [User Overrides](user-overrides.md).

### Decision guide

| Scenario | Function | Why |
|---|---|---|
| Executable scripts, hooks | `install_symlink` | Always tracks upstream |
| Config layers (zsh, starship) | `install_file` / `copy_dir` | Content-checked, machine-safe |
| Overrideable AI config | `resolve_layers` → symlink/copy | User layer wins |
| Editable configs (gitconfig, .env.local) | Template on first install | Never overwritten by sync |

## State Tracking

Component installation state is recorded in `$INSTALL_YML_FILE` — `~/.local/state/workbench/install.yml` by default, a YAML map of components to the sub-tools installed under them. `$INSTALLED_STATE_FILE` (`installed.components`) is the flat file it replaced; only `state_file_exists` still looks at it.

**Functions** (in [`lib/state.sh`](../lib/state.sh)):
- `state_record()` — mark a component as installed
- `state_is_installed()` — check if installed
- `state_prune_orphans()` — remove entries for deleted components
- `state_detect_installed()` — heuristic-based detection for bootstrapping state on existing machines

**Backward compatibility:** if no state file exists, sync runs all discovered components (pre-state-tracking behavior).

## Generated Files

These files are derived from source data and must never be edited directly. Edit the source and regenerate.

| File | Generator | Source |
|------|-----------|--------|
| [`tools.generated.md`](../ai/guidelines/rules/tools.generated.md) | [`generate-tool-context`](../bin/local/generate-tool-context) | `*/registry.yml` |
| [`git.generated.md`](../ai/guidelines/rules/git.generated.md) | [`generate-git-rules`](../git/bin/local/generate-git-rules) | [`lib/conventions.sh`](../lib/conventions.sh) |
| `docs/tools.md` | [`compose-docs`](../bin/local/compose-docs) | `docs/tools.src.md` + registries |
| `docs/ai-automation.md` | [`compose-docs`](../bin/local/compose-docs) | `docs/ai-automation.src.md` + skills, agents, Taskfile |
| `docs/components.md` | [`compose-docs`](../bin/local/compose-docs) | `docs/components.src.md` + component discovery |
| `docs/getting-started.md` | [`compose-docs`](../bin/local/compose-docs) | `docs/getting-started.src.md` + `setup.conf` post-install notes |
| `docs/libraries.md` | [`compose-docs`](../bin/local/compose-docs) | `docs/libraries.src.md` + the `lib/*.sh` module headers |
| `.claude/anatomy.md` | [`generate-anatomy.sh`](../ai/claude/skills/anatomy/generate-anatomy.sh) | `git ls-files` |
| [`config.schema.json`](../config.schema.json) | [`generate-config-schema`](../bin/local/generate-config-schema) | [`ai/lib/workbench_config.py`](../ai/lib/workbench_config.py) |

A composed doc names the generators it wants; nothing maps a doc to a generator centrally. `docs/libraries.md` reaches three that way: [`generate-lib-reference`](../bin/local/generate-lib-reference) for the module sections, and — from inside a module header, since compose-docs expands what a generator prints — [`generate-lib-reference --roots-table`](../lib/roots.sh) and [`generate-config-schema --emit config-reference`](../lib/config.sh).

**Enforcement:** the composed docs are covered by [`validate-docs-composed`](../bin/local/validate-docs-composed), which `validate-all` discovers, so a stale artifact fails before anything is regenerated and names one command to fix it. [`validate-lib-reference`](../bin/local/validate-lib-reference) adds the one thing freshness cannot see — a module in a group `docs/libraries.src.md` never asks for is documented nowhere, and the composed file is current either way. The pre-push hook then runs the tool-context generators and blocks if their own output changed. `config.schema.json` is enforced instead by `tests/test_workbench_config.py`, which fails when the committed copy differs from what the generator would write — pre-push runs pytest, so both paths block a stale file. CI runs the same freshness checks on every PR.

## Environment Variable Generation

`~/.env.local` is created from [`zsh/.env.local.template`](../zsh/.env.local.template) on first install. On every sync, the generator scans all `*.env.yml` files and rewrites the ENV marker section from scratch. Everything below `ENV-END` is yours and is never overwritten.

Because the marker section is regenerated wholesale, a value set *inside* it would be lost. `step_env_local` prevents that: before the rewrite it collects every line between the markers that the generator did not just produce, appends those lines to the end of the file under a `# Moved here by otto-workbench sync` comment, and warns with the name of each variable it relocated. The relocated copy lands after all pre-existing content, so it is the assignment the shell ends up with. Re-running sync finds nothing left to hoist and leaves the file byte-identical.
