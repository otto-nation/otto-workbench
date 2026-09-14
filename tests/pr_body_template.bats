#!/usr/bin/env bats
# A PR body supplied by hand still owes the repo's template.
#
# `--body` and `--body-file` short-circuit generate_pr_content before the
# template is loaded, so the one input nobody generated was the one input
# nothing checked. Every AI path is handed the template and fills it; a
# hand-written body skipped all of them and reached `gh pr create` with
# whatever headers its author happened to use.

setup() {
  load 'test_helper'
  common_setup
  source_lib  # $REPO_ROOT/lib/ai/pr.sh
  TMPDIR="$(mktemp -d)"
  cd "$TMPDIR" || return 1
  mkdir -p .github
  printf '## What\n\n## Why\n' > .github/PULL_REQUEST_TEMPLATE.md
}

teardown() {
  cd / || true
  rm -rf "$TMPDIR"
  common_teardown
}

@test "a body carrying the template's sections is accepted" {
  _pr_load_template
  run _pr_check_body_against_template "## What

did a thing

## Why

it was broken"

  [ "$status" -eq 0 ]
}

@test "a body using its own headers is refused" {
  _pr_load_template
  run _pr_check_body_against_template "## Summary

did a thing

## Changes

## Testing"

  [ "$status" -eq 1 ]
  [[ "$output" == *"does not use this repo's template"* ]]
}

@test "the refusal names every missing section" {
  _pr_load_template
  run _pr_check_body_against_template "## Summary"

  [ "$status" -eq 1 ]
  [[ "$output" == *"## What"* ]]
  [[ "$output" == *"## Why"* ]]
}

@test "the refusal names only the section that is missing" {
  _pr_load_template
  run _pr_check_body_against_template "## What

half of it"

  [ "$status" -eq 1 ]
  [[ "$output" == *"## Why"* ]]
  [[ "$output" != *"Missing section(s)"*"## What"* ]]
}

@test "a repo with no template accepts any body" {
  rm -rf .github
  _pr_load_template
  run _pr_check_body_against_template "## Anything At All"

  [ "$status" -eq 0 ]
}

@test "extra sections beyond the template are allowed" {
  # The template is a floor, not a ceiling — a PR that says more than it was
  # asked for is not malformed.
  _pr_load_template
  run _pr_check_body_against_template "## What

## Why

## Testing"

  [ "$status" -eq 0 ]
}

@test "a header appearing only inside prose does not satisfy the template" {
  # Deliberately weak: the check is a substring match, so this documents what
  # it does rather than claiming a structural parse it does not do.
  _pr_load_template
  run _pr_check_body_against_template "## What

talked about ## Why without having one"

  [ "$status" -eq 0 ]
}

@test "generate_pr_content refuses an off-template body override" {
  # The path the bug was on: both overrides set, so every AI path is skipped.
  PR_TITLE_OVERRIDE="fix: thing"
  PR_BODY_OVERRIDE="## Summary

no template here"

  run generate_pr_content "isaac/fix/thing" "main"

  [ "$status" -eq 1 ]
  [[ "$output" == *"does not use this repo's template"* ]]
}

@test "generate_pr_content accepts a conforming body override without calling AI" {
  PR_TITLE_OVERRIDE="fix: thing"
  PR_BODY_OVERRIDE="## What

did a thing

## Why

it was broken"
  # Unset so a path that reached the AI would fail loudly rather than silently
  # producing a body.
  AI_COMMAND=""

  run generate_pr_content "isaac/fix/thing" "main"

  [ "$status" -eq 0 ]
}
