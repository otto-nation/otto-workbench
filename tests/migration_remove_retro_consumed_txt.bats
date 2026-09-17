#!/usr/bin/env bats
# Tests for ai/claude/migrations/20260917-remove-retro-consumed-txt.sh — drops
# the plain-text consumed-reviews record the scan-ID-stamped JSON manifest
# replaced.
#
# The file it removes was a list of review directory names that authorised a
# deletion. That is what makes the guards here worth asserting rather than
# assuming: a migration that resolved its target wrongly would be running `rm`
# against a path derived from an empty state root.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260917-remove-retro-consumed-txt.sh"
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
# that never had the legacy file (MIGRATION_NOOP, 3).
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
    migration_20260917_remove_retro_consumed_txt
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR"
}

# _seed_legacy — the plain-text record the old retro-scan wrote.
_seed_legacy() {
  printf 'repo-self-branch\nother-self-branch\n' > "$STATE/retro-consumed-reviews.txt"
}

@test "removes the legacy plain-text record" {
  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 3 ]

  _seed_legacy
  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"Removed legacy"* ]]
  [ ! -e "$STATE/retro-consumed-reviews.txt" ]
}

@test "a second run is a no-op" {
  _seed_legacy

  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 0 ]
  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a machine that never ran the old scan is a no-op" {
  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "the JSON manifest that replaced it is left alone" {
  # The two names differ only by extension, and the live one authorises the
  # same deletion. A glob or a prefix match here would disarm the current retro.
  _seed_legacy
  printf '{"scan_id": "aaaaaaaaaaaa", "reviews": []}\n' > "$STATE/retro-consumed-reviews.json"

  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 0 ]
  [ -f "$STATE/retro-consumed-reviews.json" ]
  [ ! -e "$STATE/retro-consumed-reviews.txt" ]
}

# The migration's empty-$WORKBENCH_STATE_DIR guard is deliberately not covered.
# With the variable empty the target collapses to "/retro-consumed-reviews.txt",
# which `-f` rejects on any machine that does not have that file at the
# filesystem root — so a test for it passes identically with the guard deleted,
# and asserts nothing. Covering it honestly would mean creating a file at `/`.
# The guard stays as defence in depth on a line that deletes files; this note is
# here so its absence from the suite reads as a decision rather than an omission.

@test "a directory by that name is left alone rather than removed" {
  # The guard is -f, not -e. `rm -f` would fail on a directory anyway, but the
  # migration should not reach it: nothing legitimate creates one, so it is
  # someone else's state and not this migration's to touch.
  mkdir -p "$STATE/retro-consumed-reviews.txt"

  WORKBENCH_STATE_DIR="$STATE" run _run_migration
  [ "$status" -eq 3 ]
  [ -d "$STATE/retro-consumed-reviews.txt" ]
}
