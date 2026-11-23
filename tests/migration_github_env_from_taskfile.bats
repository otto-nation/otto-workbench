#!/usr/bin/env bats
# Copy taskfile.env → github.env; do not move. New file already present is
# NOOP; neither file is DEFERRED.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MACHINE="$REPO_ROOT/bin/migrations/20261123-github-env-from-taskfile.sh"
  REPO_MIG="$REPO_ROOT/bin/migrations/20261123-repo-github-env-from-taskfile.sh"
  HOME_DIR="$TMPDIR/home"
  CFG="$HOME_DIR/.config"
  mkdir -p "$CFG/task" "$CFG/workbench"
}

teardown() {
  common_teardown
}

_run_machine() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    WORKBENCH_GITHUB_ENV="$4"
    TASKFILE_ENV="$5"
    . "$1"
    migration_20261123_github_env_from_taskfile
  ' _ "$MACHINE" "$REPO_ROOT" "$TMPDIR" "$1" "$2"
}

_run_repo() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20261123_repo_github_env_from_taskfile "$4"
  ' _ "$REPO_MIG" "$REPO_ROOT" "$TMPDIR" "$1"
}

@test "copies machine taskfile.env to github.env" {
  printf 'GH_TOKEN=from-old\n' > "$CFG/task/taskfile.env"
  run _run_machine "$CFG/workbench/github.env" "$CFG/task/taskfile.env"
  [ "$status" -eq 0 ]
  [ -f "$CFG/workbench/github.env" ]
  grep -qx 'GH_TOKEN=from-old' "$CFG/workbench/github.env"
  grep -qx 'GH_TOKEN=from-old' "$CFG/task/taskfile.env"
}

@test "machine copy is a no-op when github.env already exists" {
  printf 'GH_TOKEN=new\n' > "$CFG/workbench/github.env"
  printf 'GH_TOKEN=old\n' > "$CFG/task/taskfile.env"
  run _run_machine "$CFG/workbench/github.env" "$CFG/task/taskfile.env"
  [ "$status" -eq 3 ]
  grep -qx 'GH_TOKEN=new' "$CFG/workbench/github.env"
}

@test "machine copy defers when neither file exists" {
  run _run_machine "$CFG/workbench/github.env" "$CFG/task/taskfile.env"
  [ "$status" -eq 4 ]
  [ ! -e "$CFG/workbench/github.env" ]
}

@test "copies checkout taskfile.env to .workbench/github.env" {
  local tree="$TMPDIR/wt"
  mkdir -p "$tree/.taskfile"
  printf 'GH_TOKEN=repo-old\n' > "$tree/.taskfile/taskfile.env"
  run _run_repo "$tree"
  [ "$status" -eq 0 ]
  grep -qx 'GH_TOKEN=repo-old' "$tree/.workbench/github.env"
  grep -qx 'GH_TOKEN=repo-old' "$tree/.taskfile/taskfile.env"
}

@test "checkout copy is a no-op when .workbench/github.env already exists" {
  local tree="$TMPDIR/wt-exists"
  mkdir -p "$tree/.taskfile" "$tree/.workbench"
  printf 'GH_TOKEN=new\n' > "$tree/.workbench/github.env"
  printf 'GH_TOKEN=old\n' > "$tree/.taskfile/taskfile.env"
  run _run_repo "$tree"
  [ "$status" -eq 3 ]
  grep -qx 'GH_TOKEN=new' "$tree/.workbench/github.env"
}

@test "checkout copy defers when the source is missing" {
  local tree="$TMPDIR/wt2"
  mkdir -p "$tree"
  run _run_repo "$tree"
  [ "$status" -eq 4 ]
  [ ! -e "$tree/.workbench/github.env" ]
}
