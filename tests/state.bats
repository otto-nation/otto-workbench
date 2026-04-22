#!/usr/bin/env bats
# Tests for component installation state tracking (lib/state.sh).
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
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
