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

@test "a named issue is not confirmed a second time" {
  # Every issue reaching this function was named by someone — --issue, or typed
  # at the resolve prompt. Asking again is a prompt after an answer, and an
  # unattended run answers it N, which is how a linked PR becomes unlinked.
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
  # shellcheck disable=SC2034  # read by _pr_append_issue_link in lib/ai/pr.sh
  SKIP_ISSUE=true
  _pr_append_issue_link 1267
  [ "$(_first_line)" = "## What" ]
}

@test "the link is written without a terminal to prompt at" {
  # task pr:create runs unattended in CI and from agents. Anything conditional
  # on a tty here decides the issue link by how the command was invoked.
  _pr_append_issue_link 1267 < /dev/null
  [ "$(_first_line)" = "Closes #1267" ]
}

@test "--issue is what the resolver hands on" {
  PR_ISSUE_OVERRIDE=1267
  _pr_resolve_issue "isaac/some/branch"
  [ "$PR_ISSUE" = "1267" ]
}

@test "a branch name yields only keys the linker declines" {
  # The branch regex matches [A-Z]+-[0-9]+ and nothing else, so an inferred
  # issue is always Jira-shaped and always fails the numeric gate. That is why
  # no issue reaching the linker is a guess, and why it confirms nothing.
  # shellcheck disable=SC2034  # read by _pr_resolve_issue in lib/ai/pr.sh
  PR_ISSUE_OVERRIDE=""
  _pr_resolve_issue "carlos/PROJ-42/oauth_login"
  [ "$PR_ISSUE" = "PROJ-42" ]

  _pr_append_issue_link "$PR_ISSUE"
  [ "$(_first_line)" = "## What" ]
}
