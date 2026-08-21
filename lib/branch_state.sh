#!/usr/bin/env bash
# "Has this branch's work finished?", shared by the cleanup tools.
#
# The answer comes from the issue tracker rather than from git whenever a PR
# exists, because git cannot always give one: a branch descending from a
# different root has no merge base with the default branch, so `git cherry` and
# `git branch --merged` have nothing to compare and report it unmerged forever.
# The PR state is the only signal that survives a re-rooted repo.
#
# The lookup is batched — one `gh pr list` for the whole repo, not one `gh pr
# view` per branch, which cost a sequential round trip each.
#
# ```bash
# declare -A states
# branch_pr_states states || echo "no tracker available"
# echo "${states[feat/x]:-}"   # OPEN | MERGED | CLOSED, or empty
# ```
#
# Bash-only — the state map is an associative array returned through a nameref.
# Sourced directly by scripts that already load `lib/ui.sh` or on its own;
# it depends only on `gh` and `jq`.

# One page covers every repo this has been pointed at (738 PRs in the largest).
# A truncated page is reported rather than silently dropping branches — see
# branch_pr_states.
_BRANCH_PR_LIMIT=1000

# branch_gh_available — whether gh can answer for the current repo.
branch_gh_available() {
  command -v gh >/dev/null 2>&1 || return 1
  gh auth status >/dev/null 2>&1
}

# _branch_pr_json — the raw PR page, or an empty array when the call fails.
#
# Kept separate from the grouping below so the caller can count what the page
# actually held; once grouped, that count is gone.
_branch_pr_json() {
  gh pr list --state all --limit "$_BRANCH_PR_LIMIT" \
    --json headRefName,state 2>/dev/null || echo '[]'
}

# _branch_pr_rows JSON — one `branch<TAB>state` row per branch with a PR.
#
# A branch can carry several PRs over its life, so the rows are grouped and
# reduced to the state that decides what may be done to it: an open PR outranks
# everything, and a merge outranks an unmerged close.
_branch_pr_rows() {
  jq -r '
    group_by(.headRefName)[]
    | [ .[0].headRefName,
        (map(.state)
         | if index("OPEN") then "OPEN"
           elif index("MERGED") then "MERGED"
           else "CLOSED" end) ]
    | @tsv' <<<"$1"
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

  local json
  json="$(_branch_pr_json)"

  local branch state
  while IFS=$'\t' read -r branch state; do
    [[ -n "$branch" ]] || continue
    __states["$branch"]="$state"
  done < <(_branch_pr_rows "$json")

  # Counted before grouping, which collapses a branch's several PRs into one
  # row and would hide a full page behind a short branch list. A full page means
  # PRs went unread, and a branch whose only PR was dropped is indistinguishable
  # from one that never had a PR at all.
  local fetched
  fetched="$(jq 'length' <<<"$json")"
  if [[ $fetched -ge $_BRANCH_PR_LIMIT ]]; then
    echo "warning: PR list hit the ${_BRANCH_PR_LIMIT}-entry limit;" \
         "some branches may be misreported as having no PR" >&2
  fi
  return 0
}
