#!/usr/bin/env bats
# Coverage for load_pr_context's DEFAULT_BRANCH derivation in lib/ai/pr.sh —
# regression coverage for the origin/HEAD-missing bug. `git rev-parse
# --abbrev-ref origin/HEAD` echoes "origin/HEAD" to stdout even when it fails,
# so the old derivation defeated its own "${DEFAULT_BRANCH:-main}" fallback.
# `git symbolic-ref` prints nothing on failure, so the fallback actually fires.
bats_require_minimum_version 1.5.0

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
  run ! git -C "$TMPDIR/repo" symbolic-ref refs/remotes/origin/HEAD &>/dev/null

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

@test "refuses when the resolved default branch has no remote-tracking ref" {
  make_fake_gh 0 ""
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  export GH_TOKEN="github_pat_test"

  # No origin/HEAD symref (no `remote set-head` call) and the remote's only
  # branch is "trunk" — resolve_default_branch finds neither a symref nor an
  # origin/main or origin/master ref, so it falls back to the literal guess
  # "main", which does not exist as a remote-tracking ref in this repo.
  _make_repo_no_default_branch "$TMPDIR" "trunk" "feature/test"

  cd "$TMPDIR/repo"
  run load_pr_context
  [ "$status" -ne 0 ]
  [[ "$output" == *"origin/main"* ]]
}

@test "a resolving --base override succeeds even when the guessed default does not" {
  make_fake_gh 0 ""
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  export GH_TOKEN="github_pat_test"

  # Same setup as the refusal test above: the guess lands on "main", which
  # doesn't exist. An explicit --base naming the real branch ("trunk") must
  # be honored instead of being overridden by the guess.
  _make_repo_no_default_branch "$TMPDIR" "trunk" "feature/test"

  cd "$TMPDIR/repo"
  export PR_BASE="trunk"
  run load_pr_context
  unset PR_BASE
  [ "$status" -eq 0 ]
}

@test "a non-resolving --base override still refuses, naming the branch passed" {
  make_fake_gh 0 ""
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  export GH_TOKEN="github_pat_test"

  # Default branch resolution succeeds fine here ("main" exists) — only the
  # explicit --base is bogus, and the guard must refuse on that basis and
  # name it, not silently fall through to the (perfectly fine) default.
  _make_repo_no_default_branch "$TMPDIR" "main" "feature/test"

  cd "$TMPDIR/repo"
  export PR_BASE="does-not-exist"
  run load_pr_context
  unset PR_BASE
  [ "$status" -ne 0 ]
  [[ "$output" == *"origin/does-not-exist"* ]]
  [[ "$output" != *"origin/main does not resolve"* ]]
}
