#!/usr/bin/env bash
# Code review generation for branch changes and existing PRs.
#
# Requires [`ai/core.sh`](#aicoresh) to be sourced first. Each diff section is
# compacted independently through `_compact_diff`, so a large file in one section
# cannot crowd the others out.
#
# State set by its functions: `AI_RESPONSE`.

# shellcheck source=compact_diff.sh
if [ -n "${BASH_SOURCE:-}" ]; then
  . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/compact_diff.sh"
else
  . "${TASKFILE_DIR:?review.sh requires BASH_SOURCE or TASKFILE_DIR}/lib/ai/compact_diff.sh"
fi

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

  run_ai "$(prompt_diff_review "$context")" "reviewer" "diff-review"
}

# generate_pr_review PR_NUMBER PR_TITLE PR_BODY PR_DIFF
# Requires AI_COMMAND.
# Builds a review prompt from PR metadata and its diff.
# Sets AI_RESPONSE.
generate_pr_review() {
  local pr_number="$1" pr_title="$2" pr_body="$3" pr_diff="$4"

  local compact_diff
  compact_diff=$(_compact_diff "$pr_diff")

  run_ai "$(prompt_pr_review "$pr_number" "$pr_title" "$pr_body" "$compact_diff")" "reviewer" "pr-review"
}
