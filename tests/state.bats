#!/usr/bin/env bats
# Tests for component installation state tracking (lib/state.sh).
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_STATE="$TMPDIR/state"
  mkdir -p "$FAKE_STATE"

  # Provide the constant that state.sh requires
  export INSTALLED_STATE_FILE="$FAKE_STATE/installed.components"

  # Source the real state library
  . "$REPO_ROOT/lib/state.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# ─── state_record ───────────────────────────────────────────────────────────

@test "state_record adds entry to state file" {
  state_record "ai"
  grep -qxF "ai" "$INSTALLED_STATE_FILE"
}

@test "state_record is idempotent" {
  state_record "ai"
  state_record "ai"
  local count
  count=$(grep -cxF "ai" "$INSTALLED_STATE_FILE")
  [ "$count" -eq 1 ]
}

@test "state_record creates parent directory" {
  rm -rf "$FAKE_STATE"
  export INSTALLED_STATE_FILE="$TMPDIR/nested/deep/installed.components"

  state_record "git"
  grep -qxF "git" "$INSTALLED_STATE_FILE"
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

# ─── state_remove ────────────────────────────────────────────────────────────

@test "state_remove removes entry" {
  state_record "ai"
  state_record "docker"
  state_remove "ai"

  run ! grep -qxF "ai" "$INSTALLED_STATE_FILE"
  grep -qxF "docker" "$INSTALLED_STATE_FILE"
}

@test "state_remove is safe when file missing" {
  run state_remove "ai"
  [ "$status" -eq 0 ]
}

# ─── state_file_exists ──────────────────────────────────────────────────────

@test "state_file_exists returns 0 when file exists" {
  state_record "ai"
  run state_file_exists
  [ "$status" -eq 0 ]
}

@test "state_file_exists returns 1 when file missing" {
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

  # Sub-tool entry does not match parent
  run state_is_installed "ai/serena"
  [ "$status" -eq 1 ]
}

# ─── state_list ────────────────────────────────────────────────────────────

@test "state_list prints all entries" {
  state_record "bin"
  state_record "ai"
  state_record "ai/claude"

  run state_list
  [ "$status" -eq 0 ]
  [[ "$output" == *"bin"* ]]
  [[ "$output" == *"ai"* ]]
  [[ "$output" == *"ai/claude"* ]]
}

@test "state_list returns 0 when no state file" {
  run state_list
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# ─── state_detect_installed ────────────────────────────────────────────────

@test "state_detect_installed records core components" {
  # Need constants for detection heuristics — source ui.sh with fake HOME
  HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  . "$REPO_ROOT/lib/ui.sh"

  state_detect_installed

  run state_is_installed "bin"
  [ "$status" -eq 0 ]
  run state_is_installed "git"
  [ "$status" -eq 0 ]
  run state_is_installed "zsh"
  [ "$status" -eq 0 ]
}

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

  # Record a real component and a fake one
  state_record "bin"
  state_record "nonexistent/tool"

  state_prune_orphans

  run state_is_installed "bin"
  [ "$status" -eq 0 ]
  run state_is_installed "nonexistent/tool"
  [ "$status" -ne 0 ]
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

  state_record "bin"
  state_record "git"
  state_record "ai"
  state_record "ai/claude"

  state_prune_orphans

  # All are valid components with step files
  run state_is_installed "bin"
  [ "$status" -eq 0 ]
  run state_is_installed "git"
  [ "$status" -eq 0 ]
  run state_is_installed "ai"
  [ "$status" -eq 0 ]
  run state_is_installed "ai/claude"
  [ "$status" -eq 0 ]
}
