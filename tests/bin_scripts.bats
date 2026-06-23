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
    if ! grep -qE '^set -[A-Za-z]*e' "$f"; then
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

@test "all bash bin scripts produce help and exit 0 with -h" {
  local failures=()

  while IFS= read -r f; do
    local name output rc
    name=$(basename "$f")
    rc=0
    output=$("$f" -h 2>&1) || rc=$?
    if [[ -z "$output" ]]; then
      failures+=("$name: -h produced no output")
    fi
    if [[ $rc -ne 0 ]]; then
      failures+=("$name: -h exit $rc")
    fi
  done < <(_discover_scripts)

  if (( ${#failures[@]} > 0 )); then
    printf 'Scripts with broken -h:\n'
    printf '  %s\n' "${failures[@]}"
    return 1
  fi
}

@test "all bash bin scripts produce help with --help" {
  local failures=()

  while IFS= read -r f; do
    local name output
    name=$(basename "$f")
    output=$("$f" --help 2>&1) || true
    if [[ -z "$output" ]]; then
      failures+=("$name: --help produced no output")
    fi
  done < <(_discover_scripts)

  if (( ${#failures[@]} > 0 )); then
    printf 'Scripts with broken --help:\n'
    printf '  %s\n' "${failures[@]}"
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

