# Execution Flow

What happens when you run `install.sh` or `otto-workbench sync`, step by step.

## Install Flow

`install.sh` bootstraps a new machine interactively. It runs in five stages:

```
1. Preflight         task, brew, mise — mandatory, runs first
2. Core components   bin, git, zsh — selectable menu (Enter = all)
3. Path setup        adds ~/.local/bin to shell rc if needed
4. Migrations        runs any pending migration scripts
5. Optional components  brew, docker, terminals, editors, ai — selectable menu (Enter = all)
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

**State gating:** sync only runs components that are recorded as installed, with one exception — infrastructure components (`bin`, `task`, `mise`, `git`, `zsh`) always sync regardless of state.

**No prompts:** sync runs in `SYMLINK_MODE=no-prompt` — if a real file conflicts with a symlink, it warns and skips instead of prompting. Run `install.sh` for interactive resolution.

## Install vs Sync

| Aspect | `install.sh` | `otto-workbench sync` |
|--------|-------------|----------------------|
| When to use | First-time setup, adding optional components | After pulling workbench updates |
| Interactive | Yes — menus, prompts | No — warns and skips conflicts |
| Scope | Preflight + selected components | All installed components |
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
3. On first run: function executes, filename recorded in `~/.config/workbench/migrations.applied`
4. On subsequent runs: skipped (already recorded)
5. If the migration file is deleted: stale entry auto-pruned from state

Migrations must be idempotent — a failed migration is not recorded, so it retries on next sync.

**Timing:** migrations run before component syncs, ensuring old state is cleaned up before new config is applied.

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

Component installation state is recorded in `~/.config/workbench/installed.components` (one entry per line, e.g., `ai/claude`, `terminals/ghostty`).

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
| `docs/tools.md` (tables) | [`generate-tool-context`](../bin/local/generate-tool-context) | Registries |
| `docs/ai-automation.md` (tables) | [`generate-tool-context`](../bin/local/generate-tool-context) | Skills, agents, Taskfile |
| `docs/components.md` (lists) | [`generate-tool-context`](../bin/local/generate-tool-context) | Component discovery |
| `.env.local.template` (ENV section) | [`generate-tool-context`](../bin/local/generate-tool-context) | `*.env.yml` |
| `.claude/anatomy.md` | [`generate-anatomy.sh`](../ai/claude/skills/anatomy/generate-anatomy.sh) | `git ls-files` |

**Enforcement:** the pre-push hook runs generators and blocks if output changed. CI runs the same freshness check on every PR.

## Environment Variable Generation

`~/.env.local` is created from [`zsh/.env.local.template`](../zsh/.env.local.template) on first install. On every sync, the generator scans all `*.env.yml` files and updates the auto-generated ENV section in the template. Existing user values are never overwritten — only new variables are appended.
