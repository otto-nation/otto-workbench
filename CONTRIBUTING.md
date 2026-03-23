# Contributing

## Setup

```bash
git clone https://github.com/otto-nation/otto-workbench ~/otto-workbench
cd ~/otto-workbench
task dev:setup
```

`task dev:setup` activates the git hooks in `git/hooks/`, which run ShellCheck, validate registries, and regenerate tool context before every push.

This runs ShellCheck and the bats test suite before every push.

## Dev Dependencies

Required to run the full test and lint suite:

| Tool | Install |
|------|---------|
| [bats-core](https://bats-core.readthedocs.io) | `brew install bats-core` |
| [ShellCheck](https://www.shellcheck.net) | `brew install shellcheck` |

## Running Tests

```bash
task test   # run full bats suite
task lint   # ShellCheck all shell scripts
```

Tests live in `tests/`. Each file targets a single library function or script behaviour. The shared helper `tests/test_helper.bash` provides `source_lib`, `make_ai_config`, `make_fake_binary`, and `make_git_remote`.

## Writing Tests

- Match the style of existing test files: `setup()` → `source_lib` → `@test` blocks.
- Use `run` + `$status` / `$output` for functions with side effects or exit codes.
- Call functions directly (without `run`) when asserting variable state.
- Use `TMPDIR="$(mktemp -d)"` in `setup()` and `rm -rf "$TMPDIR"` in `teardown()` for any filesystem work.
- Name tests in plain English describing the expected behaviour, e.g. `"omits a chunk that would exceed the budget"`.

When adding a new library function, add a corresponding test file `tests/<function_name>.bats`.

## Documenting Scripts

Every script and function should have a header comment that explains:
- What the script does (one line)
- Usage / arguments
- Any environment variables it reads
- Non-obvious side effects

Functions follow the pattern:
```bash
# function_name ARG — one-line description of what it does.
# Additional detail if the behaviour is non-obvious.
function_name() { ... }
```

## Component Framework

The workbench uses a two-tier component model. Use this decision rule when adding new components:

> **Will this always be applied on every machine, on every sync?** → Tier 1 (Core)
> **Should it appear in the install menu as an opt-in?** → Tier 2 (Optional)
> **Does it have idempotent operations worth re-applying on sync?** → also add `steps.sh`

Run `bin/validate-components` to check contracts for all existing components.

---

### Tier 1 — Core components

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
  steps.sh     ← defines step_ssh_config(), sync_ssh()
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
  echo; info "SSH config → ~/.ssh/"
  step_ssh_config
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}SSH setup${NC}\n"
  sync_ssh
  echo; success "SSH setup complete!"
fi
```

**Existing core components:** `bin`, `git`, `task`, `zsh`

---

### Tier 2 — Optional components

Optional components appear in the `install.sh` menu and are run interactively during first-time setup.

**Required files:**

| File | Required | Purpose |
|------|----------|---------|
| `setup.conf` | yes | metadata: `label`, `description`, `platforms`, optional `check` |
| `setup.sh` | yes | interactive install — runs as a subshell |
| `steps.sh` | if syncable | defines `sync_<name>()`, auto-discovered by `otto-workbench sync` |
| `summary.sh` | optional | defines `print_<name>_summary()`, sourced by `install.sh` post-install |

**Registration:** add the directory name to `install.components` (in desired menu order).

**The `check` field** — if defined, `install.sh` runs it before `setup.sh`. If it exits 0, the component is already configured and is skipped automatically. `WORKBENCH_DIR` and `DOTFILES_DIR` are both available in check command context:

```ini
label = My tool
description = Installs and configures my-tool
check = command -v my-tool && my-tool verify --config "$WORKBENCH_DIR/mytool/config"
```

**When to add `steps.sh` to an optional component:**
Add `steps.sh` when any part of `setup.sh`'s work can be safely re-applied without interaction (e.g., symlinking a config file, importing a color theme). If nothing is idempotent, omit `steps.sh` — its absence is an explicit signal that this component has no sync path.

`summary.sh` must contain only function definitions — no top-level execution. `install.sh` sources it after all components have run and calls `print_<name>_summary()` if defined.

**Example:** adding a tool with both interactive install and sync coverage:
```
mytool/
  setup.conf    ← label, description, platforms, optional check
  setup.sh      ← interactive install (runs as subshell)
  steps.sh      ← sync_mytool() for idempotent re-application
  summary.sh    ← print_mytool_summary() for post-install output
```

**Existing optional components:** `brew` (no sync), `docker` (has sync), `iterm` (has sync), `ai` (has sync via sub-components)

---

## Adding a Tool to the Registry

Each tooling directory has a `registry.yml` that describes the tools it provides. Add an entry whenever you add a new brew formula, bin script, or alias group:

```yaml
# brew/registry.yml
- name: ripgrep
  description: "Fast regex search tool"
  when_to_use: "Searching file contents; faster alternative to grep"
  docs: https://github.com/BurntSushi/ripgrep
```

Required fields: `name`, `description`, `when_to_use`. Optional: `usage`, `docs`.

After editing a registry, regenerate the tool context:

```bash
generate-tool-context
```

The pre-push hook enforces that `tools.generated.md` is always up to date.

## Code Conventions

- Quote all variables: `"$VAR"` not `$VAR`.
- Use `[[` instead of `[` for conditionals.
- Use `set -e` or explicit error checks in scripts.
- No magic values — use named variables or constants from `lib/constants.sh`.
- Guard clauses and early returns over nested `if` blocks.
