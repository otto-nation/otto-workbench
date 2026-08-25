#!/usr/bin/env bats
# Tests for lib/worktree.sh — project_root, the tree a project artifact goes in.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/gitenv.sh
  source "$REPO_ROOT/lib/gitenv.sh"
  # shellcheck disable=SC2034  # project_root reads it to reach resolve-worktree
  BIN_SRC_DIR="$REPO_ROOT/bin"
  # shellcheck source=../lib/worktree.sh
  source "$REPO_ROOT/lib/worktree.sh"

  # -P: git answers with the physical path, and on macOS $TMPDIR is reached
  # through /var -> /private/var, so an unresolved fixture path never matches.
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  SEED="$TMPDIR/seed"
  mkdir -p "$SEED"
  printf 'x\n' > "$SEED/a.sh"
  make_container_seed "$SEED"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

@test "a working tree answers with itself" {
  run project_root "$SEED"
  [ "$status" -eq 0 ]
  [ "$output" = "$SEED" ]
}

@test "a subdirectory answers with the repo root" {
  mkdir -p "$SEED/deep/nested"
  run project_root "$SEED/deep/nested"
  [ "$status" -eq 0 ]
  [ "$output" = "$SEED" ]
}

@test "a container answers with the worktree on its default branch" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  run project_root "$TMPDIR/c"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/c/main" ]
}

@test "a linked worktree answers with itself, not the container" {
  make_worktree_container "$TMPDIR/c" "$SEED"
  git -C "$TMPDIR/c" worktree add -q "$TMPDIR/c/feat" feat
  run project_root "$TMPDIR/c/feat"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/c/feat" ]
}

@test "a container with no worktree on its default branch is 1" {
  local container="$TMPDIR/c"
  make_empty_container "$container" "$SEED"

  run project_root "$container"
  [ "$status" -eq 1 ]
}

@test "a directory in no repository is 2" {
  mkdir -p "$TMPDIR/loose"
  run project_root "$TMPDIR/loose"
  [ "$status" -eq 2 ]
}

@test "a directory that does not exist is resolve-worktree's usage error" {
  run project_root "$TMPDIR/never-existed"
  [ "$status" -eq 64 ]
}

@test "no argument answers for the current directory" {
  cd "$SEED"
  run project_root
  [ "$status" -eq 0 ]
  [ "$output" = "$SEED" ]
}

@test "an inherited GIT_DIR does not choose the repository" {
  # A git hook exports GIT_DIR, and git reads it ahead of `-C`. Without the
  # clearing this does, --show-toplevel would answer for the hook's repo.
  make_worktree_container "$TMPDIR/c" "$SEED"
  export GIT_DIR="$SEED/.git"

  run project_root "$TMPDIR/c"
  [ "$status" -eq 0 ]
  [ "$output" = "$TMPDIR/c/main" ]

  unset GIT_DIR
}

@test "the caller's git environment survives the lookup" {
  export GIT_DIR="$SEED/.git"
  project_root "$SEED" >/dev/null
  [ "${GIT_DIR:-}" = "$SEED/.git" ]
  unset GIT_DIR
}
