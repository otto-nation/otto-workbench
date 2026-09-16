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
- A value-taking flag must check its value is present before `shift 2` — `[[ -n "${2:-}" ]] || { echo "Error: $1 requires an argument" >&2; exit 2; }`. Without the check, `shift 2` fails and `set -e` exits 1 with no diagnostic
- Use functions for reusable logic
- Scripts should be quiet on success — minimal output (single status line or nothing). On failure: full diagnostic output (what failed, where, relevant context)
- Under `set -e`, commands that return non-zero on no-match (grep, find, diff) must be guarded with `|| true` or wrapped in `if`/`while` — unguarded usage causes silent script exits
- `while read ...; done < <(cmd)` does not propagate `cmd`'s exit status under `set -e` — a failing `cmd` is empty input, and the script exits 0 having done nothing. Capture into a variable or file first (`output=$(cmd)` or `cmd > "$tmp"`) so the failure is seen before the loop
- `while IFS= read -r line; do ...; done < "$file"` drops a final line that has no trailing newline — `read` returns non-zero at EOF, so the loop body never runs for that line. Write `while IFS= read -r line || [[ -n "$line" ]]`
- Register every `mktemp` file or directory with the script's `EXIT` trap at the point it is created — an `rm` at the end of the success path leaks on any early return or `set -e` exit, which is the path that matters
- **Function-last-statement pitfall**: `[[ condition ]] && cmd` as the final statement of a function returns exit code 1 when the condition is false — the `[[ ]]` is exempt from `set -e`, but the function's return code propagates to the caller and triggers `set -e` there. Fix: end the function with `return 0`, or use `if/then/fi` instead of `&&`
- Return values via `local -n` (nameref), never `printf -v` — `printf -v "$var"` silently writes to a same-named `local` in the current scope instead of the caller's variable. Use `local -n __out=$1` and assign `__out="value"`. The `__` prefix prevents collisions
- All scripts source `lib/ui.sh` via `git rev-parse --show-toplevel` — depth-independent, no `../` paths. Bin scripts that may be symlinked resolve with `readlink` first, then use `git -C` on the resolved directory
- All setup scripts, sync functions, and migrations must be idempotent — safe to re-run with no side effects

## Portability
- Target macOS BSD userland — avoid GNU-specific flags and syntax
- `sed -i ''` (BSD) not `sed -i` (GNU) — or use `sed ... > tmp && mv tmp file` for full portability
- `find . -perm +111` (BSD) not `find . -perm /111` (GNU) — or use `test -x` per-file
- `grep -P` (PCRE) is unavailable on BSD — use `grep -E` (extended regex) instead
- `date` flags differ — avoid GNU-only formats; use `date -u` for UTC
- Never call `stat` with a format flag (`-c`, `-f`, `--format`, `--printf`) — use `file_mtime`, `file_birth`, or `file_mode` from `lib/portable.sh`. GNU reads `-f` as `--file-system`, so it prints a filesystem report to stdout *before* failing; a hand-rolled `$(gnu_form || bsd_form)` chain concatenates both outputs. Enforced by `bin/local/validate-stat-portability`
- On macOS `/tmp` and `/var` are symlinks to `/private/tmp` and `/private/var` — anything that canonicalizes (`git rev-parse --show-toplevel`, `realpath`) returns the `/private/...` form. Resolve both sides before comparing paths or matching a prefix
