#!/usr/bin/env bats
# Tests for wt-cleanup — wt list schema compatibility, and unstarted branches that git calls merged.
bats_require_minimum_version 1.5.0

setup_file() {
  load 'test_helper'
  load 'wt_cleanup_helper'
  _wt_cleanup_mocks
}

setup() {
  load 'test_helper'
  load 'wt_cleanup_helper'
  common_setup
  _wt_cleanup_env
  source "$REPO_ROOT/bin/wt-cleanup"
}

teardown() {
  common_teardown
}

# ── Schema compatibility ─────────────────────────────────────────────────────
#
# The schema this script reads is worktrunk's to change, and it has: the bump
# to 2 moved every field wt-cleanup reads, and because `wt list` was called as
# `... || return 0` the breakage surfaced as a silent no-op on every session
# stop rather than an error. These cover both halves — refusing a schema we do
# not know, and keeping the fixtures honest about the one we do.

@test "an unrecognised wt list schema is refused, not indexed" {
  jq -n '{schema: 99, repo: {}, collected: {}, items: []}' > "$WT_JSON"
  _run_cleanup
  [ "$status" -eq 1 ]
  [[ "$output" == *"schema 99 is not supported"* ]]
}

@test "a payload with no schema field is refused" {
  echo '[]' > "$WT_JSON"
  _run_cleanup
  [ "$status" -eq 1 ]
  [[ "$output" == *"not supported"* ]]
}

@test "a wt list failure is reported rather than exiting clean" {
  # The failing stub goes in a directory of this test's own, ahead of
  # $MOCK_BIN on PATH. $MOCK_BIN lives in BATS_FILE_TMPDIR and is shared by
  # every test in the file: overwriting the stub there and restoring it
  # afterwards makes `wt list` fail for whichever cases happen to run
  # alongside this one under --jobs, and the schema tests read that failure as
  # the wrong refusal.
  local stub_bin="$BATS_TEST_TMPDIR/failing-wt"
  mkdir -p "$stub_bin"
  cat > "$stub_bin/wt" <<'FAKEWT'
#!/usr/bin/env bash
[[ "$1" == "list" ]] && exit 1
exit 0
FAKEWT
  chmod +x "$stub_bin/wt"
  PATH="$stub_bin:$PATH"

  _run_cleanup

  [ "$status" -eq 1 ]
  [[ "$output" == *"wt list --format json failed"* ]]
}

@test "wt list schema matches the fixture shape" {
  # CI installs worktrunk precisely so this runs there — a skip in CI would
  # make the whole check decorative, which is the failure mode this test
  # exists to prevent. Only a developer machine without `wt` may skip, and
  # `skip` has to be the statement itself: called inside an `if` body it sets
  # the skip but does not stop the test, which then runs on an empty payload.
  local real=""
  [[ -n "$REAL_WT" && -x "$REAL_WT" ]] \
    && real=$("$REAL_WT" list --format json 2>/dev/null) || true

  if [[ -z "$real" && -n "${CI:-}" ]]; then
    echo "wt list produced nothing in CI — the schema contract is unchecked" >&2
    return 1
  fi
  if [[ -z "$real" ]]; then
    skip "wt unavailable here"
    return 0
  fi

  # The schema the script pins itself to is the one wt actually speaks.
  [ "$(jq -r '.schema' <<< "$real")" = "$WT_LIST_SCHEMA" ]

  # Every field wt-cleanup reads is present on a real row, so a future bump
  # that moves one fails here instead of no-opping in production.
  [ "$(jq -r '.items | type' <<< "$real")" = "array" ]
  local row
  row=$(jq -r '[.items[] | select(.worktree != null)][0]' <<< "$real")
  [ "$row" != "null" ]
  # `.branch` is null on a detached worktree, which is what `actions/checkout`
  # leaves behind — so this asserts the two shapes the field really has, not
  # the one a developer machine happens to show.
  [[ "$(jq -r '.branch | type' <<< "$row")" =~ ^(string|null)$ ]]
  [ "$(jq -r '.worktree.path | type' <<< "$row")" = "string" ]
  [ "$(jq -r '.worktree.main | type' <<< "$row")" = "boolean" ]
  [ "$(jq -r '.worktree.current | type' <<< "$row")" = "boolean" ]
  [ "$(jq -r '.worktree.changes.staged | type' <<< "$row")" = "boolean" ]
  [ "$(jq -r '.worktree.changes.conflicted | type' <<< "$row")" = "boolean" ]
  [ "$(jq -r '.display.state | type' <<< "$row")" = "string" ]
  [ "$(jq -r '.display.symbols | type' <<< "$row")" = "string" ]
  [ "$(jq -r '.head.committed_at | type' <<< "$row")" = "string" ]

  # The committed_at stamp is the format iso_to_epoch parses.
  local stamp
  stamp=$(jq -r '.head.committed_at' <<< "$row")
  [ -n "$(iso_to_epoch "$stamp")" ]
}

@test "a branch row with no worktree is skipped" {
  jq -n --argjson schema "$WT_LIST_SCHEMA" '
    { schema: $schema, repo: {default_branch: "main"}, collected: {},
      items: [{ branch: "feat/branch-only",
                head: {committed_at: "2026-01-01T00:00:00Z"},
                worktree: null,
                display: {state: "integrated", symbols: "⊂"} }] }' > "$WT_JSON"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
}

@test "only branches with a worktree are asked about" {
  # The PR lookup is scoped to the branches the loop can act on. A branch row
  # `wt list --branches` carries has no worktree to remove, so asking the
  # tracker about it buys an answer nothing reads — and on a repo with hundreds
  # of branches, that is the whole cost of the lookup.
  echo "feat/has-worktree" > "$GH_PR_MERGED"
  jq -n --argjson schema "$WT_LIST_SCHEMA" '
    { schema: $schema, repo: {default_branch: "main"}, collected: {},
      items: [{ branch: "feat/branch-only",
                head: {committed_at: "2026-01-01T00:00:00Z"},
                worktree: null,
                display: {state: "ahead", symbols: "↑3"} },
              { branch: "feat/has-worktree",
                head: {committed_at: "2026-01-01T00:00:00Z"},
                worktree: {path: "/nonexistent/feat/has-worktree", main: false,
                           current: false,
                           changes: {staged: false, modified: false,
                                     untracked: false, renamed: false,
                                     deleted: false, conflicted: false}},
                display: {state: "ahead", symbols: "↑3"} }] }' > "$WT_JSON"
  _run_cleanup
  [ "$status" -eq 0 ]

  run cat "$GH_BRANCH_LOG"
  [ "$output" = "feat/has-worktree" ]
}

@test "an unparseable timestamp is not treated as an ancient worktree" {
  jq -n --argjson schema "$WT_LIST_SCHEMA" '
    { schema: $schema, repo: {default_branch: "main"}, collected: {},
      items: [{ branch: "feat/bad-date",
                head: {committed_at: "not-a-date"},
                worktree: {path: "/nonexistent/feat/bad-date", main: false,
                           current: false,
                           changes: {staged: false, modified: false,
                                     untracked: false, renamed: false,
                                     deleted: false, conflicted: false}},
                display: {state: "ahead", symbols: "↑3"} }] }' > "$WT_JSON"
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
}

@test "a worktree stopped mid-merge is never removed as clean" {
  # A conflicted file is the least safe residue there is: the worktree holds a
  # half-finished merge and nothing else records it. _change_kind names the
  # code "conflicted", but the ordering loop that builds the returned string
  # once omitted that word, so the whole state was dropped and the worktree
  # read as clean — merged, disposable, force-removed.
  _make_worktrees
  git -C "$FEAT_WT" checkout -q -b conflicting
  printf 'theirs\n' > "$FEAT_WT/list.txt"
  git -C "$FEAT_WT" commit -qam theirs
  printf 'ours\n' > "$MAIN_WT/list.txt"
  git -C "$MAIN_WT" commit -qam ours
  git -C "$FEAT_WT" merge main >/dev/null 2>&1 || true
  # Guard the premise: the fixture must really be mid-merge.
  git -C "$FEAT_WT" status --porcelain | grep -q '^UU' || \
    skip "could not stage a conflict in this git"

  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"conflicting","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":false,"untracked":false,"renamed":false,"deleted":false,"conflicted":true}}
]
JSON

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" != *"removing: conflicting"* ]]
  [[ "$output" == *"conflicted"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
}

@test "a detached worktree is left alone rather than removed as \"null\"" {
  # `actions/checkout` leaves a detached HEAD, so this is the shape CI runs
  # against. Every removal path names the branch to `wt remove`; with no
  # branch there is nothing safe to name, and a detached checkout may be a
  # rebase in progress.
  jq -n --argjson schema "$WT_LIST_SCHEMA" '
    { schema: $schema, repo: {default_branch: "main"}, collected: {},
      items: [{ branch: null,
                head: {committed_at: "2026-01-01T00:00:00Z"},
                worktree: {path: "/nonexistent/detached", main: false,
                           current: false, detached: true,
                           changes: {staged: false, modified: false,
                                     untracked: false, renamed: false,
                                     deleted: false, conflicted: false}},
                display: {state: "integrated", symbols: "⊂"} }] }' > "$WT_JSON"
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
}

# ── An unstarted branch is not a merged one ─────────────────────────────────
#
# A branch cut and not yet committed to has its tip at the default branch's,
# so `⊂` reads true and the merge check calls it merged. Nothing between that
# and removal notices the branch never had a chance to hold anything, and
# `git status` does not report ignored files — so a worktree holding only a
# plan under `ignore/` reads clean as well. Both guards satisfied, the
# worktree and its branch go.

@test "an unstarted branch is kept even though git calls it merged" {
  _make_unstarted_worktree
  # Guard the premise: `feature` really has no commit of its own.
  [ "$(git -C "$FEAT_WT" rev-list --count main..feature)" -eq 0 ]

  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"feature","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}
]
JSON
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -s "$WT_REMOVE_LOG" ]
  grep -q "SKIP-UNSTARTED branch=feature" "$CLEANUP_LOG"
}

@test "work only git ignores does not make an unstarted worktree removable" {
  # The case that lost a worktree: the branch had no commits and its only
  # content was a plan under `ignore/`, which `git status` never reports.
  _make_unstarted_worktree
  mkdir -p "$FEAT_WT/ignore/plans"
  printf 'the reasoning this worktree exists for\n' > "$FEAT_WT/ignore/plans/design.md"
  # Guard the premise: git really does see this worktree as clean.
  [ -z "$(git -C "$FEAT_WT" status --porcelain -uall)" ]

  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"feature","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}
]
JSON
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -s "$WT_REMOVE_LOG" ]
  [ -f "$FEAT_WT/ignore/plans/design.md" ]
}

@test "a branch that did commit and then merged is still removed" {
  # The regression guard: keeping unstarted branches must not keep started
  # ones, or the merged path stops collecting anything at all.
  _make_worktrees
  [ "$(git -C "$FEAT_WT" rev-list --count main..feature)" -eq 1 ]

  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"feature","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}
]
JSON
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "an age removal still collects an unstarted worktree" {
  # --age is a statement about abandonment, not about integration. An empty
  # worktree nobody has touched for months is what it is asked to collect.
  _make_unstarted_worktree
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))

  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"feature","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":$old_timestamp}}
]
JSON
  _run_cleanup --age 30 --no-grace-period
  [ "$status" -eq 0 ]
  [ -s "$WT_REMOVE_LOG" ]
}

@test "a worktree this cannot open is judged exactly as it was before" {
  # The predicate reads git in the worktree. With no worktree to read, it
  # declines to overrule the rest of the script rather than guessing.
  _write_worktrees <<'JSON'
[{"branch":"feat/gone","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}
