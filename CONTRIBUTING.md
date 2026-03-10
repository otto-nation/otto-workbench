# Contributing

## Setup

Clone the repo and activate the pre-push hook:

```bash
task dev:setup
```

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

## Adding a Component

Register the component in `install.components`, then create a directory with:

| File | Required | Purpose |
|------|----------|---------|
| `setup.conf` | yes | metadata: `label`, `description`, `platforms` |
| `setup.sh` | yes | imperative — does the installation work |
| `summary.sh` | optional | defines `print_<name>_summary()`, sourced by `install.sh` post-install |

`summary.sh` must contain only function definitions — no top-level execution. `install.sh` sources it after all components have run and calls `print_<name>_summary()` if defined. This mirrors the `print_<tool>_summary()` pattern used by `ai/setup.sh`.

## Code Conventions

- Quote all variables: `"$VAR"` not `$VAR`.
- Use `[[` instead of `[` for conditionals.
- Use `set -e` or explicit error checks in scripts.
- No magic values — use named variables or constants.
- Guard clauses and early returns over nested `if` blocks.
