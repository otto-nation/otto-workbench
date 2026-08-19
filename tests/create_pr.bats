#!/usr/bin/env bats
# Coverage for create_pr in lib/ai/pr.sh — gh's exit code decides success, and
# only a real /pull/<number> URL is ever reported.

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_PATH="$PATH"
  TMPDIR="$(mktemp -d)"
}

teardown() {
  PATH="$ORIG_PATH"
  rm -rf "$TMPDIR"
  common_teardown
}

@test "reports the PR URL when gh succeeds" {
  make_fake_gh 0 "https://github.com/otto-nation/otto-workbench/pull/644"

  run create_pr --title "fix: thing" --body "body"

  [ "$status" -eq 0 ]
  [[ "$output" == *"✓ Pull request created"* ]]
  [[ "$output" == *"https://github.com/otto-nation/otto-workbench/pull/644"* ]]
}

@test "forwards its arguments to gh pr create" {
  make_fake_gh 0 "https://github.com/otto-nation/otto-workbench/pull/644"

  run create_pr --title "fix: thing" --body "body" --draft

  [ "$status" -eq 0 ]
  grep -qx -- "--draft" "$GH_ARGS_FILE"
  grep -qx -- "fix: thing" "$GH_ARGS_FILE"
}

@test "fails on gh's connectivity error instead of reporting githubstatus.com" {
  # The exact text that defeated the old grep: 'https://github' is a prefix of
  # 'https://githubstatus.com', so the error message looked like a PR URL.
  make_fake_gh 1 "error connecting to api.github.com
check your internet connection or https://githubstatus.com"

  run create_pr --title "fix: thing" --body "body"

  [ "$status" -eq 1 ]
  [[ "$output" == *"✗ PR creation failed"* ]]
  [[ "$output" != *"Pull request created"* ]]
  [[ "$output" == *"error connecting to api.github.com"* ]]
}

@test "fails when gh exits non-zero even if the output contains a PR URL" {
  make_fake_gh 1 "https://github.com/otto-nation/otto-workbench/pull/644
GraphQL: something went wrong"

  run create_pr --title "fix: thing" --body "body"

  [ "$status" -eq 1 ]
  [[ "$output" == *"✗ PR creation failed"* ]]
  [[ "$output" != *"✓ Pull request created"* ]]
}

@test "fails when gh succeeds but prints no pull request URL" {
  make_fake_gh 0 "Warning: 1 uncommitted change
https://github.com/otto-nation/otto-workbench"

  run create_pr --title "fix: thing" --body "body"

  [ "$status" -eq 1 ]
  [[ "$output" == *"no pull request URL"* ]]
  [[ "$output" != *"✓ Pull request created"* ]]
}

@test "picks the pull request URL out of surrounding gh output" {
  make_fake_gh 0 "Warning: 1 uncommitted change
See https://githubstatus.com for API status
https://github.com/otto-nation/otto-workbench/pull/700"

  run create_pr --title "fix: thing" --body "body"

  [ "$status" -eq 0 ]
  [[ "${lines[-1]}" == "https://github.com/otto-nation/otto-workbench/pull/700" ]]
}
