#!/usr/bin/env bats
# Tests for ai/claude/migrations/20261117-drop-batch-admission-registry.sh — drops
# leftover batch admission JSON and RSS estimates now that every queued step
# is an otto-queue ticket.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20261117-drop-batch-admission-registry.sh"
  STATE="$TMPDIR/state"
  mkdir -p "$STATE"
}

teardown() {
  common_teardown
}

# Runs the migration against $STATE with the ui.sh helpers stubbed out, the way
# lib/migrations.sh sources and calls it. The real lib/migrations.sh comes in
# for MIGRATION_NOOP, which the migration returns by name. Exit status is the
# function's own — the framework reads it to tell a removal (0) from a machine
# that never had leftover admission state (MIGRATION_NOOP, 3).
#
# WORKBENCH_STATE_DIR is passed through the environment rather than assigned
# inside, so a test can unset it to exercise the empty-value guard.
_run_migration() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20261117_drop_batch_admission_registry
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR"
}

# _seed_leftover — the admission registry and RSS estimates the old scheduler wrote.
_seed_leftover() {
  mkdir -p "$STATE/batch/admission"
  printf '{}\n' > "$STATE/batch/admission/registry.json"
  printf '{}\n' > "$STATE/batch/estimates.json"
  printf '' > "$STATE/batch/estimates.lock"
  printf '' > "$STATE/batch/estimates.json.lock"
}

@test "removes leftover admission dir and estimates files" {
  _seed_leftover

  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"Removed leftover batch admission registry"* ]]
  [ ! -e "$STATE/batch/admission" ]
  [ ! -e "$STATE/batch/estimates.json" ]
  [ ! -e "$STATE/batch/estimates.lock" ]
  [ ! -e "$STATE/batch/estimates.json.lock" ]
}

@test "a second run is a no-op" {
  _seed_leftover

  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 0 ]
  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a machine that never had leftover admission state is a no-op" {
  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}
