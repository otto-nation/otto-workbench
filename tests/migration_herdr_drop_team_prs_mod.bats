#!/usr/bin/env bats
# Tests for ai/herdr/migrations/20261109-herdr-drop-team-prs-mod.sh — removes the
# workbench-installed herdr-app team-prs mod, renamed to prs, from both app dirs.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/herdr/migrations/20261109-herdr-drop-team-prs-mod.sh"
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
    migration_20261109_herdr_drop_team_prs_mod
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR" "$APP" "$UNTRUSTED"
}

# _seed_ours HOME — a team-prs copy the installer wrote (manifest present).
_seed_ours() {
  mkdir -p "$1/mods/team-prs"
  printf 'export default class X {}\n' > "$1/mods/team-prs/main.js"
  printf 'main.js\tabc\n' > "$1/mods/team-prs/.workbench-install"
}

@test "removes the workbench copy from both app dirs and leaves prs" {
  _seed_ours "$APP"
  _seed_ours "$UNTRUSTED"
  mkdir -p "$APP/mods/prs"

  run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed the old team-prs mod"* ]]
  [ ! -e "$APP/mods/team-prs" ]
  [ ! -e "$UNTRUSTED/mods/team-prs" ]
  [ -d "$APP/mods/prs" ]
}

@test "leaves a hand-made team-prs without a manifest" {
  mkdir -p "$APP/mods/team-prs"
  printf 'mine\n' > "$APP/mods/team-prs/main.js"

  run _run_migration
  [ "$status" -eq 3 ]
  [ -f "$APP/mods/team-prs/main.js" ]
}

@test "is a no-op when neither app dir has the mod" {
  run _run_migration
  [ "$status" -eq 3 ]
  [[ "$output" != *"OK "* ]]
}
