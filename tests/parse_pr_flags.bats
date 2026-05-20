#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  source_lib
}

teardown() {
  common_teardown
}

@test "no flags sets defaults" {
  parse_pr_flags ""
  [ "$SKIP_ISSUE" = "false" ]
  [ "$PR_BASE" = "" ]
}

@test "--no-issue sets SKIP_ISSUE" {
  parse_pr_flags "--no-issue"
  [ "$SKIP_ISSUE" = "true" ]
  [ "$PR_BASE" = "" ]
}

@test "--base sets PR_BASE" {
  parse_pr_flags "--base feature/parent"
  [ "$SKIP_ISSUE" = "false" ]
  [ "$PR_BASE" = "feature/parent" ]
}

@test "--base and --no-issue together" {
  parse_pr_flags "--no-issue --base feature/parent"
  [ "$SKIP_ISSUE" = "true" ]
  [ "$PR_BASE" = "feature/parent" ]
}

@test "--base without value fails" {
  run parse_pr_flags "--base"
  [ "$status" -eq 1 ]
  [[ "$output" == *"--base requires a branch name"* ]]
}

@test "unknown flag fails" {
  run parse_pr_flags "--unknown"
  [ "$status" -eq 1 ]
  [[ "$output" == *"Unknown flag"* ]]
}
