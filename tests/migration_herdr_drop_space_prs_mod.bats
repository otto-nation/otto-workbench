#!/usr/bin/env bats
# Tests for ai/herdr/migrations/20261117-herdr-drop-space-prs-mod.sh — removes
# the workbench-installed herdr-app space-prs mod from both app dirs.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/herdr/migrations/20261117-herdr-drop-space-prs-mod.sh"
  APP="$TMPDIR/herdr-app"
  UNTRUSTED="$TMPDIR/herdr-app-untrusted"
}

teardown() {
  common_teardown
}

# Runs the migration with the two app dirs pointed into TMPDIR and the ui.sh
# helpers stubbed. Exit status is the function's own: 0 when it removed a copy,
# MIGRATION_NOOP when there was none of ours.
_run_migration() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    HERDR_APP_DIR="$4"
    HERDR_UNTRUSTED_APP_DIR="$5"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20261117_herdr_drop_space_prs_mod
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR" "$APP" "$UNTRUSTED"
}

# _seed_ours HOME — a space-prs copy the installer wrote (manifest present).
_seed_ours() {
  mkdir -p "$1/mods/space-prs"
  printf 'export default class X {}\n' > "$1/mods/space-prs/main.js"
  printf 'main.js\tabc\n' > "$1/mods/space-prs/.workbench-install"
}

@test "removes the workbench copy from both app dirs" {
  _seed_ours "$APP"
  _seed_ours "$UNTRUSTED"

  run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed the retired space-prs mod"* ]]
  [ ! -e "$APP/mods/space-prs" ]
  [ ! -e "$UNTRUSTED/mods/space-prs" ]
}

@test "leaves a hand-made space-prs without a manifest" {
  mkdir -p "$APP/mods/space-prs"
  printf 'mine\n' > "$APP/mods/space-prs/main.js"

  run _run_migration
  [ "$status" -eq 3 ]
  [ -f "$APP/mods/space-prs/main.js" ]
}

@test "is a no-op when neither app dir has the mod" {
  run _run_migration
  [ "$status" -eq 3 ]
  [[ "$output" != *"OK "* ]]
}

@test "is a no-op on a second run after removing ours" {
  _seed_ours "$APP"
  run _run_migration
  [ "$status" -eq 0 ]

  run _run_migration
  [ "$status" -eq 3 ]
  [[ "$output" != *"OK "* ]]
}
