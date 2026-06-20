#!/usr/bin/env bats
# Tests for collect_registry_permissions — extracts Bash(...) permission
# patterns from registry allow fields.

setup_file() {
  load 'test_helper'
  local repo_root
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

  # shellcheck source=/dev/null
  source "$repo_root/lib/registries.sh"

  # Collect real-registry permissions once for tests that scan REPO_ROOT
  local -a perms=()
  collect_registry_permissions perms "$repo_root"
  printf '%s\n' "${perms[@]}" > "$BATS_FILE_TMPDIR/real_perms.list"
}

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"

  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/registries.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

_write_registry() {
  local dir="$1" content="$2"
  mkdir -p "$dir"
  printf '%s\n' "$content" > "$dir/registry.yml"
}

# ── allow: true ──────────────────────────────────────────────────────────────

@test "allow: true generates Bash(name:*) from tool name" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: mytool
    allow: true
    description: "A tool"
    when_to_use: "Testing"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 1 ]]
  [[ "${perms[0]}" == "Bash(mytool:*)" ]]
}

# ── allow: false ─────────────────────────────────────────────────────────────

@test "allow: false generates nothing" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: mytool
    allow: false
    description: "A tool"
    when_to_use: "Testing"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 0 ]]
}

# ── allow omitted ────────────────────────────────────────────────────────────

@test "omitted allow generates nothing" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: mytool
    description: "A tool"
    when_to_use: "Testing"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 0 ]]
}

# ── allow: "cmd" (string) ───────────────────────────────────────────────────

@test "allow: string generates Bash(cmd:*) with the string value" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: bats-core
    allow: "bats"
    description: "Testing framework"
    when_to_use: "Testing"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 1 ]]
  [[ "${perms[0]}" == "Bash(bats:*)" ]]
}

# ── allow: [array] ──────────────────────────────────────────────────────────

@test "allow: array uses verbatim entries" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: gh
    allow:
      - "Bash(gh pr:*)"
      - "Bash(gh issue:*)"
    description: "GitHub CLI"
    when_to_use: "GitHub"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 2 ]]
  [[ "${perms[0]}" == "Bash(gh pr:*)" ]]
  [[ "${perms[1]}" == "Bash(gh issue:*)" ]]
}

# ── Multiple tools and registries ────────────────────────────────────────────

@test "accumulates permissions from multiple tools across registries" {
  _write_registry "$TMPDIR/comp1" 'meta:
  section: First
  install_check: false
  validation: none
tools:
  - name: tool-a
    allow: true
    description: "Tool A"
    when_to_use: "Always"
  - name: tool-b
    description: "Tool B (no allow)"
    when_to_use: "Never"'

  _write_registry "$TMPDIR/comp2" 'meta:
  section: Second
  install_check: false
  validation: none
tools:
  - name: tool-c
    allow: "tc"
    description: "Tool C"
    when_to_use: "Sometimes"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 2 ]]
  local found_a=0 found_tc=0
  for p in "${perms[@]}"; do
    case "$p" in
      "Bash(tool-a:*)") found_a=1 ;;
      "Bash(tc:*)")     found_tc=1 ;;
    esac
  done
  [[ "$found_a" -eq 1 ]]
  [[ "$found_tc" -eq 1 ]]
}

# ── Empty registries ────────────────────────────────────────────────────────

@test "allow: empty string generates nothing" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: mytool
    allow: ""
    description: "A tool"
    when_to_use: "Testing"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 0 ]]
}

# ── Empty registries ────────────────────────────────────────────────────────

@test "empty scan directory produces empty output" {
  local empty_dir
  empty_dir="$(mktemp -d "$TMPDIR/empty-XXXXX")"

  local -a perms=()
  collect_registry_permissions perms "$empty_dir"
  [[ "${#perms[@]}" -eq 0 ]]
}

# ── Real registries ─────────────────────────────────────────────────────────

@test "real registries produce expected permissions" {
  local -a perms=()
  mapfile -t perms < "$BATS_FILE_TMPDIR/real_perms.list"

  # Spot-check a few expected entries
  local found_task=0 found_wt=0 found_bats=0 found_gh_pr=0
  for p in "${perms[@]}"; do
    case "$p" in
      "Bash(task:*)")    found_task=1 ;;
      "Bash(wt:*)")      found_wt=1 ;;
      "Bash(bats:*)")    found_bats=1 ;;
      "Bash(gh pr:*)")   found_gh_pr=1 ;;
    esac
  done
  [[ "$found_task" -eq 1 ]]
  [[ "$found_wt" -eq 1 ]]
  [[ "$found_bats" -eq 1 ]]
  [[ "$found_gh_pr" -eq 1 ]]
}

@test "dangerous tools do not have broad Bash wildcard" {
  local -a perms=()
  mapfile -t perms < "$BATS_FILE_TMPDIR/real_perms.list"

  for p in "${perms[@]}"; do
    [[ "$p" != "Bash(docker:*)" ]] || { echo "docker should use scoped subcommands, not broad wildcard"; return 1; }
    [[ "$p" != "Bash(get-secret:*)" ]] || { echo "get-secret should not be allowed"; return 1; }
  done
}
