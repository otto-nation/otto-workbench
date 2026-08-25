#!/usr/bin/env bats
# Tests for wt-fetch-default — the worktrunk pre-switch hook that brings the
# local default branch up to date before `wt switch --create` cuts from it.
#
# The layouts are real git repos rather than stubs, because the bug this script
# replaces was entirely about which repository a command ran in.

setup() {
  load 'test_helper'
  common_setup
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  SCRIPT="$REPO_ROOT/git/bin/wt-fetch-default"

  export GIT_CEILING_DIRECTORIES="$TMPDIR"

  _make_origin
}

teardown() {
  cd "$ORIG_DIR" || return 1
  rm -rf "$TMPDIR"
  common_teardown
}

# ── Fixtures ─────────────────────────────────────────────────────────────────

# _git_identity DIR — the config every fixture repo needs to commit at all.
_git_identity() {
  git -C "$1" config user.email "test@example.com"
  git -C "$1" config user.name "Test"
}

# _make_origin — a bare remote on main with one commit, plus the seed clone the
# tests push further commits from.
_make_origin() {
  git init --bare --quiet --initial-branch=main "$TMPDIR/origin.git"
  git clone --quiet "$TMPDIR/origin.git" "$TMPDIR/seed" 2>/dev/null
  _git_identity "$TMPDIR/seed"
  echo one > "$TMPDIR/seed/a.txt"
  git -C "$TMPDIR/seed" add -A
  git -C "$TMPDIR/seed" commit --quiet -m "one"
  git -C "$TMPDIR/seed" push --quiet origin main
}

# _advance_origin — push one more commit to origin's main.
_advance_origin() {
  echo more >> "$TMPDIR/seed/a.txt"
  git -C "$TMPDIR/seed" commit --quiet -am "two"
  git -C "$TMPDIR/seed" push --quiet origin main
}

# _make_container — the layout wt-init produces: a bare repo at <container>/.git
# with worktrees as siblings. main/ holds the default branch to begin with.
_make_container() {
  mkdir -p "$TMPDIR/proj"
  git clone --quiet --bare "$TMPDIR/origin.git" "$TMPDIR/proj/.git" 2>/dev/null
  git -C "$TMPDIR/proj/.git" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
  _git_identity "$TMPDIR/proj/.git"
  git -C "$TMPDIR/proj/.git" fetch --quiet origin
  git -C "$TMPDIR/proj/.git" worktree add --quiet "$TMPDIR/proj/main" main
}

# _leave_default_branch — switch the primary worktree onto a feature branch, so
# no worktree holds the default branch. This is the state issue #936 was found
# in: `git branch -vv` still shows main as checked out, at the bare directory.
_leave_default_branch() {
  git -C "$TMPDIR/proj/main" checkout --quiet -b feat/other
}

_sha() {
  git -C "$TMPDIR/proj/main" rev-parse "$1"
}

# ── No worktree holds the default branch ─────────────────────────────────────

@test "advances the default branch when no worktree holds it" {
  _make_container
  _leave_default_branch
  _advance_origin

  cd "$TMPDIR/proj/main" || return 1
  run "$SCRIPT" main

  [ "$status" -eq 0 ]
  [ "$(_sha main)" = "$(_sha origin/main)" ]
}

@test "leaves the branch in the working directory alone" {
  _make_container
  _leave_default_branch
  local before
  before="$(_sha feat/other)"
  _advance_origin

  cd "$TMPDIR/proj/main" || return 1
  run "$SCRIPT" main

  [ "$status" -eq 0 ]
  # feat/other sits exactly where main did, so a fast-forward aimed at the
  # working directory instead of at the branch would have moved it.
  [ "$(_sha feat/other)" = "$before" ]
}

@test "fails loudly when the default branch has diverged from origin" {
  _make_container
  _leave_default_branch
  echo local > "$TMPDIR/proj/main/b.txt"
  git -C "$TMPDIR/proj/main" add -A
  git -C "$TMPDIR/proj/main" commit --quiet -m "local only"
  # Move the default branch onto the divergent commit without checking it out.
  git -C "$TMPDIR/proj/main" update-ref refs/heads/main "$(_sha feat/other)"
  _advance_origin

  cd "$TMPDIR/proj/main" || return 1
  run "$SCRIPT" main

  [ "$status" -ne 0 ]
  [[ "$output" == *"Refusing to branch from a stale 'main'"* ]]
}

# ── A worktree holds the default branch ──────────────────────────────────────

@test "fast-forwards the worktree holding the default branch" {
  _make_container
  _advance_origin

  cd "$TMPDIR/proj/main" || return 1
  run "$SCRIPT" main

  [ "$status" -eq 0 ]
  [ "$(_sha main)" = "$(_sha origin/main)" ]
  [ "$(_sha HEAD)" = "$(_sha origin/main)" ]
}

@test "fast-forwards a worktree that is not the working directory" {
  _make_container
  git -C "$TMPDIR/proj/.git" worktree add --quiet -b feat/work "$TMPDIR/proj/work" main
  _advance_origin

  cd "$TMPDIR/proj/work" || return 1
  run "$SCRIPT" main

  [ "$status" -eq 0 ]
  [ "$(_sha main)" = "$(_sha origin/main)" ]
  [ "$(_sha feat/work)" != "$(_sha origin/main)" ]
}

@test "fails loudly when the held default branch cannot fast-forward" {
  _make_container
  echo local > "$TMPDIR/proj/main/b.txt"
  git -C "$TMPDIR/proj/main" add -A
  git -C "$TMPDIR/proj/main" commit --quiet -m "local only"
  _advance_origin

  cd "$TMPDIR/proj/main" || return 1
  run "$SCRIPT" main

  [ "$status" -ne 0 ]
  [[ "$output" == *"could not fast-forward"* ]]
}

# ── Arguments ────────────────────────────────────────────────────────────────

@test "requires a branch argument" {
  _make_container

  cd "$TMPDIR/proj/main" || return 1
  run "$SCRIPT"

  [ "$status" -eq 2 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "--help exits 0 outside a repository" {
  cd "$TMPDIR" || return 1
  run "$SCRIPT" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}
