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
3. On first run: function executes, filename recorded in `$MIGRATIONS_STATE_FILE` (`~/.local/state/workbench/migrations.applied` by default — see [Libraries — roots.sh](libraries.md#rootssh)); a migration declaring a scope records one line per target instead, see below
4. On subsequent runs: skipped (already recorded)
5. If the migration file is deleted: stale entry auto-pruned from state

Migrations must be idempotent — a failed migration is not recorded, so it retries on next sync.

**Nothing to do is not work done:** a migration returns `0` when it changed something, `MIGRATION_NOOP` when it found the target already in the shape it exists to produce, `MIGRATION_DEFERRED` when the target it converts does not exist yet, and anything else to fail. A change and a no-op are both recorded — the target has been visited and the answer will not change — but only a change is announced, and the count in the line a checkout-scoped migration prints is targets changed rather than targets visited. Idempotency makes "nothing to do" the commonest outcome a migration has, and for a checkout-scoped one it is very nearly the only one: every worktree registers itself the first time anything runs in it, so each sync visits the worktrees created since the last one, and a branch cut from a `main` that already holds the new shape can never change anything. Reported as success, that is a `✓ Migration applied: <file> (3 projects)` line on every sync for as long as the machine keeps making worktrees — a different count each time, none of it meaning work was done. A migration that has never heard of `MIGRATION_NOOP` still works and is still reported as having applied; converting one is what makes its reporting honest.

**A target that is not there yet is not a target already done:** the two absences look identical in a migration's source — a guard on `[[ -f "$file" ]]` fires the same way whichever it is — and answering the second one with `MIGRATION_NOOP`, or with a bare `0`, retires the migration for good against a file it never saw. `bin/migrations/20260819-lift-issue-tracker-key.sh` did exactly that: it returned `0` on a machine with no `config.yml`, was recorded, and had nothing left to lift when a session wrote the legacy shape into a new `config.yml` half an hour later. The workbench creates such files on its own schedule — `~/.gitconfig` and `~/.env.local` are written by component steps later in the same sync, `config.yml` by `wb_config_ensure_file` or by any session that changes a setting — so "absent" is a state that resolves without anyone re-running anything. `MIGRATION_DEFERRED` is how a migration says so: nothing is recorded, nothing is printed, and the next sync asks again. The silence is the price of the retry, since a target that stays absent is answered on every sync for the life of the file. `bin/local/validate-migrations` holds both ends of it — a `return 0` under an absent-path guard is rejected outright, and `MIGRATION_DEFERRED` is only accepted from such a guard, so a status nothing records cannot be reached from a condition that never resolves.

Deferring is not automatic for an absent target. A migration that *removes* something has nothing stale to remove on a machine where the file never existed, and a copy of that file arriving later is a fresh install whose contents the operator chose — draining those is the same undo that adoption-forgetting deliberately refuses to perform. `ai/claude/migrations/20260402-remove-all-mcps.sh` answers `MIGRATION_NOOP` for that reason.

**Dispatch:** the framework sources the file and then calls the function itself, so the file must define the function and nothing else — a statement at file scope runs on the sourcing pass, ahead of the call the framework makes, where its exit status decides nothing. A file that invokes its own function is the usual way that happens. `bin/local/validate-migrations` rejects anything at file scope but the shebang, comments, blanks, `set` lines, and function definitions, stripping function bodies by brace-counting so a call inside a helper is not mistaken for one.

`_source_migration` reads each file twice. A verdict pass runs it in a fresh `bash -e`, where a failing file-scope statement stops the file and is reported; this cannot be done in-process, because bash suppresses errexit — and the `ERR` trap with it — for everything a subshell or function runs when a caller up the chain is an `if`, a `!`, or an `||`, which the framework's own call site is. A load pass then sources the file for its definitions, neutralising its `set -e` and restoring the caller's own setting exactly as found. A file that fails the verdict pass is warned about and retried on the next run; neither it nor the `set -e` a migration file carries can abort the sync.

**Timing:** migrations run before component syncs, ensuring old state is cleaned up before new config is applied.

**Adoption-sensitive migrations:** `adopt_legacy_workbench_root` runs ahead of the framework and carries a legacy `~/.config/workbench` into the config and state roots. A migration that drains a path under one of those roots is undone by an adoption that happens later — the data lands back in the drained path, and the state file already records the migration as applied, so nothing empties it again. A migration in that position declares itself with a `# adoption-sensitive: <reason>` line in its header; a real adoption (one that moved at least one entry) then drops those state entries so the framework gives them another pass. Only marked migrations are forgotten: re-running one that removed something on purpose would put the removal back and undo an operator who had deliberately restored it.

**Migrations with a scope:** a migration that has to visit every repo on the machine reads the [project registry](libraries.md#projectssh) rather than scanning for `.claude` directories under a list of likely git roots. `seed_project_registry` backfills that registry and, like adoption, is called from `run_all_migrations` ahead of the framework — migrations run in filename order, so a backfill written as a migration of its own would sort after some consumer, which would then read an empty registry, do nothing, and record itself as applied. It is a no-op after the first run on a machine. `project_prune` runs next and drops the entries whose work tree has gone, so the passes after it walk the repos the machine has rather than every one it has ever had — every read already skips such a line, but only the prune stops it being stored and stat-ed again on the next sync, and a machine that cuts and removes worktrees routinely accumulates them faster than anything else adds a repo. Nothing is held back for a work tree that is merely unreachable, on an unmounted volume: no reader distinguishes that from gone either, and the line comes back with the next workbench command run there. `record_project_repo_ids` runs last and gives each surviving registry line the identity of the repository behind it — the realpath of its `--git-common-dir`, which every worktree of one repo shares and no two repos do.

The loop over the registry belongs to the framework, not to the migration, and a migration declares which loop it wants. `# checkout-scoped: <reason>` runs its function once per registered work tree, with that work tree's path as the only argument, and records the ordinary key, a tab, and the work-tree path. `# repo-scoped: <reason>` runs it once per distinct repo, with one of that repo's registered work trees as the argument — it still needs a checkout to run git in — and records the key against the repo's shared git dir instead. A file carrying both is rejected. "Applied" is then a fact about a target rather than about the machine, and that is what lets a repo which registers late still receive the migration: registration is an observation, so it can only ever be late, and a target the state file does not yet name is simply one the next sync visits. A migration that ran its own loop could only record the single machine-wide line the framework asked it for, so the next sync skipped it outright and every repo cloned since kept the shape the migration existed to replace — silently, with the state file reporting the work long done.

Which of the two a migration wants is a question about what it edits. A file under a checkout's own `.claude/` is checkout-scoped: a worktree is a registry entry of its own and has its own copy to fix. Anything the checkouts share — a file at the bare-repo container, an object in the shared git dir — is repo-scoped, because the first checkout visited does the work and every later one finds it done. `ai/claude/migrations/20260824-drop-container-anatomy.sh` is the second kind: as a per-checkout migration it held sixteen state lines for six repos' worth of deletion, and each `wt remove` orphaned two of them. Which work tree a repo-scoped migration is handed is deliberately not stable — the state key names the repo, so a leader replaced by the next sync re-runs nothing.

Per-target state is per-target retry too — a target whose run fails is the only one not recorded, so the next sync retries that one rather than the whole machine. Everything that rewrites the state file splits a line on its first tab and compares the key ahead of it, so a per-target entry is recognised by pruning and by adoption's forgetting alike. `_prune_stale_migration_state` also reconciles those entries with the registry: an entry whose target has left is dropped, and so is one written in the shape another scope records — which is what lets a migration change scope without needing a new date. Both drops are silent. A checkout is removed by `wt remove` as a matter of routine, and the bookkeeping following it out is not something the operator reading a sync log can act on; the `Pruned stale migration state` warning, which means a migration *file* has gone, is the one that still says so.

`bin/local/validate-migrations` checks that the header and the signature agree — a migration marked with either scope must read the path it is handed, an unmarked one must read no argument because the framework calls it with none, and no file may carry both markers. Every mismatch is silent at runtime: a marked function that ignores the path does the same global thing once per target, and an unmarked one that reads `$1` works on an empty string.

A migration corrected after it has already recorded itself applied gets a new date in its filename. `_prune_stale_migration_state` drops the state entry for a file that no longer exists, so the rename is what gives the corrected version a run; leaving the date alone would ship the fix as dead code on every machine that had run the broken one. `ai/claude/migrations/20260819-context-to-architecture.sh` is dated later than the rename it performs for exactly that reason, and `bin/migrations/20260824-lift-issue-tracker-key.sh` carries the date it was corrected on rather than the `20260819` it shipped as.

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
| `docs/ai-libraries.md` | [`compose-docs`](../bin/local/compose-docs) | `docs/ai-libraries.src.md` + the `ai/lib/*/*.py` module docstrings |
| `.claude/anatomy.md` | [`generate-anatomy.sh`](../ai/skills/anatomy/generate-anatomy.sh) | `git ls-files` |
| [`config.schema.json`](../config.schema.json) | [`generate-config-schema`](../bin/local/generate-config-schema) | [`ai/lib/config/workbench_config.py`](../ai/lib/config/workbench_config.py) |

A composed doc names the generators it wants; nothing maps a doc to a generator centrally. `docs/libraries.md` reaches three that way: [`generate-doc-reference`](../bin/local/generate-doc-reference) for the module sections, and — from inside a module header, since compose-docs expands what a generator prints — [`generate-doc-reference --roots-table`](../lib/roots.sh) and [`generate-config-schema --emit config-reference`](../lib/config.sh).

**Enforcement:** the composed docs are covered by [`validate-docs-composed`](../bin/local/validate-docs-composed), which `validate-all` discovers, so a stale artifact fails before anything is regenerated and names one command to fix it. [`validate-doc-reference`](../bin/local/validate-doc-reference) adds the one thing freshness cannot see — a module in a group no `docs/*.src.md` asks for is documented nowhere, and the composed file is current either way. The pre-push hook then runs the tool-context generators and blocks if their own output changed. `config.schema.json` is enforced instead by `tests/test_workbench_config.py`, which fails when the committed copy differs from what the generator would write — pre-push runs pytest, so both paths block a stale file. CI runs the same freshness checks on every PR.

## Environment Variable Generation

`~/.env.local` is created from [`zsh/.env.local.template`](../zsh/.env.local.template) on first install. On every sync, the generator scans all `*.env.yml` files and rewrites the ENV marker section from scratch. Everything below `ENV-END` is yours and is never overwritten.

Because the marker section is regenerated wholesale, a value set *inside* it would be lost. `step_env_local` prevents that: before the rewrite it collects every line between the markers that the generator did not just produce, appends those lines to the end of the file under a `# Moved here by otto-workbench sync` comment, and warns with the name of each variable it relocated. The relocated copy lands after all pre-existing content, so it is the assignment the shell ends up with. Re-running sync finds nothing left to hoist and leaves the file byte-identical.
