#!/usr/bin/env bats
# Tests for bin/resolve-worktree — which worktree a bare-repo container stands in for.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  export GIT_CONFIG_GLOBAL=/dev/null

  SCRIPT="$REPO_ROOT/bin/resolve-worktree"
  # Physical path: on macOS mktemp hands back /var/..., git reports the
  # /private/var/... it resolves to, and every path comparison below would fail.
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  SEED="$TMPDIR/seed"
  CONTAINER="$TMPDIR/container"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _make_seed [BRANCH] — a one-commit repo to clone containers from.
_make_seed() {
  local branch="${1:-main}"
  git init -q --initial-branch="$branch" "$SEED"
  git -C "$SEED" config user.email test@example.com
  git -C "$SEED" config user.name Test
  printf 'seed\n' > "$SEED/README.md"
  git -C "$SEED" add -A
  git -C "$SEED" commit -qm init
}

# _make_container BRANCH — a bare .git with one worktree checked out on BRANCH,
# the layout wt-init produces: container/.git bare, worktrees as its peers.
_make_container() {
  local branch="$1"
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
  git -C "$CONTAINER" worktree add -q "$CONTAINER/$branch" "$branch"
}

# ── Bare-repo containers ─────────────────────────────────────────────────────

@test "resolves the default-branch worktree of a bare container" {
  _make_seed main
  _make_container main

  run "$SCRIPT" "$CONTAINER"
  [ "$status" -eq 0 ]
  [ "$output" = "$CONTAINER/main" ]
}

@test "resolves from the container as the current directory" {
  _make_seed main
  _make_container main

  cd "$CONTAINER"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$output" = "$CONTAINER/main" ]
}

@test "falls back to master when main does not exist" {
  _make_seed master
  _make_container master

  run "$SCRIPT" "$CONTAINER"
  [ "$status" -eq 0 ]
  [ "$output" = "$CONTAINER/master" ]
}

@test "prefers the branch origin/HEAD names over the main fallback" {
  _make_seed main
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
  git -C "$CONTAINER" branch trunk main
  git -C "$CONTAINER" worktree add -q "$CONTAINER/main" main
  git -C "$CONTAINER" worktree add -q "$CONTAINER/trunk" trunk
  git -C "$CONTAINER" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/trunk

  run "$SCRIPT" "$CONTAINER"
  [ "$status" -eq 0 ]
  [ "$output" = "$CONTAINER/trunk" ]
}

@test "ignores worktrees on other branches" {
  _make_seed main
  _make_container main
  git -C "$CONTAINER" branch feature main
  git -C "$CONTAINER" worktree add -q "$CONTAINER/feature" feature

  run "$SCRIPT" "$CONTAINER"
  [ "$status" -eq 0 ]
  [ "$output" = "$CONTAINER/main" ]
}

# ── Nothing to resolve ───────────────────────────────────────────────────────

@test "reports a container with no worktrees at all" {
  _make_seed main
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"

  run "$SCRIPT" "$CONTAINER"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no worktree on 'main'"* ]]
}

@test "reports a container whose default-branch worktree directory is gone" {
  _make_seed main
  _make_container main
  rm -rf "$CONTAINER/main"

  run "$SCRIPT" "$CONTAINER"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no worktree on 'main'"* ]]
}

# ── Everything that is not a container ───────────────────────────────────────

@test "exits 2 and prints nothing inside a worktree" {
  _make_seed main
  _make_container main

  run "$SCRIPT" "$CONTAINER/main"
  [ "$status" -eq 2 ]
  [ -z "$output" ]
}

@test "exits 2 and prints nothing in an ordinary repo" {
  _make_seed main

  run "$SCRIPT" "$SEED"
  [ "$status" -eq 2 ]
  [ -z "$output" ]
}

@test "exits 2 and prints nothing outside any repo" {
  local loose="$TMPDIR/loose"
  mkdir -p "$loose"
  export GIT_CEILING_DIRECTORIES="$TMPDIR"

  run "$SCRIPT" "$loose"
  [ "$status" -eq 2 ]
  [ -z "$output" ]
}

# ── Usage ────────────────────────────────────────────────────────────────────

@test "prints usage for -h" {
  run "$SCRIPT" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "rejects an unknown option" {
  run "$SCRIPT" --nope
  [ "$status" -eq 64 ]
  [[ "$output" == *"Unknown option"* ]]
}

@test "rejects a directory that does not exist" {
  run "$SCRIPT" "$TMPDIR/absent"
  [ "$status" -eq 64 ]
  [[ "$output" == *"No such directory"* ]]
}
