#!/usr/bin/env bash
# Branch state shared by the cleanup tools — "has this branch's work finished?"
#
# The answer comes from the issue tracker rather than from git whenever a PR
# exists, because git cannot always answer it: a branch descending from a
# different root has no merge base with the default branch, so `git cherry` and
# `git branch --merged` have nothing to compare and report it as unmerged
# forever. The PR state is the only signal that survives a re-rooted repo.
#
# Usage (from scripts that already source lib/ui.sh, or by sourcing this file
# directly — it depends only on git, gh, and jq):
#   declare -A states
#   branch_pr_states states || echo "no tracker available"
#   echo "${states[feat/x]:-}"   # OPEN | MERGED | CLOSED, or empty

# One page covers every repo this has been pointed at (738 PRs in the largest).
# A truncated page is reported rather than silently dropping branches — see
# branch_pr_states.
_BRANCH_PR_LIMIT=1000

# branch_gh_available — whether gh can answer for the current repo.
branch_gh_available() {
  command -v gh >/dev/null 2>&1 || return 1
  gh auth status >/dev/null 2>&1
}

# _branch_pr_rows — one `branch<TAB>state` row per branch with a PR.
#
# A branch can carry several PRs over its life, so the rows are grouped and
# reduced to the state that decides what may be done to it: an open PR outranks
# everything, and a merge outranks an unmerged close.
_branch_pr_rows() {
  gh pr list --state all --limit "$_BRANCH_PR_LIMIT" \
    --json headRefName,state 2>/dev/null \
    | jq -r '
        group_by(.headRefName)[]
        | [ .[0].headRefName,
            (map(.state)
             | if index("OPEN") then "OPEN"
               elif index("MERGED") then "MERGED"
               else "CLOSED" end) ]
        | @tsv'
}

# branch_pr_states ASSOC_ARRAY_NAME — fill an associative array branch → state.
#
# One `gh pr list` call for the whole repo, not one `gh pr view` per branch: the
# per-branch form costs a sequential round trip each and is what made a sweep
# over a hundred branches unusable.
#
# Returns 1 and leaves the array empty when no tracker is reachable, so callers
# can carry on with git-only signals rather than treating it as fatal.
branch_pr_states() {
  local -n __states="$1"
  __states=()

  branch_gh_available || return 1

  local branch state rows=0
  while IFS=$'\t' read -r branch state; do
    [[ -n "$branch" ]] || continue
    __states["$branch"]="$state"
    rows=$((rows + 1))
  done < <(_branch_pr_rows)

  # Grouping collapses branches, so this can only undercount — a repo at the
  # limit has PRs that were never read, and a branch whose only PR is missing
  # looks like a branch with no PR at all.
  if [[ $rows -ge $_BRANCH_PR_LIMIT ]]; then
    echo "warning: PR list hit the ${_BRANCH_PR_LIMIT}-entry limit;" \
         "some branches may be misreported as having no PR" >&2
  fi
  return 0
}
