# Component Framework

The workbench uses a two-tier component model for organizing setup and sync operations.

## Decision Rule

> **Will this always be applied on every machine, on every sync?** -> Tier 1 (Core)
> **Should it appear in the install menu as an opt-in?** -> Tier 2 (Optional)
> **Does it have idempotent operations worth re-applying on sync?** -> also add `steps.sh`

**How discovery works:**
- [`install.sh`](../install.sh) and `otto-workbench sync` auto-discover core components by globbing `*/steps.sh` and skipping any that have a sibling `setup.conf` (those are optional).
- Adding a new core component requires **no edits to `install.sh`** — just create the directory with `steps.sh` defining `sync_<name>()`.
- Adding a new optional component requires creating `setup.conf` + `setup.sh` and adding the name to [`install.components`](../install.components).

Run `bin/validate-components` to check contracts for all existing components.

---

## Tier 1 — Core Components

Core components are always applied by `install.sh` and always re-applied by `otto-workbench sync`.

**Required files:**

| File | Purpose |
|------|---------|
| `steps.sh` | All step functions + `sync_<name>()` |

**Contract:**
- `steps.sh` must define `sync_<name>()` (auto-called by `otto-workbench sync` via discovery)
- `steps.sh` must have a standalone bootstrap guard so it can be run directly: `bash <component>/steps.sh`
- No `setup.conf`, not registered in `install.components`

**Example:** adding an SSH config component that always needs to be synced:
```
ssh/
  steps.sh     <- defines step_ssh_config(), sync_ssh()
```

```bash
# ssh/steps.sh
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

step_ssh_config() {
  install_symlink "$SSH_SRC_DIR/config" "$HOME/.ssh/config"
}

sync_ssh() {
  echo; info "SSH config -> ~/.ssh/"
  step_ssh_config
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}SSH setup${NC}\n"
  sync_ssh
  echo; success "SSH setup complete!"
fi
```

<!-- CORE-COMPONENTS-START -->
**Existing core components:** `bin`, `git`, `mise`, `task`, `zsh`
<!-- CORE-COMPONENTS-END -->

---

## Tier 2 — Optional Components

Optional components appear in the `install.sh` menu and are run interactively during first-time setup.

**Required files:**

| File | Required | Purpose |
|------|----------|---------|
| `setup.conf` | yes | metadata: `label`, `description`, `platforms`, optional `check` |
| `setup.sh` | yes | interactive install — runs as a subshell |
| `steps.sh` | if syncable | defines `sync_<name>()`, auto-discovered by `otto-workbench sync` |
| `summary.sh` | optional | defines `print_<name>_summary()`, sourced by `install.sh` post-install |

**Registration:** add the directory name to [`install.components`](../install.components) (in desired menu order).

**The `check` field** — if defined, `install.sh` runs it before `setup.sh`. If it exits 0, the component is already configured and is skipped automatically. `WORKBENCH_DIR` and `DOTFILES_DIR` are both available in check command context:

```ini
label = My tool
description = Installs and configures my-tool
check = command -v my-tool && my-tool verify --config "$WORKBENCH_DIR/mytool/config"
```

**When to add `steps.sh` to an optional component:**
Add `steps.sh` when any part of `setup.sh`'s work can be safely re-applied without interaction (e.g., symlinking a config file, importing a color theme). If nothing is idempotent, omit `steps.sh` — its absence is an explicit signal that this component has no sync path.

`summary.sh` must contain only function definitions — no top-level execution. [`install.sh`](../install.sh) sources it after all components have run and calls `print_<name>_summary()` if defined.

**Example:** adding a tool with both interactive install and sync coverage:
```
mytool/
  setup.conf    <- label, description, platforms, optional check
  setup.sh      <- interactive install (runs as subshell)
  steps.sh      <- sync_mytool() for idempotent re-application
  summary.sh    <- print_mytool_summary() for post-install output
```

<!-- OPTIONAL-COMPONENTS-START -->
**Existing optional components:** `brew` (has sync), `docker` (has sync), `terminals` (has sync), `editors` (has sync), `ai` (has sync)
<!-- OPTIONAL-COMPONENTS-END -->

---

## `sync_<name>()` Contract

Every `steps.sh` that defines `sync_<name>()` **must** follow these rules:

1. **Idempotent** — running twice must produce the same state as running once.
2. **Non-interactive** — must not prompt for user input. Use [`install_symlink`](../lib/files.sh) (respects `SYMLINK_MODE`), not `prompt_overwrite` directly.
3. **Self-contained** — relies only on constants from [`lib/constants.sh`](../lib/constants.sh) and helpers from [`lib/ui.sh`](../lib/ui.sh); does not depend on being called from a specific working directory.
4. **Standalone bootstrap** — if the steps.sh can be run directly (`bash component/steps.sh`), include the standard bootstrap guard at the top.

## Error Recovery

| Scenario | Behavior |
|----------|----------|
| Component `setup.sh` fails during `run_components` | Warn and continue — other components still run |
| Framework contract violation (missing `register_<tool>_steps`, bad registry) | Hard-fail immediately — setup cannot proceed safely |
| Individual `install_symlink` on a real file (non-interactive mode) | Warn and skip — real files are never silently overwritten by sync |
| Missing optional dependency (brew, jq, etc.) | Warn via `require_command` and return — caller decides whether to exit |
