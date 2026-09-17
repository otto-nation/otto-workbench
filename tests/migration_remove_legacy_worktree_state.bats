#!/usr/bin/env bats
# Tests for bin/migrations/20260916-remove-legacy-worktree-state.sh — removes
# the state.json, run.lock, and trail.jsonl the pre-target layout left in a
# worktree's .workbench/.

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/bin/migrations/20260916-remove-legacy-worktree-state.sh"
  WORKTREE="$TMPDIR/worktree"
  mkdir -p "$WORKTREE"
}

teardown() {
  common_teardown
}

# Sources the migration the way lib/migrations.sh does and calls it with the
# work tree path the framework hands a checkout-scoped migration.
_run_migration() {
  run bash -c '
    success() { :; }
    warn()    { :; }
    WORKBENCH_DIR="$3"
    LIB_SRC_DIR="$3/lib"
    LEGACY_WORKBENCH_ROOT="$4/.unused-legacy"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20260916_remove_legacy_worktree_state "$2"
  ' _ "$MIGRATION" "$1" "$REPO_ROOT" "$TMPDIR"
}

_seed_legacy() {
  mkdir -p "$WORKTREE/.workbench"
  local name
  for name in "$@"; do
    printf 'x\n' > "$WORKTREE/.workbench/$name"
  done
}

@test "is a no-op when the work tree has no legacy directory" {
  _run_migration "$WORKTREE"
  [ "$status" -eq 3 ]
  [ ! -e "$WORKTREE/.workbench" ]
}

@test "removes all three files and the directory when nothing else is in it" {
  _seed_legacy state.json run.lock trail.jsonl

  _run_migration "$WORKTREE"
  [ "$status" -eq 0 ]
  [ ! -e "$WORKTREE/.workbench" ]
}

@test "removes a single owned file and the directory when it is then empty" {
  _seed_legacy trail.jsonl

  _run_migration "$WORKTREE"
  [ "$status" -eq 0 ]
  [ ! -e "$WORKTREE/.workbench" ]
}

@test "removes two of the three and the directory, leaving no trace of the third" {
  # The singleton case above and the all-three case cannot tell a per-file loop
  # from one that stops after its first hit. This one can.
  _seed_legacy state.json run.lock

  _run_migration "$WORKTREE"
  [ "$status" -eq 0 ]
  [ ! -e "$WORKTREE/.workbench" ]
}

@test "removes trail.jsonl and run.lock, leaving no trace of state.json" {
  _seed_legacy trail.jsonl run.lock

  _run_migration "$WORKTREE"
  [ "$status" -eq 0 ]
  [ ! -e "$WORKTREE/.workbench" ]
}

@test "keeps the directory when an unrelated file remains" {
  _seed_legacy state.json run.lock trail.jsonl
  printf 'keep\n' > "$WORKTREE/.workbench/someone-elses.txt"

  _run_migration "$WORKTREE"
  [ "$status" -eq 0 ]
  [ ! -e "$WORKTREE/.workbench/state.json" ]
  [ ! -e "$WORKTREE/.workbench/run.lock" ]
  [ ! -e "$WORKTREE/.workbench/trail.jsonl" ]
  [ -d "$WORKTREE/.workbench" ]
  [ "$(cat "$WORKTREE/.workbench/someone-elses.txt")" = "keep" ]
}

@test "is idempotent — a second run reports no work" {
  _seed_legacy state.json run.lock trail.jsonl

  _run_migration "$WORKTREE"
  [ "$status" -eq 0 ]
  _run_migration "$WORKTREE"
  [ "$status" -eq 3 ]
  [ ! -e "$WORKTREE/.workbench" ]
}
