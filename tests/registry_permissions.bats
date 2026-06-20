#!/usr/bin/env bats
# Tests for collect_registry_permissions — extracts Bash(...) permission
# patterns from registry permission fields.

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

# ── permission: true ──────────────────────────────────────────────────────────

@test "permission: true generates Bash(name:*) from tool name" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: mytool
    permission: true
    description: "A tool"
    when_to_use: "Testing"
    usage: "mytool --help"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 1 ]]
  [[ "${perms[0]}" == "Bash(mytool:*)" ]]
}

# ── permission: false ─────────────────────────────────────────────────────────

@test "permission: false generates nothing" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: mytool
    permission: false
    description: "A tool"
    when_to_use: "Testing"
    usage: "mytool --help"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 0 ]]
}

# ── permission omitted ────────────────────────────────────────────────────────

@test "omitted permission generates nothing" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: mytool
    description: "A tool"
    when_to_use: "Testing"
    usage: "mytool --help"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 0 ]]
}

# ── permission: "cmd" (string) ───────────────────────────────────────────────

@test "permission: string generates Bash(cmd:*) with the string value" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: bats-core
    permission: "bats"
    description: "Testing framework"
    when_to_use: "Testing"
    usage: "bats tests/"'

  local -a perms=()
  collect_registry_permissions perms "$TMPDIR"
  [[ "${#perms[@]}" -eq 1 ]]
  [[ "${perms[0]}" == "Bash(bats:*)" ]]
}

# ── permission: [array] ──────────────────────────────────────────────────────

@test "permission: array uses verbatim entries" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: gh
    permission:
      - "Bash(gh pr:*)"
      - "Bash(gh issue:*)"
    description: "GitHub CLI"
    when_to_use: "GitHub"
    usage: "gh pr create"'

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
    permission: true
    description: "Tool A"
    when_to_use: "Always"
    usage: "tool-a --help"
  - name: tool-b
    description: "Tool B (no permission)"
    when_to_use: "Never"
    usage: "tool-b --help"'

  _write_registry "$TMPDIR/comp2" 'meta:
  section: Second
  install_check: false
  validation: none
tools:
  - name: tool-c
    permission: "tc"
    description: "Tool C"
    when_to_use: "Sometimes"
    usage: "tc --help"'

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

@test "permission: empty string generates nothing" {
  _write_registry "$TMPDIR/comp" 'meta:
  section: Test
  install_check: false
  validation: none
tools:
  - name: mytool
    permission: ""
    description: "A tool"
    when_to_use: "Testing"
    usage: "mytool --help"'

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
