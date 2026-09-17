#!/usr/bin/env bats
# Tests for the retro stamp move out of Claude's tree.
#
# The value carried matters more than the file: a machine that loses it reads 0,
# which puts retro-scan in first-run mode — every merged PR the window allows
# per registered repo, and every local review consumed and then deleted.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  mkdir -p "$HOME/.claude"
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260917-retro-stamp-state-root.sh"
  OLD_STAMP="$HOME/.claude/.last-retro"
  NEW_STAMP="$TMPDIR/state/gates/last-retro"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _run_migration — sources the migration the way lib/migrations.sh does and
# calls its function, returning its exit status. lib/migrations.sh is sourced
# too, not just lib/ui.sh — it is where MIGRATION_NOOP is defined.
_run_migration() {
  WORKBENCH_DIR="$REPO_ROOT" run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    . '$MIGRATION'
    migration_20260917_retro_stamp_state_root
  "
}

@test "carries the timestamp to the state root" {
  echo "1789583719" > "$OLD_STAMP"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$NEW_STAMP")" = "1789583719" ]
  [ ! -f "$OLD_STAMP" ]
}

@test "the carried stamp is the one the gate reads" {
  # The migration and should-retro.sh must agree on the path, or the carry is
  # a file write nothing looks at.
  echo "1789583719" > "$OLD_STAMP"
  _run_migration
  [ "$status" -eq 0 ]

  run bash -c "
    . '$REPO_ROOT/lib/constants.sh'
    . '$REPO_ROOT/lib/ai/session-count.sh'
    _read_stamp \"\$RETRO_STAMP_FILE\"
  "
  [ "$output" = "1789583719" ]
}

@test "is a no-op when no old stamp exists" {
  _run_migration
  [ "$status" -eq 3 ]
}

@test "keeps the newer stamp when both exist" {
  mkdir -p "$TMPDIR/state/gates"
  echo "1000" > "$OLD_STAMP"
  echo "2000" > "$NEW_STAMP"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$NEW_STAMP")" = "2000" ]
  [ ! -f "$OLD_STAMP" ]
}

@test "is idempotent — a second run reports no work" {
  echo "1789583719" > "$OLD_STAMP"

  _run_migration
  [ "$status" -eq 0 ]
  _run_migration
  [ "$status" -eq 3 ]
}
