#!/usr/bin/env bats
# Tests for bin/migrations/20261006-remove-global-taskfile-links.sh — drops
# the ~/.config/task symlinks the retired task component made for
# `task --global`.
#
# Taskfile.yml and lib were links into the workbench; taskfile.env (GitHub
# PATs) shares the directory and must never be touched, nor the directory
# itself. Only `-L` paths are removed so a regular file or directory by
# those names is someone else's and not this migration's to delete.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/bin/migrations/20261006-remove-global-taskfile-links.sh"
  CFG="$TMPDIR/home/.config/task"
  mkdir -p "$CFG"
}

teardown() {
  common_teardown
}

# Runs the migration against TASK_CONFIG_DIR with the ui.sh helpers stubbed
# out, the way lib/migrations.sh sources and calls it. The real
# lib/migrations.sh comes in for MIGRATION_NOOP, which the migration returns
# by name. Exit status is the function's own — the framework reads it to tell
# a removal (0) from a machine that never had the links (MIGRATION_NOOP, 3).
#
# TASK_CONFIG_DIR is passed through the environment rather than assigned
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
    migration_20261006_remove_global_taskfile_links
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR"
}

@test "removes both symlinks the task component created" {
  # Taskfile.yml -> a file, lib -> a directory: the shapes step_task_symlinks made.
  ln -s "$REPO_ROOT/Taskfile.yml" "$CFG/Taskfile.yml"
  ln -s "$REPO_ROOT/lib" "$CFG/lib"
  TASK_CONFIG_DIR="$CFG" run _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$CFG/Taskfile.yml" ] && [ ! -L "$CFG/Taskfile.yml" ]
  [ ! -e "$CFG/lib" ] && [ ! -L "$CFG/lib" ]
  [ -d "$REPO_ROOT/lib" ]
}

@test "taskfile.env and the directory are left alone" {
  printf 'GH_TOKEN=x\n' > "$CFG/taskfile.env"
  ln -s "$REPO_ROOT/lib" "$CFG/lib"
  TASK_CONFIG_DIR="$CFG" run _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$CFG/taskfile.env")" = "GH_TOKEN=x" ]
  [ -d "$CFG" ]
}

@test "a dangling symlink is removed too" {
  # After the PR merges, Taskfile.global.yml is gone, so the link dangles.
  ln -s "$TMPDIR/gone/Taskfile.global.yml" "$CFG/Taskfile.yml"
  TASK_CONFIG_DIR="$CFG" run _run_migration
  [ "$status" -eq 0 ]
  [ ! -L "$CFG/Taskfile.yml" ]
}

@test "a regular file or directory by those names is not touched" {
  printf 'version: "3"\n' > "$CFG/Taskfile.yml"
  mkdir -p "$CFG/lib"
  TASK_CONFIG_DIR="$CFG" run _run_migration
  [ "$status" -eq 3 ]
  [ -f "$CFG/Taskfile.yml" ]
  [ -d "$CFG/lib" ]
}

@test "nothing there is a silent no-op, and a second run is too" {
  TASK_CONFIG_DIR="$CFG" run _run_migration
  [ "$status" -eq 3 ]
  [ -z "$output" ]
  ln -s "$REPO_ROOT/lib" "$CFG/lib"
  TASK_CONFIG_DIR="$CFG" run _run_migration
  [ "$status" -eq 0 ]
  TASK_CONFIG_DIR="$CFG" run _run_migration
  [ "$status" -eq 3 ]
}

@test "a link that cannot be removed fails so the framework retries" {
  [ "$(id -u)" -ne 0 ] || skip "root ignores directory permissions"
  ln -s "$REPO_ROOT/lib" "$CFG/lib"
  chmod 500 "$CFG"
  TASK_CONFIG_DIR="$CFG" run _run_migration
  chmod 700 "$CFG"
  [ "$status" -eq 1 ]
  [[ "$output" == *"WARN Could not remove $CFG/lib"* ]]
  [[ "$output" != *"OK Removed"* ]]
  [ -L "$CFG/lib" ]
}

# With TASK_CONFIG_DIR empty the targets collapse to "/Taskfile.yml" and "/lib",
# which are absent on any normal machine, so only the returned status shows the
# guard ran. It must fail (retried next sync) and not answer NOOP, which the
# framework records as applied — retiring the migration unlooked-at.
@test "an empty TASK_CONFIG_DIR fails rather than recording the migration as done" {
  TASK_CONFIG_DIR="" run _run_migration
  [ "$status" -eq 1 ]
  [[ "$output" == *"TASK_CONFIG_DIR is empty"* ]]
}
