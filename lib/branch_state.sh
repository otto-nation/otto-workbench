#!/usr/bin/env bash
# "Has this branch's work finished?", shared by the cleanup tools.
#
# The answer comes from the issue tracker rather than from git whenever a PR
# exists, because git cannot always give one: a branch descending from a
# different root has no merge base with the default branch, so `git cherry` and
# `git branch --merged` have nothing to compare and report it unmerged forever.
# The PR state is the only signal that survives a re-rooted repo.
#
# The lookup asks about the branches the caller names, not about the repo. It
# used to page the whole PR history with `gh pr list --limit 1000` and index the
# result, which cost a page that grows with every merge to answer about the
# handful of branches a machine has worktrees for — and silently stopped being
# correct once a repo passed 1000 PRs, since the oldest fell off the page and
# read as "no PR at all". A branch nobody asked about is one nobody has to fetch.
#
# One REST request per branch, against the `core` hourly budget. An aliased
# GraphQL query would answer every branch for a single point, which is cheaper
# on paper and worse here: `gh pr view`, `gh pr list` and every read the review
# pipeline makes spend the *graphql* budget, and this runs unattended on every
# session exit. It has been observed asking its question with graphql exhausted
# and core almost untouched, where the batch returns no answer at all — and no
# answer means a squash-merged worktree is kept forever, since git alone cannot
# see that merge. Nothing else in `bin/` or `lib/` spends core.
#
# The per-branch cost that buys is bounded by what the caller can act on: one
# worktree each, a handful per machine.
#
# ```bash
# declare -A states
# branch_pr_states states feat/x feat/y || echo "no tracker available"
# echo "${states[feat/x]:-}"   # OPEN | MERGED | CLOSED, or empty
# ```
#
# Bash-only — the state map is an associative array returned through a nameref.
# Sourced directly by scripts that already load `lib/ui.sh` or on its own;
# it depends only on `gh` and `jq`.
#
# `ai/lib/gh/client.py` owns "how to talk to GitHub" for Python, with retries and
# budget accounting this deliberately does not reach for: its rate-limit ladder
# waits up to nine minutes, which inside a `--quiet` janitor is a hang nobody
# sees. Nothing owns that question for bash, and the two `gh` calls below are not
# yet a reason to build an owner — a third caller would be.

# PRs per page. A branch with more than one page of PRs over its life does not
# exist, and the reduction below only asks whether any is open and whether any
# merged, which a second page could not change.
#
# ceiling: one page rather than --paginate, upgrade if a branch ever carries more
# than 100 PRs — an earlier page holding the merge would read as CLOSED.
_BRANCH_PR_PAGE=100

# The return code for a question GitHub refused to answer, as distinct from one
# it could never have answered here. A caller that acts on the absence of a PR
# has to tell the two apart — see the note on `branch_pr_states`.
BRANCH_PR_REFUSED_RC=2

# How GitHub words a budget it will not spend on us. The first two are the
# hourly primary quota and are the same strings `_BUDGET_EXHAUSTED_MARKERS` in
# `ai/lib/gh/budget.py` matches for the Python half; they are restated rather
# than shared because nothing imports across that boundary, and a drift between
# the two costs a refusal that should have been best-effort, not a wrong delete.
#
# The third has no counterpart there. `budget.py` deliberately leaves the
# secondary limit out because its retry ladder rides through one; nothing here
# retries, so for this caller a throttled read is simply a read that did not
# happen.
_BRANCH_PR_BUDGET_MARKERS=(
  "api rate limit exceeded"
  "api rate limit already exceeded"
  "secondary rate limit"
)

# branch_gh_available — whether gh can answer for the current repo.
branch_gh_available() {
  command -v gh >/dev/null 2>&1 || return 1
  gh auth status >/dev/null 2>&1
}

# _branch_pr_state BRANCH — OPEN, MERGED or CLOSED on stdout, or nothing at all
# when the branch has no PR. Returns $BRANCH_PR_REFUSED_RC when GitHub refused
# to answer, and 1 when the question could not be put at all.
#
# A branch can carry several PRs over its life, so they are reduced to the state
# that decides what may be done to it: an open PR outranks everything, and a
# merge outranks an unmerged close.
#
# REST has no "merged" state — a merged PR is `state: "closed"` with a non-null
# `merged_at`, and an abandoned one is `state: "closed"` with a null one. Reading
# the state field alone collapses the two, and the difference is exactly what the
# caller removes a worktree on.
#
# The `{owner}:` prefix on `head` is load-bearing rather than decorative: GitHub
# ignores the filter outright without it and answers with the repo's most recent
# hundred PRs, which this reduction reads as the branch being OPEN. It is gh's
# own placeholder, so the slug costs no round trip — but it expands only inside
# the endpoint string, never inside a `-f` value.
_branch_pr_state() {
  local branch="$1" encoded

  # An empty name would filter on `head={owner}:` — the unfiltered page again,
  # and an answer about a branch that was never named.
  [[ -n "$branch" ]] || return 1

  # A `&`, `#` or `?` in a branch name would otherwise end the query parameter
  # and take the rest of the name with it.
  encoded=$(jq -rn --arg b "$branch" '$b|@uri') || return 1

  # stdout is the answer and stderr is the diagnosis, so the two are split
  # rather than both discarded: whether a failure was a spent quota or something
  # this repo could never answer is the whole of the caller's decision. fd 3
  # carries the answer out untouched while stderr is captured for reading.
  # The classic fd-3 stdout/stderr swap: `3>&1` hands the outer subshell's
  # original stdout to fd 3, then `2>&1 1>&3` inside the command substitution
  # sends stderr where stdout used to go (captured by `said=$(...)`) and
  # redirects stdout to fd 3 (the real terminal/caller stdout) — swapping which
  # stream the substitution captures.
  local said rc=0
  { said=$(gh api \
    "repos/{owner}/{repo}/pulls?state=all&head={owner}:${encoded}&per_page=$_BRANCH_PR_PAGE" \
    --jq 'if   any(.[]; .state == "open")   then "OPEN"
          elif any(.[]; .merged_at != null) then "MERGED"
          elif length > 0                   then "CLOSED"
          else empty end' 2>&1 1>&3); } 3>&1 || rc=$?

  [[ $rc -eq 0 ]] || _branch_pr_refusal "$said"
}

# _branch_pr_refusal SAID — the return code for a failed request, read from what
# gh said on stderr: $BRANCH_PR_REFUSED_RC for a refusal, 1 for anything else.
#
# Only a spent budget earns the refusal. A missing gh, no auth, no network, a
# repo the token cannot see, and a remote that is not GitHub at all are all "this
# machine cannot answer this, now or later" — a caller that refused on those
# would never clean up a worktree on a GitLab repo or on a laptop offline.
#
# `ai/lib/gh/landed.py` draws the line in the same place for the same reason:
# only a budget refusal clears its `looked` flag, because the distinction that
# matters is whether the answer was knowable, not whether the failure was
# transient.
_branch_pr_refusal() {
  local lower="${1,,}" marker
  for marker in "${_BRANCH_PR_BUDGET_MARKERS[@]}"; do
    if [[ "$lower" == *"$marker"* ]]; then
      return "$BRANCH_PR_REFUSED_RC"
    fi
  done
  return 1
}

# branch_pr_states ASSOC_ARRAY_NAME BRANCH... — fill an associative array
# branch → state for the named branches.
#
# Leaves the array empty and returns non-zero when the lookup did not answer,
# so callers can carry on with git-only signals rather than treating it as fatal.
# Which non-zero says whether carrying on is safe:
#
#   1                       — the question could not be put here at all: no gh,
#                             no auth, no network, not a GitHub remote. That
#                             machine has no tracker, and a caller that stopped
#                             on this would never do anything on it.
#   $BRANCH_PR_REFUSED_RC   — GitHub declined to answer a question it could have.
#                             The PR state exists and is unread, so an absence
#                             here is not evidence of anything.
#
# The distinction matters in one direction only: an empty map read as "no branch
# has a PR" is what would let an open PR's worktree be removed, so a question
# nobody could answer must not come back looking like an answer.
#
# Naming no branches is not an error — it is a caller with nothing to ask about,
# which is answered with an empty map and no round trip.
branch_pr_states() {
  local -n __states="$1"
  shift
  __states=()

  [[ $# -gt 0 ]] || return 0
  branch_gh_available || return 1

  # Accumulated and assigned in one pass at the end: a branch that fails after an
  # earlier one answered must not leave a half-filled map, which reports the
  # branches it reached and silently denies the rest.
  local -a rows=()
  local branch state
  for branch in "$@"; do
    # `return $?`, not `return 1`: flattening the two here is the whole bug the
    # refusal code exists to prevent, and it reads as a harmless tidy-up.
    state=$(_branch_pr_state "$branch") || return $?
    # if, not `[[ ]] &&`: this is the last statement in the loop body, so under
    # `set -e` a false test on the final branch would return 1 from the whole
    # function — reporting "no tracker" for a lookup that answered.
    if [[ -n "$state" ]]; then
      rows+=("${branch}"$'\t'"${state}")
    fi
  done

  local row
  for row in ${rows[@]+"${rows[@]}"}; do
    IFS=$'\t' read -r branch state <<<"$row"
    __states["$branch"]="$state"
  done

  return 0
}
