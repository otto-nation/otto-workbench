#!/usr/bin/env bats
# Tests for bin script conventions.
# Dynamically discovers all bash scripts in */bin/ directories and validates
# that they adhere to basic requirements: shebang, set -e, help flags.

bats_require_minimum_version 1.5.0

setup_file() {
  load 'test_helper'
  local repo_root
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

  # Discover scripts once for all tests in this file
  local f shebang
  while IFS= read -r f; do
    [[ -x "$f" ]] || continue
    [[ "$f" == */migrations/* ]] && continue
    [[ "$f" == */steps.sh ]] && continue
    [[ "$(basename "$f")" == "otto-workbench-maintenance" ]] && continue
    shebang=$(head -1 "$f" 2>/dev/null)
    [[ "$shebang" == "#!/usr/bin/env bash" ]] || continue
    printf '%s\n' "$f"
  done < <(find "$repo_root" -type f -path '*/bin/*' \
    ! -path '*/__pycache__/*' ! -name '*.pyc' | sort) > "$BATS_FILE_TMPDIR/scripts.list"
}

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# Read cached script list (populated by setup_file)
_discover_scripts() {
  cat "$BATS_FILE_TMPDIR/scripts.list"
}

# ─── Shebang ─────────────────────────────────────────────────────────────────

@test "all bash bin scripts use #!/usr/bin/env bash shebang" {
  local failures=()

  while IFS= read -r f; do
    local shebang
    shebang=$(head -1 "$f")
    if [[ "$shebang" != "#!/usr/bin/env bash" ]]; then
      failures+=("$(basename "$f"): got '$shebang'")
    fi
  done < <(_discover_scripts)

  if (( ${#failures[@]} > 0 )); then
    printf 'Missing #!/usr/bin/env bash:\n'
    printf '  %s\n' "${failures[@]}"
    return 1
  fi
}

# ─── set -e ──────────────────────────────────────────────────────────────────

@test "all bash bin scripts use set -e" {
  local failures=()

  while IFS= read -r f; do
    if ! grep -qE '^[[:space:]]*set -[A-Za-z]*e' "$f"; then
      failures+=("$(basename "$f")")
    fi
  done < <(_discover_scripts)

  if (( ${#failures[@]} > 0 )); then
    printf 'Missing set -e:\n'
    printf '  %s\n' "${failures[@]}"
    return 1
  fi
}

# ─── Help flags ──────────────────────────────────────────────────────────────

# _help_run SCRIPT FLAG — SCRIPT's help output, asked the way a stranger would.
#
# The PATH is cut back to the system directories on purpose. A help flag must be
# answered before the script reaches for anything, and these suites otherwise
# report on the developer's install rather than on the script: run-due-auto-tasks
# shipped with no handler at all, so `-h` fell through to a live run, found
# `claude` on the author's PATH, logged a line and exited 0 — which satisfied
# both cases below. CI has no `claude`, the identical path exited 69 with an
# empty stdout, and the suite failed there and only there.
#
# Homebrew's bin stays on the list because lib/roots.sh uses `declare -g` and
# macOS ships bash 3.2 at /bin/bash: dropping it tests the OS's bash, not the
# script. HOME is kept for the same reason a real invocation has one.
_help_run() {
  env -i HOME="$HOME" PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    "$1" "$2" 2>&1
}

# _help_failures FLAG [--check-status] — the scripts whose FLAG is broken.
#
# Each script is asked independently and nothing is shared between them, so the
# ~150 invocations run concurrently rather than one after another: the two
# cases below spawned that many processes serially and cost ~9.4s each.
#
# `xargs -P` over a helper that prints one line per bad script, rather than a
# shell loop — the findings come back on stdout, so a worker exiting non-zero
# cannot lose a name. The exit status is deliberately not read: a script whose
# -h fails is reported as a finding, which is what the caller asserts on.
_help_failures() {
  local flag="$1" check_status="${2:-}"
  export -f _help_run
  # shellcheck disable=SC2016  # $0/$1 are the worker's, expanded by bash -c
  _discover_scripts | xargs -P 8 -I {} bash -c '
    flag="$0"; check_status="$1"; script="$2"
    name=$(basename "$script")
    rc=0
    output=$(_help_run "$script" "$flag") || rc=$?
    [[ -n "$output" ]] || printf "%s: %s produced no output\n" "$name" "$flag"
    if [[ -n "$check_status" && $rc -ne 0 ]]; then
      printf "%s: %s exit %s\n" "$name" "$flag" "$rc"
    fi
  ' "$flag" "$check_status" {}
}

@test "all bash bin scripts produce help and exit 0 with -h" {
  local failures
  failures=$(_help_failures -h --check-status)

  if [[ -n "$failures" ]]; then
    printf 'Scripts with broken -h:\n'
    printf '  %s\n' "$failures"
    return 1
  fi
}

@test "all bash bin scripts produce help with --help" {
  local failures
  failures=$(_help_failures --help)

  if [[ -n "$failures" ]]; then
    printf 'Scripts with broken --help:\n'
    printf '  %s\n' "$failures"
    return 1
  fi
}

# ─── Command documentation ───────────────────────────────────────────────────

# _extract_command_names FILE ARRAY_NAME
# Extracts command names from a COMMANDS-style array declaration.
# Returns the first word of each usage form (the bare command name).
_extract_command_names() {
  local file="$1" array_name="$2"
  local in_block=false line

  while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*${array_name}=\( ]]; then
      in_block=true
      continue
    fi
    [[ "$in_block" == true ]] || continue
    [[ "$line" =~ ^\) ]] && break
    if [[ "$line" =~ ^[[:space:]]*\"([^\"]+)\" ]]; then
      local usage_form="${BASH_REMATCH[1]}"
      echo "${usage_form%% *}"
    fi
  done < "$file"
}

# _extract_commands_arrays FILE
# Lists all COMMANDS-style array names declared in a file.
_extract_commands_arrays() {
  grep -oE '^[[:space:]]*COMMANDS(_[A-Z_]+)?=' "$1" | sed 's/^[[:space:]]*//; s/=//' | sort -u
}

# _check_commands_for_file FILE FAILURES_ARRAYNAME
# Validates cmd_* ↔ COMMANDS consistency for a single script.
_check_commands_for_file() {
  local file="$1"
  local -n __failures="$2"
  local name
  name=$(basename "$file")

  local -a cmd_functions=()
  local fn
  while IFS= read -r fn; do
    cmd_functions+=("$fn")
  done < <(grep -oE '^cmd_[a-z0-9_]+' "$file" | sed 's/^cmd_//' | sort -u)

  [[ ${#cmd_functions[@]} -gt 0 ]] || return 0

  local -a registered=()
  local array_name prefix cmd_name
  while IFS= read -r array_name; do
    [[ -n "$array_name" ]] || continue
    prefix=""
    if [[ "$array_name" != "COMMANDS" ]]; then
      prefix="${array_name#COMMANDS_}"
      prefix="${prefix,,}_"
    fi
    while IFS= read -r cmd_name; do
      [[ -n "$cmd_name" ]] || continue
      registered+=("${prefix}${cmd_name}")
    done < <(_extract_command_names "$file" "$array_name")
  done < <(_extract_commands_arrays "$file")

  local -A cmd_set=() reg_set=()
  for fn in "${cmd_functions[@]}"; do cmd_set["$fn"]=1; done
  for fn in "${registered[@]}"; do reg_set["$fn"]=1; done

  for fn in "${registered[@]}"; do
    [[ -n "${cmd_set[$fn]:-}" ]] || __failures+=("$name: COMMANDS entry '$fn' has no cmd_${fn}() function")
  done
  for fn in "${cmd_functions[@]}"; do
    [[ -n "${reg_set[$fn]:-}" ]] || __failures+=("$name: cmd_${fn}() not in any COMMANDS array")
  done
}

@test "all cmd_* functions have matching COMMANDS entries" {
  local failures=()

  while IFS= read -r f; do
    _check_commands_for_file "$f" failures
  done < <(_discover_scripts)

  if (( ${#failures[@]} > 0 )); then
    printf 'Command documentation drift:\n'
    printf '  %s\n' "${failures[@]}"
    return 1
  fi
}

