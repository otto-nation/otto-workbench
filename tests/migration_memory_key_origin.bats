#!/usr/bin/env bats
# Tests for the memory origin-key migration.
#
# Path-keyed memory directories (shared git dir) split when a clone moves.
# The migration renames a live repo's old key onto its origin key, and adopts
# a unique leftover whose slug still names the repo.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export HOME="$TMPDIR/home"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_DATA_DIR="$TMPDIR/data"
  export PROJECTS_REGISTRY_FILE="$TMPDIR/state/projects.registry"
  mkdir -p "$HOME" "$TMPDIR/state" "$TMPDIR/data/memory"

  # 3 is MIGRATION_NOOP in lib/migrations.sh. The tests compare against the
  # number the function returns, not a second definition of the constant.
  MIGRATION_NOOP=3
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20261125-memory-key-origin.sh"
}

teardown() {
  common_teardown
}

_init_repo() {
  local dir="$1" origin="${2:-}"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" -c user.email=t@example.com -c user.name=t \
    commit --allow-empty -qm init
  if [[ -n "$origin" ]]; then
    git -C "$dir" remote add origin "$origin"
  fi
}

_key() {
  local fn="$1" dir="$2"
  bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/ai/session-count.sh'
    $fn '$dir'
  "
}

_run_migration() {
  WORKBENCH_DIR="$REPO_ROOT" run bash -c "
    PROJECTS_EXCLUDED_PREFIXES=('$TMPDIR/state' '$TMPDIR/data')
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    . '$MIGRATION'
    migration_20261125_memory_key_origin
  "
}

@test "renames a live repo's path-keyed memory onto its origin key" {
  local repo="$TMPDIR/otto-workbench"
  _init_repo "$repo" "git@github.com:otto-nation/otto-workbench.git"
  local old new
  old="$(_key _repo_key_from_shared_dir "$repo")"
  new="$(_key _repo_key "$repo")"
  [ "$old" != "$new" ]
  mkdir -p "$WORKBENCH_DATA_DIR/memory/$old"
  echo note > "$WORKBENCH_DATA_DIR/memory/$old/MEMORY.md"

  PROJECTS_EXCLUDED_PREFIXES=("$TMPDIR/state" "$TMPDIR/data") \
    bash -c "
      . '$REPO_ROOT/lib/ui.sh'
      . '$REPO_ROOT/lib/migrations.sh'
      project_register '$repo'
      record_project_repo_ids
    "
  _run_migration
  [ "$status" -eq 0 ]
  [ -f "$WORKBENCH_DATA_DIR/memory/$new/MEMORY.md" ]
  [ ! -e "$WORKBENCH_DATA_DIR/memory/$old" ]
}

@test "adopts a unique leftover whose slug still names the repo" {
  # The leftover is named as a short path-keyed directory, the shape a moved
  # clone actually leaves behind. Deriving one from the bats tmp path truncates
  # at 64 characters and drops the repo name the matcher looks for.
  local live="$TMPDIR/new/maximum"
  local leftover="--Users-isaacg-git-maximum--git---deadbeef"
  mkdir -p "$WORKBENCH_DATA_DIR/memory/$leftover"
  echo kept > "$WORKBENCH_DATA_DIR/memory/$leftover/MEMORY.md"

  _init_repo "$live" "git@github.com:usemaximum/maximum.git"
  local dest
  dest="$(_key _repo_key "$live")"
  PROJECTS_EXCLUDED_PREFIXES=("$TMPDIR/state" "$TMPDIR/data") \
    bash -c "
      . '$REPO_ROOT/lib/ui.sh'
      . '$REPO_ROOT/lib/migrations.sh'
      project_register '$live'
      record_project_repo_ids
    "
  _run_migration
  [ "$status" -eq 0 ]
  [ -f "$WORKBENCH_DATA_DIR/memory/$dest/MEMORY.md" ]
  [ "$(cat "$WORKBENCH_DATA_DIR/memory/$dest/MEMORY.md")" = kept ]
  [ ! -e "$WORKBENCH_DATA_DIR/memory/$leftover" ]
}

@test "leaves memory alone when it is already origin-keyed" {
  local repo="$TMPDIR/otto-workbench"
  _init_repo "$repo" "git@github.com:otto-nation/otto-workbench.git"
  local key
  key="$(_key _repo_key "$repo")"
  mkdir -p "$WORKBENCH_DATA_DIR/memory/$key"
  echo already > "$WORKBENCH_DATA_DIR/memory/$key/MEMORY.md"
  PROJECTS_EXCLUDED_PREFIXES=("$TMPDIR/state" "$TMPDIR/data") \
    bash -c "
      . '$REPO_ROOT/lib/ui.sh'
      . '$REPO_ROOT/lib/migrations.sh'
      project_register '$repo'
      record_project_repo_ids
    "
  _run_migration
  [ "$status" -eq "$MIGRATION_NOOP" ]
  [ -f "$WORKBENCH_DATA_DIR/memory/$key/MEMORY.md" ]
}

@test "does not overwrite an origin-keyed directory that already exists" {
  local repo="$TMPDIR/otto-workbench"
  _init_repo "$repo" "git@github.com:otto-nation/otto-workbench.git"
  local old new
  old="$(_key _repo_key_from_shared_dir "$repo")"
  new="$(_key _repo_key "$repo")"
  mkdir -p "$WORKBENCH_DATA_DIR/memory/$old" "$WORKBENCH_DATA_DIR/memory/$new"
  echo old > "$WORKBENCH_DATA_DIR/memory/$old/MEMORY.md"
  echo new > "$WORKBENCH_DATA_DIR/memory/$new/MEMORY.md"
  PROJECTS_EXCLUDED_PREFIXES=("$TMPDIR/state" "$TMPDIR/data") \
    bash -c "
      . '$REPO_ROOT/lib/ui.sh'
      . '$REPO_ROOT/lib/migrations.sh'
      project_register '$repo'
      record_project_repo_ids
    "
  _run_migration
  [ "$status" -eq "$MIGRATION_NOOP" ]
  [ "$(cat "$WORKBENCH_DATA_DIR/memory/$new/MEMORY.md")" = new ]
  [ -f "$WORKBENCH_DATA_DIR/memory/$old/MEMORY.md" ]
}
