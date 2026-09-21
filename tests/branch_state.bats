#!/usr/bin/env bats
# Tests for lib/branch_state.sh — the per-branch PR-state lookup shared by the
# cleanup tools.

setup_file() {
  load 'test_helper'
  MOCK_BIN="$BATS_FILE_TMPDIR/bin"
  mkdir -p "$MOCK_BIN"

  # Answers `gh api repos/{owner}/{repo}/pulls?...` the way REST does: an array
  # of `{state, merged_at}`, where a merged PR is `closed` with a timestamp.
  # Serving that shape rather than a canned verdict is what keeps the precedence
  # tests honest — they exercise the reduction against what the endpoint really
  # returns, including the merged-is-closed trap.
  #
  # The endpoint is parsed for its `head=` filter and answered only for the
  # branch named there. A request whose filter is missing or empty gets the
  # unfiltered page, exactly as GitHub gives it — which is what lets a test prove
  # the owner prefix and the empty-name guard matter.
  #
  # Invocations are counted, and the branches asked about recorded, so a test can
  # assert the call shape as well as the answer.
  cat > "$MOCK_BIN/gh" <<'FAKEGH'
#!/usr/bin/env bash
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  [[ "${GH_AUTHED:-true}" == "true" ]] && exit 0
  exit 1
elif [[ "$1" == "api" ]]; then
  echo "call" >> "$GH_CALL_LOG"
  endpoint="$2"

  # The reduction under test rides in --jq, so the mock has to apply it the way
  # gh does. Serving the body unfiltered would test nothing but the mock.
  jq_filter="."
  while [[ $# -gt 0 ]]; do
    [[ "$1" == "--jq" ]] && jq_filter="$2"
    shift
  done

  # Failures speak on stderr, as gh does — the library classifies on that wording
  # and a diagnosis delivered on stdout would be read as the answer instead.
  if [[ "${GH_API_FAIL:-}" == "quota" ]]; then
    echo "gh: API rate limit exceeded for user ID 1234. (HTTP 403)" >&2
    exit 1
  fi
  [[ -n "${GH_API_FAIL:-}" ]] && { echo 'gh: Not Found (HTTP 404)' >&2; exit 1; }

  # `{owner}:branch`, url-decoded far enough for the characters a branch name
  # can carry. Absent when the caller built the filter wrong.
  filter=""
  [[ "$endpoint" == *"head="* ]] && filter="${endpoint#*head=}" && filter="${filter%%&*}"
  printf '%s\n' "$filter" >> "$GH_BRANCH_LOG"

  # GitHub ignores a head filter with no owner prefix and returns the whole
  # page. The mock reproduces that rather than hiding it.
  if [[ -z "$filter" || "$filter" != *:* ]]; then
    jq -r "$jq_filter" < "$GH_PR_JSON"
    exit 0
  fi

  branch="${filter#*:}"
  branch="$(printf '%b' "${branch//%/\\x}")"
  # An owner-prefixed but empty branch name is the unfiltered page too.
  if [[ -z "$branch" ]]; then
    jq -r "$jq_filter" < "$GH_PR_JSON"
    exit 0
  fi

  jq -c --arg b "$branch" '[.[] | select(.headRefName == $b)]' < "$GH_PR_JSON" \
    | jq -r "$jq_filter"
  exit 0
fi
exit 1
FAKEGH
  chmod +x "$MOCK_BIN/gh"

  export MOCK_BIN
}

setup() {
  load 'test_helper'
  common_setup

  export PATH="$MOCK_BIN:$PATH"
  export GH_PR_JSON="$TMPDIR/prs.json"
  export GH_CALL_LOG="$TMPDIR/gh-calls.log"
  export GH_BRANCH_LOG="$TMPDIR/gh-branches.log"
  export GH_AUTHED=true
  unset GH_API_FAIL
  : > "$GH_CALL_LOG"
  : > "$GH_BRANCH_LOG"
  echo '[]' > "$GH_PR_JSON"

  source "$REPO_ROOT/lib/branch_state.sh"
}

teardown() {
  common_teardown
}

# Fixtures are written in the endpoint's own vocabulary: `state` is open or
# closed, and `merged_at` is what distinguishes a landed PR from an abandoned
# one. `headRefName` is the mock's filter key, not a REST field.
_write_prs() {
  cat > "$GH_PR_JSON"
}

_gh_calls() {
  wc -l < "$GH_CALL_LOG" | tr -d ' '
}

# ── Availability ─────────────────────────────────────────────────────────────

@test "branch_gh_available is true when gh is authenticated" {
  run branch_gh_available
  [ "$status" -eq 0 ]
}

@test "branch_gh_available is false when gh is not authenticated" {
  export GH_AUTHED=false
  run branch_gh_available
  [ "$status" -ne 0 ]
}

@test "branch_pr_states returns non-zero and leaves the map empty without auth" {
  export GH_AUTHED=false
  declare -A states
  run branch_pr_states states feat/a
  [ "$status" -ne 0 ]

  # `run` executes in a subshell, so re-run to inspect the map itself.
  branch_pr_states states feat/a || true
  [ "${#states[@]}" -eq 0 ]
}

@test "branch_pr_states makes no api call without auth" {
  export GH_AUTHED=false
  declare -A states
  branch_pr_states states feat/a || true
  [ "$(_gh_calls)" -eq 0 ]
}

# ── Mapping ──────────────────────────────────────────────────────────────────

@test "each branch maps to its PR state" {
  _write_prs <<'JSON'
[{"headRefName":"feat/a","state":"closed","merged_at":"2026-01-01T00:00:00Z"},
 {"headRefName":"feat/b","state":"open","merged_at":null},
 {"headRefName":"feat/c","state":"closed","merged_at":null}]
JSON
  declare -A states
  branch_pr_states states feat/a feat/b feat/c

  [ "${states[feat/a]}" = "MERGED" ]
  [ "${states[feat/b]}" = "OPEN" ]
  [ "${states[feat/c]}" = "CLOSED" ]
}

@test "a branch with no PR is absent from the map" {
  _write_prs <<'JSON'
[{"headRefName":"feat/a","state":"closed","merged_at":"2026-01-01T00:00:00Z"}]
JSON
  declare -A states
  branch_pr_states states feat/a feat/never-opened

  [ -z "${states[feat/never-opened]:-}" ]
  [ "${#states[@]}" -eq 1 ]
}

@test "a second call replaces the map rather than merging into it" {
  _write_prs <<'JSON'
[{"headRefName":"feat/a","state":"open","merged_at":null}]
JSON
  declare -A states
  branch_pr_states states feat/a

  _write_prs <<'JSON'
[]
JSON
  branch_pr_states states feat/b

  # A stale entry from the first call would still be here if the array were
  # merged into rather than reset at the top of the function.
  [ -z "${states[feat/a]:-}" ]
  [ "${#states[@]}" -eq 0 ]
}

@test "naming no branches succeeds with an empty map and no call" {
  declare -A states
  run branch_pr_states states
  [ "$status" -eq 0 ]

  branch_pr_states states
  [ "${#states[@]}" -eq 0 ]
  [ "$(_gh_calls)" -eq 0 ]
}

# ── The REST state vocabulary ────────────────────────────────────────────────

@test "a closed PR with a merge timestamp is MERGED, not CLOSED" {
  # REST has no "merged" state. Reading .state alone makes a landed branch
  # indistinguishable from an abandoned one — and the caller removes a worktree
  # on exactly that difference.
  _write_prs <<'JSON'
[{"headRefName":"feat/landed","state":"closed","merged_at":"2026-01-01T00:00:00Z"}]
JSON
  declare -A states
  branch_pr_states states feat/landed

  [ "${states[feat/landed]}" = "MERGED" ]
}

@test "a closed PR with no merge timestamp is CLOSED" {
  _write_prs <<'JSON'
[{"headRefName":"feat/abandoned","state":"closed","merged_at":null}]
JSON
  declare -A states
  branch_pr_states states feat/abandoned

  [ "${states[feat/abandoned]}" = "CLOSED" ]
}

# ── Request shape ────────────────────────────────────────────────────────────

@test "the head filter carries an owner prefix" {
  # Without it GitHub ignores the filter and answers with the repo's most recent
  # hundred PRs — any one of them open makes every branch read as OPEN.
  _write_prs <<'JSON'
[{"headRefName":"feat/other","state":"open","merged_at":null}]
JSON
  declare -A states
  branch_pr_states states feat/asked-about

  # The fixture's open PR is on a different branch, so a filter that worked
  # leaves the map empty. One that was dropped returns that PR and says OPEN.
  [ "${#states[@]}" -eq 0 ]
  # The name is url-encoded by the time it reaches the endpoint, so the prefix
  # is what is asserted here rather than the branch spelling.
  run cat "$GH_BRANCH_LOG"
  [[ "$output" == *"{owner}:feat%2Fasked-about"* ]]
}

@test "a branch name carrying a query delimiter is encoded" {
  # A raw & or # would end the query parameter and take the rest of the name
  # with it, leaving a filter for a shorter branch that may well exist.
  _write_prs <<'JSON'
[{"headRefName":"feat/a&b#c","state":"open","merged_at":null}]
JSON
  declare -A states
  branch_pr_states states 'feat/a&b#c'

  [ "${states['feat/a&b#c']}" = "OPEN" ]
  run cat "$GH_BRANCH_LOG"
  [[ "$output" != *"&b"* ]]
}

@test "an empty branch name is refused rather than asked about" {
  # `head={owner}:` is the unfiltered page again, so the answer would describe
  # whatever PR happens to be newest.
  _write_prs <<'JSON'
[{"headRefName":"feat/other","state":"open","merged_at":null}]
JSON
  declare -A states
  run branch_pr_states states ""
  [ "$status" -ne 0 ]

  branch_pr_states states "" || true
  [ "${#states[@]}" -eq 0 ]
  [ "$(_gh_calls)" -eq 0 ]
}

# ── Precedence across several PRs on one branch ──────────────────────────────

@test "an open PR outranks a merged one on the same branch" {
  _write_prs <<'JSON'
[{"headRefName":"feat/reopened","state":"closed","merged_at":"2026-01-01T00:00:00Z"},
 {"headRefName":"feat/reopened","state":"open","merged_at":null}]
JSON
  declare -A states
  branch_pr_states states feat/reopened

  # The branch was reused for follow-up work; deleting it would drop live work.
  [ "${states[feat/reopened]}" = "OPEN" ]
}

@test "a merged PR outranks a closed one on the same branch" {
  _write_prs <<'JSON'
[{"headRefName":"feat/retried","state":"closed","merged_at":null},
 {"headRefName":"feat/retried","state":"closed","merged_at":"2026-01-01T00:00:00Z"}]
JSON
  declare -A states
  branch_pr_states states feat/retried

  # An abandoned first attempt does not undo the landing of the second.
  [ "${states[feat/retried]}" = "MERGED" ]
}

@test "several closed PRs collapse to CLOSED" {
  _write_prs <<'JSON'
[{"headRefName":"feat/twice","state":"closed","merged_at":null},
 {"headRefName":"feat/twice","state":"closed","merged_at":null}]
JSON
  declare -A states
  branch_pr_states states feat/twice

  [ "${states[feat/twice]}" = "CLOSED" ]
}

# ── Cost ─────────────────────────────────────────────────────────────────────

@test "each branch costs exactly one call" {
  local -a branches=()
  for i in $(seq 0 5); do branches+=("feat/b$i"); done
  jq -n '[range(6) | {headRefName: "feat/b\(.)", state: "closed",
                      merged_at: "2026-01-01T00:00:00Z"}]' > "$GH_PR_JSON"

  declare -A states
  branch_pr_states states "${branches[@]}"

  [ "${#states[@]}" -eq 6 ]
  [ "$(_gh_calls)" -eq 6 ]
}

# ── Failure ──────────────────────────────────────────────────────────────────

@test "a failed request is a refusal, not an answer of no PR" {
  # A throttle or an unreadable repo must not read as "this branch has no PR",
  # which is the answer that lets an open PR's worktree be removed.
  export GH_API_FAIL=1
  declare -A states
  run branch_pr_states states feat/a
  [ "$status" -ne 0 ]

  branch_pr_states states feat/a || true
  [ "${#states[@]}" -eq 0 ]
}

@test "a spent quota returns the refusal code" {
  # GitHub declining to answer a question it holds the answer to. The caller acts
  # on the absence of a PR, so it has to tell this from a question that could
  # never have been put here.
  export GH_API_FAIL=quota
  declare -A states
  run branch_pr_states states feat/a
  [ "$status" -eq "$BRANCH_PR_REFUSED_RC" ]
}

@test "a repo gh cannot answer for is a plain failure, not a refusal" {
  # A 404, a non-GitHub remote, no network. Permanent for this machine, so a
  # caller that treated it as a refusal would never act on such a repo again.
  export GH_API_FAIL=1
  declare -A states
  run branch_pr_states states feat/a
  [ "$status" -eq 1 ]
}

@test "no auth is a plain failure, not a refusal" {
  export GH_AUTHED=false
  declare -A states
  run branch_pr_states states feat/a
  [ "$status" -eq 1 ]
}

@test "a branch failing after an earlier one answered leaves no partial map" {
  # Assigning as each answer lands would report the branches reached and
  # silently deny the rest — indistinguishable from those having no PR.
  _write_prs <<'JSON'
[{"headRefName":"feat/first","state":"open","merged_at":null}]
JSON
  declare -A states

  # The second branch is the empty name, which _branch_pr_state refuses after
  # the first has already produced a row.
  run branch_pr_states states feat/first ""
  [ "$status" -ne 0 ]

  branch_pr_states states feat/first "" || true
  [ "${#states[@]}" -eq 0 ]
}
