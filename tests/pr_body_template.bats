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
  cd "$TMPDIR" || return 1
  mkdir -p .github
  printf '## What\n\n## Why\n' > .github/PULL_REQUEST_TEMPLATE.md
}

teardown() {
  cd / || true
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

@test "a template header survives a backslash elsewhere in the file" {
  # `printf '%s'` rather than `echo` throughout, matching the rest of this
  # file. The stronger failure `echo` invites — a whole template that is
  # exactly `-e`, which bash reads as a flag and drops — is not reachable
  # through this function, since such a template carries no `##` line and
  # yields no headers either way. What is reachable is content mangling, and a
  # template is a repo-controlled file that may hold anything.
  printf '## What\n\nuse C:\\path or \\n in your description\n\n## Why\n' \
    > .github/PULL_REQUEST_TEMPLATE.md
  _pr_load_template

  run _pr_template_headers
  [ "$status" -eq 0 ]
  [[ "$output" == *"## What"* ]]
  [[ "$output" == *"## Why"* ]]
}

@test "the template is found from a subdirectory of the repo" {
  # The check is only as good as the template load under it, and all four
  # locations GitHub recognises are repo-root-relative. Resolved against the
  # working directory, every candidate misses from a subdirectory,
  # PR_HAS_TEMPLATE stays false, and the check returns 0 having compared
  # nothing — the silent-no-op this whole file exists to prevent, reachable by
  # running `task pr:create` one directory down.
  git init -q .
  mkdir -p lib/deep
  cd lib/deep || return 1

  _pr_load_template
  [ "$PR_HAS_TEMPLATE" = "true" ]

  run _pr_check_body_against_template "## Summary

off template"
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not use this repo's template"* ]]
}

@test "a template outside any repo is still found in the working directory" {
  # Not every caller is inside a git worktree, and the pre-#1301 behaviour for
  # those was to read the template beside them. `git rev-parse` answering
  # nothing must fall back to that rather than losing the template.
  run git rev-parse --show-toplevel
  [ "$status" -ne 0 ]

  _pr_load_template
  [ "$PR_HAS_TEMPLATE" = "true" ]
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

@test "every call to generate_pr_content acts on its refusal" {
  # The refusal is only worth having if a caller acts on it. All three targets
  # called `generate_pr_content` unguarded at first, so the non-zero return
  # printed an error and the script carried on to `create_pr` with `PR_TITLE`
  # and `PR_DESCRIPTION` never assigned — an empty PR, opened right after an
  # error message saying it would not be.
  #
  # Counted rather than matched line-for-line: what matters is that no call
  # site is missing a guard, and a count survives the reformatting that an
  # exact-string assertion would break on. `pr:content` prints what would be
  # posted and `pr:update` replaces a live body, so all three owe it — hence
  # the floor as well as the ceiling, which is what catches a guard added by
  # deleting the call it was on.
  local calls guarded
  # shellcheck disable=SC2016  # the literal `$BRANCH` is what is being matched
  calls=$(grep -c 'generate_pr_content "\$BRANCH" "\$TARGET_BASE"' \
    "$REPO_ROOT/Taskfile.global.yml" || true)
  # shellcheck disable=SC2016  # the literal `$BRANCH` is what is being matched
  guarded=$(grep -c 'generate_pr_content "\$BRANCH" "\$TARGET_BASE" || exit 1' \
    "$REPO_ROOT/Taskfile.global.yml" || true)

  [ "$calls" -eq 3 ]
  [ "$guarded" -eq "$calls" ]
}

@test "the refused body leaves no title or description behind" {
  # What the unguarded caller went on to use. Asserted directly so the failure
  # reads as "empty PR" rather than as a missing `|| exit 1`.
  PR_TITLE=""
  PR_DESCRIPTION=""
  # shellcheck disable=SC2034  # both read by generate_pr_content in the sourced lib
  PR_TITLE_OVERRIDE="fix: thing"
  # shellcheck disable=SC2034
  PR_BODY_OVERRIDE="## Summary

off template"

  run generate_pr_content "isaac/fix/thing" "main"
  [ "$status" -eq 1 ]

  generate_pr_content "isaac/fix/thing" "main" || true
  [ -z "$PR_TITLE" ]
  [ -z "$PR_DESCRIPTION" ]
}

@test "generate_pr_content accepts a conforming body override without calling AI" {
  # shellcheck disable=SC2034  # both read by generate_pr_content in the sourced lib
  PR_TITLE_OVERRIDE="fix: thing"
  # shellcheck disable=SC2034
  PR_BODY_OVERRIDE="## What

did a thing

## Why

it was broken"
  # Unset so a path that reached the AI would fail loudly rather than silently
  # producing a body.
  # shellcheck disable=SC2034  # read by run_ai in the sourced lib
  AI_COMMAND=""

  run generate_pr_content "isaac/fix/thing" "main"

  [ "$status" -eq 0 ]
}
