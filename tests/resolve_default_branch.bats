#!/usr/bin/env bats
# Coverage for resolve_default_branch in lib/ai/core.sh — the shared derivation
# used by load_pr_context (lib/ai/pr.sh) and the `review:` Taskfile task. `git
# rev-parse --abbrev-ref origin/HEAD` echoes "origin/HEAD" to stdout even when
# it fails, which used to defeat a "${DEFAULT_BRANCH:-main}" fallback at both
# call sites. `git symbolic-ref` prints nothing on failure, so the fallback here
# actually fires.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  # Prevent git from discovering the parent workbench repo during parallel test runs
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  export GIT_CONFIG_GLOBAL=/dev/null
}

teardown() {
  cd "$ORIG_DIR" || return 1
  rm -rf "$TMPDIR"
  common_teardown
}

@test "falls back to main when origin/HEAD is missing" {
  _make_repo_no_default_branch "$TMPDIR" "main"

  # Confirm the precondition this test relies on — no symref means the fix's
  # `git symbolic-ref` line (not `rev-parse --abbrev-ref`) is the one being exercised.
  run ! git -C "$TMPDIR/repo" symbolic-ref refs/remotes/origin/HEAD &>/dev/null

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$status" -eq 0 ]
  [ "$output" = "main" ]
}

@test "resolves origin/HEAD to a non-main default branch" {
  _make_repo_no_default_branch "$TMPDIR" "trunk"
  git -C "$TMPDIR/repo" fetch origin --quiet
  # Point origin/HEAD explicitly, the way `git remote set-head origin -a` would
  git -C "$TMPDIR/repo" remote set-head origin trunk

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$status" -eq 0 ]
  [ "$output" = "trunk" ]
}

@test "falls back to master when origin/HEAD is missing and only master exists" {
  _make_repo_no_default_branch "$TMPDIR" "master"

  # Confirm the precondition: no symref, and no origin/main to fall back to —
  # only the "prefer an existing ref" branch of the fallback can produce "master".
  run ! git -C "$TMPDIR/repo" symbolic-ref refs/remotes/origin/HEAD &>/dev/null
  run ! git -C "$TMPDIR/repo" show-ref --verify --quiet refs/remotes/origin/main

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$status" -eq 0 ]
  [ "$output" = "master" ]
}

@test "prefers the symref over existing main/master candidates" {
  _make_repo_no_default_branch "$TMPDIR" "trunk"
  # Give the remote a "main" branch too, so the fallback candidate list has
  # something to (wrongly) prefer if the symref were not checked first.
  git -C "$TMPDIR/repo" checkout -b main --quiet
  git -C "$TMPDIR/repo" push origin main --quiet
  git -C "$TMPDIR/repo" checkout trunk --quiet
  git -C "$TMPDIR/repo" fetch origin --quiet
  # Point origin/HEAD explicitly, the way `git remote set-head origin -a` would
  git -C "$TMPDIR/repo" remote set-head origin trunk

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$status" -eq 0 ]
  [ "$output" = "trunk" ]
}

@test "falls back to the literal main when neither main nor master exist" {
  _make_repo_no_default_branch "$TMPDIR" "develop"

  run ! git -C "$TMPDIR/repo" symbolic-ref refs/remotes/origin/HEAD &>/dev/null
  run ! git -C "$TMPDIR/repo" show-ref --verify --quiet refs/remotes/origin/main
  run ! git -C "$TMPDIR/repo" show-ref --verify --quiet refs/remotes/origin/master

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$status" -eq 0 ]
  [ "$output" = "main" ]
}

@test "remote_branch_ref_exists succeeds for a branch that has a remote-tracking ref" {
  _make_repo_no_default_branch "$TMPDIR" "main"

  cd "$TMPDIR/repo"
  run remote_branch_ref_exists "main"
  [ "$status" -eq 0 ]
}

@test "remote_branch_ref_exists fails for a branch with no remote-tracking ref" {
  # Only "trunk" exists on the remote — "main" has no refs/remotes/origin/main.
  _make_repo_no_default_branch "$TMPDIR" "trunk"

  cd "$TMPDIR/repo"
  run remote_branch_ref_exists "main"
  [ "$status" -ne 0 ]
}
