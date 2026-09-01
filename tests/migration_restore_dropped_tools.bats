#!/usr/bin/env bats
# Tests for the migration that restores tool selections an interrupted install dropped.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  # Source libs with fake HOME so all constants resolve there — including the
  # state root, which is what INSTALL_YML_FILE hangs off.
  HOME="$FAKE_HOME"
  export WORKBENCH_STATE_DIR="$FAKE_HOME/.local/state/workbench"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"
  # The migration returns the framework's own statuses, so the numbers stay
  # owned by lib/migrations.sh.
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/migrations.sh"
  # shellcheck source=/dev/null
  source "$REPO_ROOT/bin/migrations/20260901-restore-dropped-tool-selections.sh"

  mkdir -p "$(dirname "$CLAUDE_SETTINGS_FILE")"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _claude_is_installed — the state the detector reads for the ai/claude entry.
_claude_is_installed() {
  echo '{}' > "$CLAUDE_SETTINGS_FILE"
}

@test "no install.yml defers rather than recording a repair it did not make" {
  [[ ! -f "$INSTALL_YML_FILE" ]]

  run migration_20260901_restore_dropped_tool_selections
  [[ "$status" -eq "$MIGRATION_DEFERRED" ]]
}

@test "a list that never lost anything is a no-op" {
  _claude_is_installed
  state_record "ai"
  state_record "ai/claude"

  run migration_20260901_restore_dropped_tool_selections
  [[ "$status" -eq "$MIGRATION_NOOP" ]]
}

@test "a tool dropped from the list is restored and named" {
  # The reported failure: ai.tools was emptied to ask about a newly added tool,
  # the install ended before answering, and claude never synced again.
  _claude_is_installed
  state_record "ai"
  state_set_list "ai.tools" "pi"

  run migration_20260901_restore_dropped_tool_selections
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"ai/claude"* ]]

  run state_get_list "ai.tools"
  [[ "$output" == *"claude"* ]]
}

@test "restoring keeps the tools already recorded" {
  # pi is not something the detector knows about, so a repair that rebuilt the
  # list instead of merging into it would drop pi on the way past.
  _claude_is_installed
  state_record "ai"
  state_set_list "ai.tools" "pi"

  migration_20260901_restore_dropped_tool_selections

  run state_get_list "ai.tools"
  [[ "$output" == *"pi"* ]]
  [[ "$output" == *"claude"* ]]
}

@test "an emptied list is restored" {
  _claude_is_installed
  state_record "ai"
  state_set_list "ai.tools"

  run migration_20260901_restore_dropped_tool_selections
  [[ "$status" -eq 0 ]]

  run state_get_list "ai.tools"
  [[ "$output" == *"claude"* ]]
}

@test "a tool that is not installed is not invented" {
  # No settings file, so nothing detects claude — the migration must not add a
  # tool back purely because the list looks short.
  state_record "ai"
  state_set_list "ai.tools" "pi"

  run migration_20260901_restore_dropped_tool_selections
  [[ "$status" -eq "$MIGRATION_NOOP" ]]

  run state_get_list "ai.tools"
  [[ "$output" != *"claude"* ]]
}

@test "the repair is idempotent" {
  _claude_is_installed
  state_record "ai"
  state_set_list "ai.tools" "pi"

  migration_20260901_restore_dropped_tool_selections
  run migration_20260901_restore_dropped_tool_selections
  [[ "$status" -eq "$MIGRATION_NOOP" ]]
}
