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
  [[ "$output" == *"--base requires a value"* ]]
}

@test "unknown flag fails" {
  run parse_pr_flags "--unknown"
  [ "$status" -eq 1 ]
  [[ "$output" == *"Unknown flag"* ]]
}

@test "--title sets PR_TITLE_OVERRIDE" {
  parse_pr_flags "--title my-title"
  [ "$PR_TITLE_OVERRIDE" = "my-title" ]
  [ "$PR_BODY_OVERRIDE" = "" ]
}

@test "--body sets PR_BODY_OVERRIDE" {
  parse_pr_flags "--body my-body"
  [ "$PR_BODY_OVERRIDE" = "my-body" ]
  [ "$PR_TITLE_OVERRIDE" = "" ]
}

@test "--title and --body together" {
  parse_pr_flags "--title my-title --body my-body"
  [ "$PR_TITLE_OVERRIDE" = "my-title" ]
  [ "$PR_BODY_OVERRIDE" = "my-body" ]
}

@test "--title without value fails" {
  run parse_pr_flags "--title"
  [ "$status" -eq 1 ]
  [[ "$output" == *"--title requires a value"* ]]
}

@test "--body without value fails" {
  run parse_pr_flags "--body"
  [ "$status" -eq 1 ]
  [[ "$output" == *"--body requires a value"* ]]
}

@test "defaults include empty overrides" {
  parse_pr_flags ""
  [ "$PR_TITLE_OVERRIDE" = "" ]
  [ "$PR_BODY_OVERRIDE" = "" ]
}

@test "--title with quoted multi-word value" {
  parse_pr_flags '--title "fix: clean empty markers and fix counts"'
  [ "$PR_TITLE_OVERRIDE" = "fix: clean empty markers and fix counts" ]
}

@test "--body-file reads content from file" {
  local tmpfile
  tmpfile=$(mktemp)
  printf "line one\nline two" > "$tmpfile"
  parse_pr_flags "--body-file $tmpfile"
  rm -f "$tmpfile"
  [[ "$PR_BODY_OVERRIDE" == *"line one"* ]]
  [[ "$PR_BODY_OVERRIDE" == *"line two"* ]]
}

@test "--body-file without value fails" {
  run parse_pr_flags "--body-file"
  [ "$status" -eq 1 ]
  [[ "$output" == *"--body-file requires a value"* ]]
}

@test "--title and --body-file together" {
  local tmpfile
  tmpfile=$(mktemp)
  printf "body from file" > "$tmpfile"
  parse_pr_flags "--title \"my title\" --body-file $tmpfile"
  rm -f "$tmpfile"
  [ "$PR_TITLE_OVERRIDE" = "my title" ]
  [ "$PR_BODY_OVERRIDE" = "body from file" ]
}
