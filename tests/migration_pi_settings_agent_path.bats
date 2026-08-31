#!/usr/bin/env bats
# Tests for ai/pi/migrations/20260831-pi-settings-agent-path.sh — drains the Pi
# settings file an earlier sync wrote to ~/.pi/settings.json, a path Pi never read.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/pi/migrations/20260831-pi-settings-agent-path.sh"
  TMPDIR="$(mktemp -d)"
  LEGACY="$TMPDIR/pi/settings.json"
  TEMPLATE="$TMPDIR/template.json"
  mkdir -p "$TMPDIR/pi"
  printf '{\n  "defaultModel": "claude-opus-4-6"\n}\n' > "$TEMPLATE"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Runs the migration with the ui.sh helpers stubbed out. Sources the real
# lib/migrations.sh for MIGRATION_NOOP. Exit status is the function's own: the
# framework reads it to tell a drained file (0) from a machine that never had
# one (MIGRATION_NOOP).
#
# LEGACY_WORKBENCH_ROOT is set because lib/migrations.sh reads it while being
# sourced; the migration itself never touches it, and the path deliberately does
# not exist. Without it the source fails and every case answers 0 rather than
# MIGRATION_NOOP, which reads as a migration that did work on an empty machine.
_run_migration() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    PI_LEGACY_SETTINGS_FILE="$3/pi/settings.json"
    PI_SETTINGS_FILE="$3/pi/agent/settings.json"
    PI_SETTINGS_SRC="$4"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20260831_pi_settings_agent_path
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR" "$TEMPLATE"
}

@test "a machine that never had the inert file is a no-op" {
  run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a file still matching the template is removed outright" {
  cp "$TEMPLATE" "$LEGACY"

  run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"Removed the inert Pi settings"* ]]
  [ ! -e "$LEGACY" ]
  [ ! -e "$LEGACY.pre-move" ]
}

@test "a file the operator edited is kept beside the path it never reached" {
  printf '{\n  "defaultModel": "hand-picked"\n}\n' > "$LEGACY"

  run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pi never read"* ]]
  [ ! -e "$LEGACY" ]
  [ "$(jq -r '.defaultModel' "$LEGACY.pre-move")" = "hand-picked" ]
}

@test "the edited contents are not merged forward into the live file" {
  # Pi never read them, so the operator believes that configuration is inactive.
  # Activating it silently is the wrong surprise.
  printf '{\n  "defaultModel": "hand-picked"\n}\n' > "$LEGACY"

  run _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$TMPDIR/pi/agent/settings.json" ]
}

@test "a second run after a removal is a no-op" {
  cp "$TEMPLATE" "$LEGACY"

  run _run_migration
  [ "$status" -eq 0 ]
  run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a second run leaves the kept copy alone" {
  printf '{\n  "defaultModel": "hand-picked"\n}\n' > "$LEGACY"

  run _run_migration
  [ "$status" -eq 0 ]
  run _run_migration
  [ "$status" -eq 3 ]
  [ "$(jq -r '.defaultModel' "$LEGACY.pre-move")" = "hand-picked" ]
}

@test "a backup already there is never the file overwritten" {
  # Reachable when the state line was cleared and the legacy file recreated: the
  # earlier copy is one the operator was told to keep, so the second goes beside it.
  printf '{\n  "defaultModel": "first"\n}\n' > "$LEGACY"
  run _run_migration
  [ "$status" -eq 0 ]

  printf '{\n  "defaultModel": "second"\n}\n' > "$LEGACY"
  run _run_migration
  [ "$status" -eq 0 ]
  [ "$(jq -r '.defaultModel' "$LEGACY.pre-move")" = "first" ]
  [ "$(jq -r '.defaultModel' "$LEGACY.pre-move.2")" = "second" ]
}
