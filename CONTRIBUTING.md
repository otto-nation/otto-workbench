# Contributing

## Setup

```bash
git clone https://github.com/otto-nation/otto-workbench ~/otto-workbench
cd ~/otto-workbench
task dev:setup
```

`task dev:setup` activates the git hooks in `git/hooks/`, which run ShellCheck, validate registries, and regenerate tool context before every push.

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

- Match the style of existing test files: `setup()` -> `source_lib` -> `@test` blocks.
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

## Adding a Component

See the [Component Framework](docs/components.md) reference for the full Tier 1/Tier 2 contract, required files, examples, and the `sync_<name>()` contract.

## Adding a Tool to the Registry

See [Registries](docs/registries.md#adding-an-entry) for the full schema, validation modes, and step-by-step instructions.

## Environment Variables

| Variable | Where set | Effect |
|----------|-----------|--------|
| `SYMLINK_MODE=no-prompt` | `bin/otto-workbench sync` | Skips the interactive overwrite prompt in `install_symlink` — real files at the target path are warned about and skipped instead of prompting |
| `NO_COLOR` | shell environment | Disables all ANSI color output from `lib/ui.sh` helpers (follows [no-color.org](https://no-color.org)) |
| `WORKBENCH_DIR` | auto-derived or caller | Override the repo root; set by `install.sh` and auto-derived from `lib/constants.sh` otherwise |

## Code Conventions

- Quote all variables: `"$VAR"` not `$VAR`.
- Use `[[` instead of `[` for conditionals.
- Use `set -e` or explicit error checks in scripts.
- No magic values — use named variables or constants from [`lib/constants.sh`](lib/constants.sh).
- Guard clauses and early returns over nested `if` blocks.
- Colors: `RED`=error `YELLOW`=warn `GREEN`=success `BLUE`=info `CYAN`=section label `DIM`=metadata.
