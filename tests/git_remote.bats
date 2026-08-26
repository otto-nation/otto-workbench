#!/usr/bin/env bats
# Coverage for lib/git_remote.sh — the one ladder answering "which branch is
# trunk" for load_pr_context (lib/ai/pr.sh), the `review:` Taskfile task, both
# pre-push hooks, and bin/local/check-surface-compat. `git rev-parse
# --abbrev-ref origin/HEAD` echoes "origin/HEAD" to stdout even when it fails,
# which used to defeat a "${DEFAULT_BRANCH:-main}" fallback at both call sites.
# `git symbolic-ref` prints nothing on failure, so the fallback here actually
# fires.
#
# Sourced directly rather than through source_lib: the global pre-push hook
# loads this file on its own, without lib/ai/core.sh, and a suite that only ever
# reached it through the facade would not notice the day it stopped standing
# alone. `sources standalone` below asserts that reachability explicitly.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/git_remote.sh
  . "$REPO_ROOT/lib/git_remote.sh"
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  # Prevent git from discovering the parent workbench repo during parallel test runs
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
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

# ── The DIR argument ─────────────────────────────────────────────────────────

@test "answers for the directory given rather than the current one" {
  # bin/local/check-surface-compat resolves the base for --repo-dir, which is
  # not necessarily where it is standing. Asked from outside any repository, the
  # cwd default would fail rather than answer for the repo named.
  _make_repo_no_default_branch "$TMPDIR" "master"

  cd "$TMPDIR"
  run resolve_default_branch "$TMPDIR/repo"
  [ "$status" -eq 0 ]
  [ "$output" = "master" ]
}

# ── default_base_ref ─────────────────────────────────────────────────────────

@test "default_base_ref names the remote-tracking ref of the default branch" {
  _make_repo_no_default_branch "$TMPDIR" "master"

  cd "$TMPDIR/repo"
  run default_base_ref
  [ "$status" -eq 0 ]
  [ "$output" = "origin/master" ]
}

@test "default_base_ref follows the symref rather than the candidate list" {
  _make_repo_no_default_branch "$TMPDIR" "trunk"
  git -C "$TMPDIR/repo" fetch origin --quiet
  git -C "$TMPDIR/repo" remote set-head origin trunk

  cd "$TMPDIR/repo"
  run default_base_ref
  [ "$status" -eq 0 ]
  [ "$output" = "origin/trunk" ]
}

@test "default_base_ref refuses when the resolved branch is only a guess" {
  # The rung resolve_default_branch ends on: no symref, no origin/main, no
  # origin/master, so the literal "main" is all it has. Handing that to `git
  # diff` yields "unknown revision", which reads as a broken repository — the
  # callers that are about to diff need to hear "no base" instead.
  _make_repo_no_default_branch "$TMPDIR" "develop"

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$output" = "main" ]

  run default_base_ref
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

# ── Standalone reachability ──────────────────────────────────────────────────

@test "sources standalone, with no other workbench library loaded" {
  # What the global pre-push hook does: source this one file into a bare shell
  # and call it. A dependency added here would refuse a push in every repository
  # on the machine, so it is asserted rather than assumed.
  _make_repo_no_default_branch "$TMPDIR" "master"

  run env -i PATH="$PATH" HOME="$HOME" bash -c \
    ". '$REPO_ROOT/lib/git_remote.sh' && cd '$TMPDIR/repo' && default_base_ref"
  [ "$status" -eq 0 ]
  [ "$output" = "origin/master" ]
}

@test "sources under a POSIX shell without emitting a bashism" {
  # lib/ai/core.sh sources this file, and go-task runs the tasks that source
  # core.sh under /bin/sh — dash on CI. A `[[` here does not abort the source,
  # it writes "[[: not found" to stderr and carries on, so the caller's next
  # `$(...)` capture silently gains a line of shell diagnostics.
  #
  # dash, not sh: macOS /bin/sh is bash, which would run the bashism happily.
  _make_repo_no_default_branch "$TMPDIR" "master"

  run dash -c \
    ". '$REPO_ROOT/lib/git_remote.sh' && cd '$TMPDIR/repo' && default_base_ref" 2>&1
  [ "$status" -eq 0 ]
  [ "$output" = "origin/master" ]
}

@test "lib/ai/core.sh still re-exports the ladder to its own callers" {
  # load_pr_context and the `review:` Taskfile task call resolve_default_branch
  # having sourced only core.sh. Moving the function out must not have moved it
  # out of their reach.
  source_lib
  _make_repo_no_default_branch "$TMPDIR" "master"

  cd "$TMPDIR/repo"
  run resolve_default_branch
  [ "$status" -eq 0 ]
  [ "$output" = "master" ]
  [ "$GIT_REMOTE" = "origin" ]
}
