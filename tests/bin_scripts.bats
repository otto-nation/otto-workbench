#!/usr/bin/env bats
# Tests for bin script conventions.
# Dynamically discovers all bash scripts in */bin/ directories and validates
# that they adhere to basic requirements: shebang, set -e, help flags.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# Collect all bash bin scripts, excluding non-user-invocable files.
_discover_scripts() {
  local scripts=()
  local f shebang

  while IFS= read -r f; do
    # Skip non-user-invocable scripts
    [[ "$f" == */migrations/* ]] && continue
    [[ "$f" == */steps.sh ]] && continue
    [[ "$(basename "$f")" == "otto-workbench-autoupdate" ]] && continue

    shebang=$(head -1 "$f" 2>/dev/null)
    [[ "$shebang" == "#!/usr/bin/env bash" ]] || continue

    scripts+=("$f")
  done < <(find "$REPO_ROOT" -type f -perm +111 -path '*/bin/*' \
    ! -path '*/__pycache__/*' ! -name '*.pyc' | sort)

  printf '%s\n' "${scripts[@]}"
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
    if ! grep -qE '^set -e' "$f"; then
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

@test "all bash bin scripts produce help with -h" {
  local failures=()

  while IFS= read -r f; do
    local name output
    name=$(basename "$f")
    output=$("$f" -h 2>&1) || true
    if [[ -z "$output" ]]; then
      failures+=("$name: -h produced no output")
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

@test "all bash bin scripts exit 0 on -h" {
  local failures=()

  while IFS= read -r f; do
    local name rc
    name=$(basename "$f")
    "$f" -h &>/dev/null
    rc=$?
    if [[ $rc -ne 0 ]]; then
      failures+=("$name: exit $rc")
    fi
  done < <(_discover_scripts)

  if (( ${#failures[@]} > 0 )); then
    printf 'Scripts with non-zero exit on -h:\n'
    printf '  %s\n' "${failures[@]}"
    return 1
  fi
}
