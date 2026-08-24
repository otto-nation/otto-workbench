#!/usr/bin/env bats
# Tests for ai/claude/migrations/20260824-drop-container-anatomy.sh — deletes the
# anatomy index a bare-repo container was given while the generator still wrote
# one there.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260824-drop-container-anatomy.sh"
  TMPDIR="$(mktemp -d)"
  SEED="$TMPDIR/seed"
  mkdir -p "$SEED"
  printf '# a thing\ncode\n' > "$SEED/a.sh"
  make_container_seed "$SEED"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Runs the migration against PROJECT_DIR with the ui.sh helpers stubbed out.
# Sources the real lib/migrations.sh for MIGRATION_NOOP and lib/gitenv.sh for
# git_env_clear — both reach the migration from the framework's own sourcing
# environment, so neither is redefined here. Exit status is the function's own:
# the framework reads it to tell a deletion (0) from a container that had
# nothing to delete (MIGRATION_NOOP).
_run_migration() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$3"
    LIB_SRC_DIR="$3/lib"
    BIN_SRC_DIR="$3/bin"
    LEGACY_WORKBENCH_ROOT="$4/.unused-legacy"
    . "$WORKBENCH_DIR/lib/gitenv.sh"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20260824_drop_container_anatomy "$2"
  ' _ "$MIGRATION" "$1" "$REPO_ROOT" "$TMPDIR"
}

# _seed_stale DIR — the anatomy index the generator used to write at DIR.
_seed_stale() {
  mkdir -p "$1/.claude"
  printf '# Project Anatomy\n' > "$1/.claude/anatomy.md"
}

@test "removes the stale index from a bare-repo container" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_stale "$TMPDIR/c"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Removed the stale anatomy index"* ]]
  [ ! -e "$TMPDIR/c/.claude/anatomy.md" ]
}

@test "leaves the container's .claude directory in place" {
  # The permission mirror owns that directory and writes settings.json into it.
  # Only the index this migration is named for goes.
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_stale "$TMPDIR/c"
  printf '{}\n' > "$TMPDIR/c/.claude/settings.json"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/c/.claude/settings.json" ]
}

@test "a second run is a no-op" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_stale "$TMPDIR/c"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 3 ]
  [ -z "$output" ]
}

@test "a container that never had one is a no-op" {
  make_worktree_container "$TMPDIR/c" "$SEED"

  run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 3 ]
  [ ! -e "$TMPDIR/c/.claude" ]
}

@test "a container whose default branch has no worktree is still cleaned" {
  # resolve-worktree answers 1 here rather than 0 — the container holds whatever
  # was written into it either way, so 1 must not be read as "not a container".
  local container="$TMPDIR/c"
  make_empty_container "$container" "$SEED"
  git -C "$container" worktree add -q "$container/feat" feat
  _seed_stale "$container"

  run _run_migration "$container/feat"
  [ "$status" -eq 0 ]
  [ ! -e "$container/.claude/anatomy.md" ]
}

@test "an ordinary repo keeps its own tracked index" {
  # dirname of a non-bare repo's --git-common-dir is the repo itself, so a
  # migration that skipped the bare check would delete a real checkout's
  # committed anatomy.md.
  local repo="$TMPDIR/plain"
  mkdir -p "$repo"
  git -C "$repo" init -q
  _seed_stale "$repo"

  run _run_migration "$repo"
  [ "$status" -eq 3 ]
  [ -f "$repo/.claude/anatomy.md" ]
}

@test "a linked worktree of an ordinary repo leaves the main checkout alone" {
  # --git-common-dir from a linked worktree points into the primary checkout's
  # .git, so its parent is that checkout — a real tree, not a container.
  local repo="$TMPDIR/plain/repo"
  mkdir -p "$repo"
  git -C "$repo" init -q
  git -C "$repo" config user.email test@example.com
  git -C "$repo" config user.name Test
  printf 'x\n' > "$repo/a.sh"
  git -C "$repo" add -A
  git -C "$repo" commit -qm init
  git -C "$repo" worktree add -q -b feat "$TMPDIR/plain/wt"
  _seed_stale "$repo"

  run _run_migration "$TMPDIR/plain/wt"
  [ "$status" -eq 3 ]
  [ -f "$repo/.claude/anatomy.md" ]
}

@test "a directory that is not a git repo is a no-op" {
  mkdir -p "$TMPDIR/loose"

  run _run_migration "$TMPDIR/loose"
  [ "$status" -eq 3 ]
}

@test "a registered directory that is gone is a no-op" {
  run _run_migration "$TMPDIR/never-existed"
  [ "$status" -eq 3 ]
}

@test "an inherited GIT_DIR does not redirect the lookup" {
  # Sync runs from inside a git hook, which exports GIT_DIR — and GIT_DIR beats
  # `git -C`, so without git_env_clear the lookup answers for the hook's
  # repository and the container is never reached.
  make_worktree_container "$TMPDIR/c" "$SEED"
  _seed_stale "$TMPDIR/c"

  GIT_DIR="$SEED/.git" run _run_migration "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [ ! -e "$TMPDIR/c/.claude/anatomy.md" ]
}
