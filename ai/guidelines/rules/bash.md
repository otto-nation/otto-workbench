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
- Scripts should be quiet on success — minimal output (single status line or nothing). On failure: full diagnostic output (what failed, where, relevant context)
- Under `set -e`, commands that return non-zero on no-match (grep, find, diff) must be guarded with `|| true` or wrapped in `if`/`while` — unguarded usage causes silent script exits
- **Function-last-statement pitfall**: `[[ condition ]] && cmd` as the final statement of a function returns exit code 1 when the condition is false — the `[[ ]]` is exempt from `set -e`, but the function's return code propagates to the caller and triggers `set -e` there. Fix: end the function with `return 0`, or use `if/then/fi` instead of `&&`
- Return values via `local -n` (nameref), never `printf -v` — `printf -v "$var"` silently writes to a same-named `local` in the current scope instead of the caller's variable. Use `local -n __out=$1` and assign `__out="value"`. The `__` prefix prevents collisions
- All scripts source `lib/ui.sh` via `git rev-parse --show-toplevel` — depth-independent, no `../` paths. Bin scripts that may be symlinked resolve with `readlink` first, then use `git -C` on the resolved directory
- All setup scripts, sync functions, and migrations must be idempotent — safe to re-run with no side effects

## Script Invocation
- Never invoke scripts by absolute path — use the relative path from the repo root (`bin/local/validate-skills`, not `/Users/.../bin/local/validate-skills`). Permission rules match the first word of the command; an absolute path triggers a permission prompt every time

## Portability
- Target macOS BSD userland — avoid GNU-specific flags and syntax
- `sed -i ''` (BSD) not `sed -i` (GNU) — or use `sed ... > tmp && mv tmp file` for full portability
- `find . -perm +111` (BSD) not `find . -perm /111` (GNU) — or use `test -x` per-file
- `grep -P` (PCRE) is unavailable on BSD — use `grep -E` (extended regex) instead
- `date` flags differ — avoid GNU-only formats; use `date -u` for UTC
