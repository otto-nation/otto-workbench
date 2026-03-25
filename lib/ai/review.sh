#!/bin/bash
# AI code review helpers.
# Requires lib/ai/core.sh to be sourced first.
#
# Functions:
#   generate_diff_review  — review staged, unstaged, and committed branch changes
#   generate_pr_review    — review an existing PR by number
#
# State set by functions: AI_RESPONSE

# generate_diff_review STAGED UNSTAGED COMMITS COMMITTED_DIFF BRANCH DEFAULT_BRANCH
# Requires AI_COMMAND.
# Builds a review prompt from three diff sources (committed, staged, unstaged).
# Each section is independently compacted via _compact_diff.
# Sets AI_RESPONSE.
generate_diff_review() {
  local staged="$1" unstaged="$2" commits="$3" committed_diff="$4" branch="$5" default_branch="$6"

  local context=""

  if [ -n "$committed_diff" ]; then
    local compact_committed
    compact_committed=$(_compact_diff "$committed_diff")
    context+="== Committed changes on $branch (vs $default_branch) ==
Commits:
$commits

Diff:
$compact_committed

"
  fi

  if [ -n "$staged" ]; then
    local compact_staged
    compact_staged=$(_compact_diff "$staged")
    context+="== Staged changes (ready to commit) ==
$compact_staged

"
  fi

  if [ -n "$unstaged" ]; then
    local compact_unstaged
    compact_unstaged=$(_compact_diff "$unstaged")
    context+="== Unstaged changes ==
$compact_unstaged"
  fi

  local ai_prompt="Review the following code changes and provide actionable feedback.

Focus on:
- Bugs, logic errors, and edge cases
- Security vulnerabilities
- Performance concerns
- Code quality and maintainability
- Missing error handling
- Improvements worth making

Be concise and direct. Group feedback by section when relevant. Skip sections with no issues.

$context

Provide a brief summary first, then specific findings."

  run_ai "$ai_prompt"
}

# generate_pr_review PR_NUMBER PR_TITLE PR_BODY PR_DIFF
# Requires AI_COMMAND.
# Builds a review prompt from PR metadata and its diff.
# Sets AI_RESPONSE.
generate_pr_review() {
  local pr_number="$1" pr_title="$2" pr_body="$3" pr_diff="$4"

  local compact_diff
  compact_diff=$(_compact_diff "$pr_diff")

  local ai_prompt="Review this pull request and provide actionable feedback.

PR #$pr_number: $pr_title

Description:
$pr_body

Focus on:
- Bugs, logic errors, and edge cases
- Security vulnerabilities
- Performance concerns
- Code quality and maintainability
- Missing error handling
- Whether the changes match the PR description
- Missing tests

Be concise and direct. Group findings by file or category. Skip areas with no issues.

Diff:
$compact_diff

Provide a brief overall summary first, then specific findings."

  run_ai "$ai_prompt"
}
