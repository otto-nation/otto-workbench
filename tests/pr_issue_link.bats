#!/usr/bin/env bats
# Tests for _pr_append_issue_link — whether a PR closes its issue on merge.
#
# The flag reaching PR_ISSUE_OVERRIDE is covered by parse_pr_flags.bats, which
# stops one call short of the point where it stopped working: a PR template
# suppressed the link outright, so `--issue` printed "Using issue: N", wrote no
# closing keyword, and left the issue open after the merge.

setup() {
  load 'test_helper'
  common_setup
  source_lib
  SKIP_ISSUE=false
  PR_ISSUE_EXPLICIT=true
  PR_DESCRIPTION="## What

Some change."
}

teardown() {
  common_teardown
}

# The first line of the description, which is where a closing keyword has to be
# for GitHub to act on it.
_first_line() {
  head -1 <<< "$PR_DESCRIPTION"
}

@test "an explicit issue is linked even though this repo has a PR template" {
  # The regression. .github/PULL_REQUEST_TEMPLATE.md exists here and carries no
  # issue field, so suppressing the link on its account left nothing to close
  # the issue.
  [ -f "$REPO_ROOT/.github/PULL_REQUEST_TEMPLATE.md" ]

  run _pr_append_issue_link 1267
  [ "$status" -eq 0 ]

  _pr_append_issue_link 1267
  [ "$(_first_line)" = "Closes #1267" ]
}

@test "the original description survives underneath the link" {
  _pr_append_issue_link 1267
  [[ "$PR_DESCRIPTION" == *"## What"* ]]
  [[ "$PR_DESCRIPTION" == *"Some change."* ]]
}

@test "an explicit issue is not confirmed interactively" {
  # --issue is the answer. Re-asking is a prompt an unattended run answers N to,
  # which is how a linked PR silently becomes an unlinked one.
  run _pr_append_issue_link 1267
  [[ "$output" != *"when PR merges?"* ]]
}

@test "a description that already closes the issue is not given a second link" {
  PR_DESCRIPTION="Closes #1267

## What"
  _pr_append_issue_link 1267
  [ "$(grep -c 'Closes #1267' <<< "$PR_DESCRIPTION")" -eq 1 ]
}

@test "any of GitHub's closing keywords counts as already linked" {
  local keyword
  for keyword in Closes Fixes Resolves closed fixed resolved; do
    PR_DESCRIPTION="$keyword #1267 in passing"
    _pr_append_issue_link 1267
    [ "$(_first_line)" = "$keyword #1267 in passing" ]
  done
}

@test "a keyword naming another issue does not count as this one's link" {
  PR_DESCRIPTION="Closes #999"
  _pr_append_issue_link 1267
  [ "$(_first_line)" = "Closes #1267" ]
}

@test "a longer number starting with this one is not this issue's link" {
  # "#12670" contains "#1267" — a substring match would read it as linked and
  # write nothing, leaving the real issue open.
  PR_DESCRIPTION="Closes #12670"
  _pr_append_issue_link 1267
  [ "$(_first_line)" = "Closes #1267" ]
}

@test "a bare mention is not a link" {
  # Prose naming the issue closes nothing on merge.
  PR_DESCRIPTION="Follow-up to #1267"
  _pr_append_issue_link 1267
  [ "$(_first_line)" = "Closes #1267" ]
}

@test "a Jira-style key gets no link" {
  # PROJ-123 does not auto-close on GitHub, so there is nothing to write.
  _pr_append_issue_link "PROJ-123"
  [ "$(_first_line)" = "## What" ]
}

@test "a leading hash on the issue number is tolerated" {
  _pr_append_issue_link "#1267"
  [ "$(_first_line)" = "Closes #1267" ]
}

@test "no issue leaves the description alone" {
  _pr_append_issue_link ""
  [ "$(_first_line)" = "## What" ]
}

@test "--no-issue leaves the description alone even with an issue in hand" {
  SKIP_ISSUE=true
  _pr_append_issue_link 1267
  [ "$(_first_line)" = "## What" ]
}

@test "an inferred issue is not linked unprompted when nobody can be asked" {
  # Only an explicit --issue is a standing answer. A number guessed from the
  # branch name still needs confirming, and a non-interactive run has no one to
  # confirm it — so it declines rather than linking an issue nobody chose.
  PR_ISSUE_EXPLICIT=false
  _pr_append_issue_link 1267 < /dev/null
  [ "$(_first_line)" = "## What" ]
}

@test "--issue records that the caller named the issue" {
  PR_ISSUE_OVERRIDE=1267
  _pr_resolve_issue "isaac/some/branch"
  [ "$PR_ISSUE" = "1267" ]
  [ "$PR_ISSUE_EXPLICIT" = "true" ]
}

@test "an issue read off the branch name is not an explicit one" {
  PR_ISSUE_OVERRIDE=""
  _pr_resolve_issue "carlos/PROJ-42/oauth_login"
  [ "$PR_ISSUE" = "PROJ-42" ]
  [ "$PR_ISSUE_EXPLICIT" = "false" ]
}
