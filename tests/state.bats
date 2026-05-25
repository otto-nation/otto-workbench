#!/usr/bin/env bats
# Tests for component installation state tracking (lib/state.sh).
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_STATE="$TMPDIR/state"
  mkdir -p "$FAKE_STATE"

  # Provide the constants that state.sh requires
  export INSTALLED_STATE_FILE="$FAKE_STATE/installed.components"
  export INSTALL_YML_FILE="$FAKE_STATE/install.yml"
  export CORE_COMPONENTS="bin git zsh task"

  # Source the real state library
  . "$REPO_ROOT/lib/state.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# ─── state_record ───────────────────────────────────────────────────────────

@test "state_record adds entry to YAML file" {
  state_record "ai"
  yq -e '.components.ai' "$INSTALL_YML_FILE" &>/dev/null
}

@test "state_record is idempotent" {
  state_record "ai"
  state_record "ai"
  [[ -f "$INSTALL_YML_FILE" ]]
  yq -e '.components.ai' "$INSTALL_YML_FILE" &>/dev/null
}

@test "state_record creates parent directory" {
  rm -rf "$FAKE_STATE"
  export INSTALL_YML_FILE="$TMPDIR/nested/deep/install.yml"

  state_record "ai"
  yq -e '.components.ai' "$INSTALL_YML_FILE" &>/dev/null
}

@test "state_record skips core components" {
  state_record "bin"
  state_record "git"
  state_record "zsh"
  state_record "task"

  # No YAML file should be created for core-only records
  [[ ! -f "$INSTALL_YML_FILE" ]]
}

@test "state_record writes mise as scalar true" {
  state_record "mise"
  local val
  val=$(yq '.components.mise' "$INSTALL_YML_FILE")
  [[ "$val" == "true" ]]
}

@test "state_record writes top-level component as map" {
  state_record "docker"
  local val
  val=$(yq '.components.docker | type' "$INSTALL_YML_FILE")
  [[ "$val" == "!!map" ]]
}

# ─── state_is_installed ─────────────────────────────────────────────────────

@test "state_is_installed returns 0 for recorded entry" {
  state_record "ai"
  run state_is_installed "ai"
  [ "$status" -eq 0 ]
}

@test "state_is_installed returns 1 for missing entry" {
  state_record "ai"
  run state_is_installed "docker"
  [ "$status" -eq 1 ]
}

@test "state_is_installed returns non-zero when no state file" {
  run state_is_installed "ai"
  [ "$status" -ne 0 ]
}

@test "state_is_installed returns 0 for core components without file" {
  run state_is_installed "bin"
  [ "$status" -eq 0 ]
  run state_is_installed "git"
  [ "$status" -eq 0 ]
  run state_is_installed "zsh"
  [ "$status" -eq 0 ]
  run state_is_installed "task"
  [ "$status" -eq 0 ]
}

# ─── state_remove ────────────────────────────────────────────────────────────

@test "state_remove removes entry" {
  state_record "ai"
  state_record "docker"
  state_remove "ai"

  run state_is_installed "ai"
  [ "$status" -ne 0 ]
  run state_is_installed "docker"
  [ "$status" -eq 0 ]
}

@test "state_remove is safe when file missing" {
  run state_remove "ai"
  [ "$status" -eq 0 ]
}

# ─── state_file_exists ──────────────────────────────────────────────────────

@test "state_file_exists returns 0 when YAML file exists" {
  state_record "ai"
  run state_file_exists
  [ "$status" -eq 0 ]
}

@test "state_file_exists returns 0 when legacy file exists" {
  echo "ai" > "$INSTALLED_STATE_FILE"
  run state_file_exists
  [ "$status" -eq 0 ]
}

@test "state_file_exists returns 1 when no files exist" {
  run state_file_exists
  [ "$status" -eq 1 ]
}

# ─── Component and sub-tool entries ─────────────────────────────────────────

@test "state handles component and sub-tool entries" {
  state_record "ai"
  state_record "ai/claude"

  run state_is_installed "ai"
  [ "$status" -eq 0 ]

  run state_is_installed "ai/claude"
  [ "$status" -eq 0 ]

  # Sub-tool entry does not match sibling
  run state_is_installed "ai/serena"
  [ "$status" -eq 1 ]
}

@test "state_record sub-tool creates tools array in YAML" {
  state_record "ai/claude"

  local tools
  tools=$(yq '.components.ai.tools | length' "$INSTALL_YML_FILE")
  [[ "$tools" == "1" ]]

  local tool
  tool=$(yq '.components.ai.tools[0]' "$INSTALL_YML_FILE")
  [[ "$tool" == "claude" ]]
}

@test "state_record sub-tool is idempotent in tools array" {
  state_record "ai/claude"
  state_record "ai/claude"

  local count
  count=$(yq '.components.ai.tools | length' "$INSTALL_YML_FILE")
  [[ "$count" == "1" ]]
}

@test "state_remove sub-tool removes from tools array" {
  state_record "ai/claude"
  state_record "ai/serena"
  state_remove "ai/claude"

  run state_is_installed "ai/claude"
  [ "$status" -ne 0 ]
  run state_is_installed "ai/serena"
  [ "$status" -eq 0 ]
}

# ─── state_list ────────────────────────────────────────────────────────────

@test "state_list prints all entries" {
  state_record "ai"
  state_record "ai/claude"
  state_record "docker"

  run state_list
  [ "$status" -eq 0 ]
  echo "$output" | grep -xF "ai"
  echo "$output" | grep -xF "ai/claude"
  echo "$output" | grep -xF "docker"
}

@test "state_list returns 0 when no state file" {
  run state_list
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# ─── state_detect_installed ────────────────────────────────────────────────

@test "state_detect_installed detects optional components by heuristic" {
  HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  . "$REPO_ROOT/lib/ui.sh"

  # Set up ghostty
  mkdir -p "$GHOSTTY_CONFIG_DIR"

  state_detect_installed

  run state_is_installed "terminals"
  [ "$status" -eq 0 ]
  run state_is_installed "terminals/ghostty"
  [ "$status" -eq 0 ]

  # Docker not set up — should not be detected
  run state_is_installed "docker"
  [ "$status" -ne 0 ]
}

# ─── state_prune_orphans ──────────────────────────────────────────────────

@test "state_prune_orphans removes entries with no step file" {
  HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/lib/components.sh"

  state_record "docker"
  # Record a fake entry directly in YAML
  yq -i '.components.nonexistent = {}' "$INSTALL_YML_FILE"

  state_prune_orphans

  run state_is_installed "docker"
  [ "$status" -eq 0 ]

  # Orphan should be removed
  local val
  val=$(yq '.components.nonexistent // "gone"' "$INSTALL_YML_FILE")
  [[ "$val" == "gone" ]]
}

@test "state_prune_orphans is safe with no state file" {
  HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/lib/components.sh"

  run state_prune_orphans
  [ "$status" -eq 0 ]
}

@test "state_prune_orphans keeps all valid entries" {
  HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/lib/components.sh"

  state_record "ai"
  state_record "ai/claude"

  state_prune_orphans

  run state_is_installed "ai"
  [ "$status" -eq 0 ]
  run state_is_installed "ai/claude"
  [ "$status" -eq 0 ]
}

# ─── state_set / state_get ──────────────────────────────────────────────────

@test "state_set writes a value to YAML" {
  state_set "docker.runtime" "orbstack"
  local val
  val=$(yq '.components.docker.runtime' "$INSTALL_YML_FILE")
  [[ "$val" == "orbstack" ]]
}

@test "state_get reads a value from YAML" {
  state_set "docker.runtime" "colima"
  run state_get "docker.runtime"
  [[ "$output" == "colima" ]]
}

@test "state_get returns empty for missing key" {
  echo "components: {}" > "$INSTALL_YML_FILE"
  run state_get "docker.runtime"
  [[ -z "$output" ]]
}

# ─── state_append_list / state_get_list ──────────────────────────────────────

@test "state_append_list adds items to a YAML list" {
  state_append_list "brew.stacks" "infra/kubernetes"
  state_append_list "brew.stacks" "lang/go"

  local count
  count=$(yq '.components.brew.stacks | length' "$INSTALL_YML_FILE")
  [[ "$count" == "2" ]]
}

@test "state_append_list is idempotent" {
  state_append_list "brew.stacks" "infra/kubernetes"
  state_append_list "brew.stacks" "infra/kubernetes"

  local count
  count=$(yq '.components.brew.stacks | length' "$INSTALL_YML_FILE")
  [[ "$count" == "1" ]]
}

@test "state_get_list reads a YAML list" {
  state_append_list "brew.stacks" "infra/kubernetes"
  state_append_list "brew.stacks" "lang/go"

  run state_get_list "brew.stacks"
  [[ "$output" == *"infra/kubernetes"* ]]
  [[ "$output" == *"lang/go"* ]]
}

@test "state_get_list returns empty for missing key" {
  echo "components: {}" > "$INSTALL_YML_FILE"
  run state_get_list "brew.stacks"
  [[ -z "$output" ]]
}
