---
paths:
  - "**/*.sh"
---

# Bash / Shell

## Shebang & Invocation
- Always use `#!/usr/bin/env bash` (not `#!/bin/bash`) — picks up Homebrew's modern bash on macOS
- Bash 4.3+ is required (namerefs, associative arrays)
- Never invoke scripts with `bash script.sh` — run them directly (`./script.sh` or `"$path/script.sh"`) so their shebang is honored

## Code Style
- Use `set -e` in all scripts
- Use `[[` instead of `[` for conditionals
- Quote all variables: `"$VAR"` not `$VAR`
- Use meaningful argument names — not `$1`, `$2`
- Guard clauses and early returns over nested `if` blocks

## Best Practices
- Scripts in `bin/` should include usage documentation (what, usage, env vars, side effects)
- Validate required arguments
- Use functions for reusable logic
- Under `set -e`, commands that return non-zero on no-match (grep, find, diff) must be guarded with `|| true` or wrapped in `if`/`while` — unguarded usage causes silent script exits
- Return values via `local -n` (nameref), never `printf -v` — `printf -v "$var"` silently writes to a same-named `local` in the current scope instead of the caller's variable. Use `local -n __out=$1` and assign `__out="value"`. The `__` prefix prevents collisions
- All scripts source `lib/ui.sh` via the `_SELF` readlink pattern for portability
- All setup scripts, sync functions, and migrations must be idempotent — safe to re-run with no side effects
