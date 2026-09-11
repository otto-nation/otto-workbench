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
  [ "$PR_ISSUE_OVERRIDE" = "" ]
  [ "${#PR_CLOSES[@]}" -eq 0 ]
}

@test "--closes sets PR_CLOSES" {
  parse_pr_flags "--closes 941"
  [ "${#PR_CLOSES[@]}" -eq 1 ]
  [ "${PR_CLOSES[0]}" = "#941" ]
}

@test "--closes is repeatable and keeps order" {
  parse_pr_flags "--closes 941 --closes 942"
  [ "${#PR_CLOSES[@]}" -eq 2 ]
  [ "${PR_CLOSES[0]}" = "#941" ]
  [ "${PR_CLOSES[1]}" = "#942" ]
}

@test "--closes normalises a leading #" {
  parse_pr_flags "--closes #941"
  [ "${PR_CLOSES[0]}" = "#941" ]
}

@test "--closes without value fails" {
  run parse_pr_flags "--closes"
  [ "$status" -eq 1 ]
  [[ "$output" == *"--closes requires a value"* ]]
}

@test "--closes coexists with the other create flags" {
  parse_pr_flags "--no-issue --draft --closes 941 --title \"my title\""
  [ "$SKIP_ISSUE" = "true" ]
  [ "$PR_DRAFT" = "true" ]
  [ "$PR_TITLE_OVERRIDE" = "my title" ]
  [ "${PR_CLOSES[0]}" = "#941" ]
}

@test "a rejected --closes fails the whole parse" {
  run parse_pr_flags "--closes banana --draft"
  [ "$status" -eq 1 ]
}

@test "--issue sets PR_ISSUE_OVERRIDE" {
  parse_pr_flags "--issue ENG-123"
  [ "$PR_ISSUE_OVERRIDE" = "ENG-123" ]
}

@test "--issue without value fails" {
  run parse_pr_flags "--issue"
  [ "$status" -eq 1 ]
  [[ "$output" == *"--issue requires a value"* ]]
}

@test "--issue and --draft together" {
  parse_pr_flags "--issue PROJ-42 --draft"
  [ "$PR_ISSUE_OVERRIDE" = "PROJ-42" ]
  [ "$PR_DRAFT" = "true" ]
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
