#!/usr/bin/env bats
# Tests for lib/branch_state.sh — the batched PR-state lookup shared by the
# cleanup tools.

setup_file() {
  load 'test_helper'
  MOCK_BIN="$BATS_FILE_TMPDIR/bin"
  mkdir -p "$MOCK_BIN"

  # Serves whatever JSON the test wrote, and counts its own invocations so a
  # test can assert the lookup is batched rather than per-branch.
  cat > "$MOCK_BIN/gh" <<'FAKEGH'
#!/usr/bin/env bash
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  [[ "${GH_AUTHED:-true}" == "true" ]] && exit 0
  exit 1
elif [[ "$1" == "pr" && "$2" == "list" ]]; then
  echo "call" >> "$GH_CALL_LOG"
  cat "$GH_PR_JSON"
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
  TMPDIR="$(mktemp -d)"

  export PATH="$MOCK_BIN:$PATH"
  export GH_PR_JSON="$TMPDIR/prs.json"
  export GH_CALL_LOG="$TMPDIR/gh-calls.log"
  export GH_AUTHED=true
  : > "$GH_CALL_LOG"
  echo '[]' > "$GH_PR_JSON"

  source "$REPO_ROOT/lib/branch_state.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

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
  run branch_pr_states states
  [ "$status" -ne 0 ]

  # `run` executes in a subshell, so re-run to inspect the map itself.
  branch_pr_states states || true
  [ "${#states[@]}" -eq 0 ]
}

@test "branch_pr_states makes no gh pr list call without auth" {
  export GH_AUTHED=false
  declare -A states
  branch_pr_states states || true
  [ "$(_gh_calls)" -eq 0 ]
}

# ── Mapping ──────────────────────────────────────────────────────────────────

@test "each branch maps to its PR state" {
  _write_prs <<'JSON'
[{"headRefName":"feat/a","state":"MERGED"},
 {"headRefName":"feat/b","state":"OPEN"},
 {"headRefName":"feat/c","state":"CLOSED"}]
JSON
  declare -A states
  branch_pr_states states

  [ "${states[feat/a]}" = "MERGED" ]
  [ "${states[feat/b]}" = "OPEN" ]
  [ "${states[feat/c]}" = "CLOSED" ]
}

@test "a branch with no PR is absent from the map" {
  _write_prs <<'JSON'
[{"headRefName":"feat/a","state":"MERGED"}]
JSON
  declare -A states
  branch_pr_states states

  [ -z "${states[feat/never-opened]:-}" ]
  [ "${#states[@]}" -eq 1 ]
}

@test "an empty PR list yields an empty map and still succeeds" {
  declare -A states
  run branch_pr_states states
  [ "$status" -eq 0 ]
}

# ── Precedence across several PRs on one branch ──────────────────────────────

@test "an open PR outranks a merged one on the same branch" {
  _write_prs <<'JSON'
[{"headRefName":"feat/reopened","state":"MERGED"},
 {"headRefName":"feat/reopened","state":"OPEN"}]
JSON
  declare -A states
  branch_pr_states states

  # The branch was reused for follow-up work; deleting it would drop live work.
  [ "${states[feat/reopened]}" = "OPEN" ]
}

@test "a merged PR outranks a closed one on the same branch" {
  _write_prs <<'JSON'
[{"headRefName":"feat/retried","state":"CLOSED"},
 {"headRefName":"feat/retried","state":"MERGED"}]
JSON
  declare -A states
  branch_pr_states states

  # An abandoned first attempt does not undo the landing of the second.
  [ "${states[feat/retried]}" = "MERGED" ]
}

@test "several closed PRs collapse to CLOSED" {
  _write_prs <<'JSON'
[{"headRefName":"feat/twice","state":"CLOSED"},
 {"headRefName":"feat/twice","state":"CLOSED"}]
JSON
  declare -A states
  branch_pr_states states

  [ "${states[feat/twice]}" = "CLOSED" ]
}

# ── Batching ─────────────────────────────────────────────────────────────────

@test "many branches cost exactly one gh call" {
  jq -n '[range(50) | {headRefName: "feat/b\(.)", state: "MERGED"}]' > "$GH_PR_JSON"
  declare -A states
  branch_pr_states states

  [ "${#states[@]}" -eq 50 ]
  [ "$(_gh_calls)" -eq 1 ]
}

# ── Re-entrancy ──────────────────────────────────────────────────────────────

@test "a second call replaces the map rather than merging into it" {
  _write_prs <<'JSON'
[{"headRefName":"feat/gone","state":"MERGED"}]
JSON
  declare -A states
  branch_pr_states states

  _write_prs <<'JSON'
[{"headRefName":"feat/fresh","state":"OPEN"}]
JSON
  branch_pr_states states

  [ -z "${states[feat/gone]:-}" ]
  [ "${states[feat/fresh]}" = "OPEN" ]
}

# ── Truncation ───────────────────────────────────────────────────────────────

@test "hitting the page limit warns instead of silently undercounting" {
  _BRANCH_PR_LIMIT=3
  _write_prs <<'JSON'
[{"headRefName":"feat/a","state":"MERGED"},
 {"headRefName":"feat/b","state":"MERGED"},
 {"headRefName":"feat/c","state":"MERGED"}]
JSON
  declare -A states
  run branch_pr_states states

  [ "$status" -eq 0 ]
  [[ "$output" == *"hit the 3-entry limit"* ]]
}

@test "a full page of PRs on one branch still warns" {
  _BRANCH_PR_LIMIT=3
  _write_prs <<'JSON'
[{"headRefName":"feat/retried","state":"CLOSED"},
 {"headRefName":"feat/retried","state":"CLOSED"},
 {"headRefName":"feat/retried","state":"MERGED"}]
JSON
  declare -A states
  run branch_pr_states states

  # Grouping leaves one branch, so counting the map would read as 1 of 3 and
  # stay quiet on a page that was in fact full.
  [ "$status" -eq 0 ]
  [[ "$output" == *"hit the 3-entry limit"* ]]
}

@test "a list under the page limit warns about nothing" {
  _BRANCH_PR_LIMIT=3
  _write_prs <<'JSON'
[{"headRefName":"feat/a","state":"MERGED"}]
JSON
  declare -A states
  run branch_pr_states states

  [ "$status" -eq 0 ]
  [ -z "$output" ]
}
