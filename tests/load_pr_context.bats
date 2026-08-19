#!/usr/bin/env bats
# Coverage for load_pr_context's DEFAULT_BRANCH derivation in lib/ai/pr.sh —
# regression coverage for the origin/HEAD-missing bug. `git rev-parse
# --abbrev-ref origin/HEAD` echoes "origin/HEAD" to stdout even when it fails,
# so the old derivation defeated its own "${DEFAULT_BRANCH:-main}" fallback.
# `git symbolic-ref` prints nothing on failure, so the fallback actually fires.

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_HOME="$HOME"
  ORIG_PATH="$PATH"
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR"
  # Prevent git from discovering the parent workbench repo during parallel test runs
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  # Re-derive TASKFILE_ENV for the test HOME (constants.sh resolves at source time)
  # shellcheck disable=SC2034  # read by load_ai_command / load_gh_token
  TASKFILE_ENV="$HOME/.config/task/taskfile.env"
  unset GH_TOKEN
}

teardown() {
  export HOME="$ORIG_HOME"
  PATH="$ORIG_PATH"
  cd "$ORIG_DIR" || return 1
  rm -rf "$TMPDIR"
  unset GH_TOKEN
  common_teardown
}

@test "falls back to main when origin/HEAD is missing" {
  make_fake_gh 0 ""
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  export GH_TOKEN="github_pat_test"

  _make_repo_no_default_branch "$TMPDIR" "main" "feature/test"

  # Confirm the precondition this test relies on — no symref means the fix's
  # `git symbolic-ref` line (not `rev-parse --abbrev-ref`) is the one being exercised.
  ! git -C "$TMPDIR/repo" symbolic-ref refs/remotes/origin/HEAD &>/dev/null

  cd "$TMPDIR/repo"
  load_pr_context
  [ "$DEFAULT_BRANCH" = "main" ]
}

@test "resolves origin/HEAD to a non-main default branch" {
  make_fake_gh 0 ""
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  export GH_TOKEN="github_pat_test"

  _make_repo_no_default_branch "$TMPDIR" "trunk" "feature/test"
  # Point origin/HEAD explicitly, the way `git remote set-head origin -a` would
  git -C "$TMPDIR/repo" fetch origin --quiet
  git -C "$TMPDIR/repo" remote set-head origin trunk

  cd "$TMPDIR/repo"
  load_pr_context
  [ "$DEFAULT_BRANCH" = "trunk" ]
}
