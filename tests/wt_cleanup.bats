#!/usr/bin/env bats
# Tests for wt-cleanup — merge detection, age-based removal, dry-run mode,
# quiet mode, and worktree protection (main/current).

setup_file() {
  load 'test_helper'
  MOCK_BIN="$BATS_FILE_TMPDIR/bin"
  mkdir -p "$MOCK_BIN"

  cat > "$MOCK_BIN/wt" <<'FAKEWT'
#!/usr/bin/env bash
if [[ "$1" == "list" ]]; then
  cat "$WT_JSON_FILE"
elif [[ "$1" == "remove" ]]; then
  echo "$*" >> "$WT_REMOVE_LOG_FILE"
fi
FAKEWT
  chmod +x "$MOCK_BIN/wt"

  # `gh pr list` for the whole repo, matching what lib/branch_state.sh calls.
  # The per-state fixture files stay one branch name per line; only the shape
  # of the answer changed.
  cat > "$MOCK_BIN/gh" <<'FAKEGH'
#!/usr/bin/env bash
_emit() {
  local file="$1" state="$2"
  [[ -f "$file" ]] || return 0
  while read -r branch; do
    [[ -n "$branch" ]] || continue
    printf '{"headRefName":"%s","state":"%s"}\n' "$branch" "$state"
  done < "$file"
}

if [[ "$1" == "auth" && "$2" == "status" ]]; then
  [[ -f "$GH_PR_MERGED_FILE" || -f "$GH_PR_OPEN_FILE" || -f "$GH_PR_CLOSED_FILE" ]] && exit 0
  exit 1
elif [[ "$1" == "pr" && "$2" == "list" ]]; then
  { _emit "$GH_PR_MERGED_FILE" MERGED
    _emit "$GH_PR_OPEN_FILE" OPEN
    _emit "$GH_PR_CLOSED_FILE" CLOSED
  } | jq -s '.'
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

  WT_JSON="$TMPDIR/wt-list.json"
  WT_REMOVE_LOG="$TMPDIR/wt-removes.log"
  GH_PR_MERGED="$TMPDIR/gh-pr-merged.txt"
  GH_PR_OPEN="$TMPDIR/gh-pr-open.txt"
  GH_PR_CLOSED="$TMPDIR/gh-pr-closed.txt"

  export PATH="$MOCK_BIN:$PATH"
  export WT_JSON_FILE="$WT_JSON"
  export WT_REMOVE_LOG_FILE="$WT_REMOVE_LOG"
  export GH_PR_MERGED_FILE="$GH_PR_MERGED"
  export GH_PR_OPEN_FILE="$GH_PR_OPEN"
  export GH_PR_CLOSED_FILE="$GH_PR_CLOSED"
  export CLEANUP_LOG_DIR="$TMPDIR/logs"
  export NO_COLOR=1
  export WORKBENCH_DIR="$REPO_ROOT"

  source "$REPO_ROOT/bin/wt-cleanup"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run wt-cleanup with mocked wt and gh
_run_cleanup() {
  run main "$@"
}

# Helper: write worktree JSON
_write_worktrees() {
  cat > "$WT_JSON"
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "wt-cleanup --help exits 0" {
  run main --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"worktrees"* ]]
}

@test "wt-cleanup -h exits 0" {
  run main -h
  [ "$status" -eq 0 ]
}

# ── No worktrees ─────────────────────────────────────────────────────────────

@test "empty worktree list shows 'no stale worktrees'" {
  _write_worktrees <<< '[]'
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

# ── Merged worktrees ─────────────────────────────────────────────────────────

@test "merged worktree is removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/old","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/old"* ]]
  [[ "$output" == *"merged"* ]]
  grep -q "feat/old" "$WT_REMOVE_LOG"
}

@test "merged via symbols field is removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/done","is_main":false,"is_current":false,"main_state":"diverged","symbols":"⊂ ↑1","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/done"* ]]
}

@test "integrated via main_state field is removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/merged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/merged"* ]]
}

# ── Squash-merged PRs (GitHub fallback) ──────────────────────────────────────

@test "squash-merged PR detected via gh fallback" {
  _write_worktrees <<'JSON'
[{"branch":"feat/squashed","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0}}]
JSON
  echo "feat/squashed" > "$GH_PR_MERGED"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/squashed"* ]]
  [[ "$output" == *"pr merged"* ]]
  grep -q "feat/squashed" "$WT_REMOVE_LOG"
}

@test "unmerged PR not removed by gh fallback" {
  _write_worktrees <<'JSON'
[{"branch":"feat/open-pr","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":0}}]
JSON
  echo "feat/other-branch" > "$GH_PR_MERGED"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

# ── Protected worktrees ──────────────────────────────────────────────────────

@test "main worktree is skipped even if merged" {
  _write_worktrees <<'JSON'
[{"branch":"main","is_main":true,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

@test "main branch worktree is skipped even when is_main flag is false" {
  _write_worktrees <<'JSON'
[{"branch":"main","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

@test "current worktree is skipped even if merged" {
  _write_worktrees <<'JSON'
[{"branch":"feat/active","is_main":false,"is_current":true,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

# ── Age-based removal ────────────────────────────────────────────────────────

@test "old worktree removed with --age flag" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/stale","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/stale"* ]]
  [[ "$output" == *"inactive"* ]]
}

@test "recent worktree kept with --age flag" {
  local recent_timestamp
  recent_timestamp=$(( $(date +%s) - 10 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/fresh","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":$recent_timestamp}}]
JSON
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

@test "old unmerged worktree not removed without --age" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 200 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/ancient","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑10","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

# ── Dry run ──────────────────────────────────────────────────────────────────

@test "--dry-run prints but does not call wt remove" {
  _write_worktrees <<'JSON'
[{"branch":"feat/bye","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"would remove: feat/bye"* ]]
  [ ! -f "$WT_REMOVE_LOG" ]
}

# ── Quiet mode ───────────────────────────────────────────────────────────────

@test "--quiet suppresses output" {
  _write_worktrees <<'JSON'
[{"branch":"feat/silent","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# ── Uncommitted changes protection ──────────────────────────────────────────

@test "merged worktree with uncommitted changes is not removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "unmerged worktree with uncommitted changes is silently skipped" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty-unmerged","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
  [[ "$output" != *"uncommitted"* ]]
}

@test "clean worktree is still removed when merged" {
  _write_worktrees <<'JSON'
[{"branch":"feat/clean-merged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":false,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/clean-merged"* ]]
}

# ── Dirty-merged summary ───────────────────────────────────────────────────

@test "dirty merged worktree shows summary with change types" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":true,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"feat/dirty"* ]]
  [[ "$output" == *"modified"* ]]
  [[ "$output" == *"untracked"* ]]
}

@test "dirty merged summary shows staged changes" {
  _write_worktrees <<'JSON'
[{"branch":"feat/staged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":true,"modified":false,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat/staged"* ]]
  [[ "$output" == *"staged"* ]]
}

@test "dirty merged summary shows multiple worktrees" {
  _write_worktrees <<'JSON'
[
  {"branch":"feat/a","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":true,"modified":false,"untracked":false,"renamed":false,"deleted":false}},
  {"branch":"feat/b","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":true,"renamed":false,"deleted":false}}
]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat/a"* ]]
  [[ "$output" == *"feat/b"* ]]
}

@test "dirty merged summary suppressed by --quiet" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "dirty merged summary appears alongside removals" {
  _write_worktrees <<'JSON'
[
  {"branch":"feat/clean","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":false,"untracked":false,"renamed":false,"deleted":false}},
  {"branch":"feat/dirty","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}
]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/clean"* ]]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"feat/dirty"* ]]
}

@test "squash-merged dirty worktree detected via gh fallback" {
  _write_worktrees <<'JSON'
[{"branch":"feat/squash-dirty","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  echo "feat/squash-dirty" > "$GH_PR_MERGED"
  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"feat/squash-dirty"* ]]
}

# ── Disposable residue ──────────────────────────────────────────────────────
#
# A merged worktree's residue is judged against the default branch, because the
# branch is already in it: a file the default branch ignores is a file the
# project decided weeks ago was not worth keeping, and a worktree cut before the
# rule landed reports it only because its own copy of the rules is stale. The
# flags `wt list` carries cannot see either — they answer whether the worktree's
# own index calls a file dirty, which for a merged worktree is the wrong
# question.

# _make_worktrees — a repo checked out at `main` with a `feature` worktree of the
# same commit, in MAIN_WT and FEAT_WT. The feature worktree's HEAD is the commit
# main was at when it was cut, which is what lets a test land an ignore rule on
# main that the worktree does not carry.
_make_worktrees() {
  MAIN_WT="$TMPDIR/repo"
  FEAT_WT="$TMPDIR/feature"
  mkdir -p "$MAIN_WT"
  git -C "$MAIN_WT" init -q --initial-branch=main
  git -C "$MAIN_WT" config user.email test@example.com
  git -C "$MAIN_WT" config user.name Test
  printf 'alpha\nbeta\ngamma\n' > "$MAIN_WT/list.txt"
  git -C "$MAIN_WT" add -A
  git -C "$MAIN_WT" commit -qm init
  git -C "$MAIN_WT" worktree add -q -b feature "$FEAT_WT"
}

# _ignore_on_main PATTERN — commit PATTERN to main's .gitignore, after the
# feature worktree was cut so the worktree does not have it.
_ignore_on_main() {
  printf '%s\n' "$1" >> "$MAIN_WT/.gitignore"
  git -C "$MAIN_WT" add .gitignore
  git -C "$MAIN_WT" commit -qm "ignore $1"
}

# _write_merged_pair FLAGS — the two-worktree list the tests above write by
# hand: main, plus a merged `feature` carrying the working_tree FLAGS.
_write_merged_pair() {
  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"feature","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":$1}
]
JSON
}

@test "untracked file the default branch ignores does not hold a worktree back" {
  _make_worktrees
  _ignore_on_main '*.tar.gz'
  printf 'artifact\n' > "$FEAT_WT/build.tar.gz"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feature"* ]]
  [[ "$output" != *"uncommitted"* ]]
}

@test "an untracked directory the default branch ignores is forgiven too" {
  # The whole directory is untracked, which git would otherwise collapse into
  # one entry naming the directory — a path the ignore rule under it does not
  # cover, and one the check would refuse for the wrong reason.
  _make_worktrees
  _ignore_on_main 'docs/scratch/'
  mkdir -p "$FEAT_WT/docs/scratch"
  printf 'notes\n' > "$FEAT_WT/docs/scratch/plan.md"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feature"* ]]
}

@test "untracked file the default branch does not ignore still holds it back" {
  _make_worktrees
  printf 'real work\n' > "$FEAT_WT/notes.md"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"Merged worktrees with uncommitted changes"* ]]
  [[ "$output" == *"untracked"* ]]
}

@test "a file whose lines only moved does not hold a worktree back" {
  _make_worktrees
  printf 'gamma\nalpha\nbeta\n' > "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feature"* ]]
}

@test "a file that gained a line still holds a worktree back" {
  _make_worktrees
  printf 'gamma\nalpha\nbeta\ndelta\n' > "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"modified"* ]]
}

@test "the summary names only the changes that survived the check" {
  # The flags would have said "modified, untracked" for this worktree. Reporting
  # a kind that was forgiven is what makes the warning stop being read.
  _make_worktrees
  _ignore_on_main '*.tar.gz'
  printf 'artifact\n' > "$FEAT_WT/build.tar.gz"
  printf 'alpha\nbeta\ngamma\ndelta\n' > "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":true,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"modified"* ]]
  [[ "$output" != *"untracked"* ]]
}

@test "a deleted file is never forgiven" {
  _make_worktrees
  rm "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":false,"renamed":false,"deleted":true}'

  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"deleted"* ]]
}

@test "every path forgiven is named in the cleanup log" {
  _make_worktrees
  _ignore_on_main '*.tar.gz'
  printf 'artifact\n' > "$FEAT_WT/build.tar.gz"
  _write_merged_pair '{"staged":false,"modified":false,"untracked":true,"renamed":false,"deleted":false}'

  _run_cleanup --quiet --no-grace-period
  [ "$status" -eq 0 ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  grep -q "DISPOSABLE-PATH branch=feature path=build.tar.gz reason=ignored-on-" "$log_file"
  grep -q "DISPOSABLE-RESIDUE branch=feature" "$log_file"
}

@test "a reordered file is named in the cleanup log with its reason" {
  _make_worktrees
  printf 'gamma\nalpha\nbeta\n' > "$FEAT_WT/list.txt"
  _write_merged_pair '{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}'

  _run_cleanup --quiet --no-grace-period
  [ "$status" -eq 0 ]
  grep -q "DISPOSABLE-PATH branch=feature path=list.txt reason=reordered" \
    "$TMPDIR/logs/wt-cleanup.log"
}

@test "an unreadable work-tree path leaves the flags in charge" {
  # Every other test in this file names no path at all, which is this case: the
  # check cannot open the work tree, so nothing is forgiven and the worktree is
  # reported exactly as it was before.
  _write_worktrees <<JSON
[{"branch":"feat/gone","path":"$TMPDIR/no-such-worktree","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  [[ "$output" == *"feat/gone"* ]]
  [[ "$output" == *"modified"* ]]
}

# ── Grace period ────────────────────────────────────────────────────────────

@test "recently created worktree is skipped by grace period" {
  # Create a real directory so stat works
  local wt_dir="$TMPDIR/recent-worktree"
  mkdir -p "$wt_dir"
  _write_worktrees <<JSON
[{"branch":"feat/new","path":"$wt_dir","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping: feat/new"* ]]
  [[ "$output" == *"grace period"* ]]
}

@test "--no-grace-period removes recently created worktree" {
  local wt_dir="$TMPDIR/recent-worktree"
  mkdir -p "$wt_dir"
  _write_worktrees <<JSON
[{"branch":"feat/new","path":"$wt_dir","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/new"* ]]
}

# ── Open PR guard ──────────────────────────────────────────────────────────

@test "worktree with open PR is not removed even if integrated" {
  _write_worktrees <<'JSON'
[{"branch":"feat/open","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  echo "feat/open" > "$GH_PR_OPEN"
  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "worktree with open PR is not removed by squash-merge fallback" {
  _write_worktrees <<'JSON'
[{"branch":"feat/pr-open","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑2","commit":{"timestamp":0}}]
JSON
  echo "feat/pr-open" > "$GH_PR_OPEN"
  _run_cleanup
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "open PR shown in dry-run skip" {
  _write_worktrees <<'JSON'
[{"branch":"feat/guarded","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  echo "feat/guarded" > "$GH_PR_OPEN"
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping: feat/guarded"* ]]
  [[ "$output" == *"open PR"* ]]
}

@test "old worktree with open PR is not removed by age" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/old-pr","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  echo "feat/old-pr" > "$GH_PR_OPEN"
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
}

@test "closed PR is not guarded — integrated worktree still removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/closed","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  echo "feat/closed" > "$GH_PR_CLOSED"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feat/closed"* ]]
  grep -q "feat/closed" "$WT_REMOVE_LOG"
}

@test "dirty worktree with open PR is not added to dirty-merged summary" {
  _write_worktrees <<'JSON'
[{"branch":"feat/dirty-open","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0},"working_tree":{"staged":false,"modified":true,"untracked":false,"renamed":false,"deleted":false}}]
JSON
  echo "feat/dirty-open" > "$GH_PR_OPEN"
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" != *"feat/dirty-open"* ]]
}

# ── Branch deletion ────────────────────────────────────────────────────────
#
# `wt` decides on its own whether the branch goes with the worktree, and asks an
# ancestry check a squash merge defeats. Where this script has already proved
# the branch merged it says so with --force-delete; where it has only proved the
# worktree idle it says nothing and the branch survives.

@test "a merged removal deletes the branch with the worktree" {
  _write_worktrees <<'JSON'
[{"branch":"feat/gone","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "a squash-merged removal deletes the branch too" {
  _write_worktrees <<'JSON'
[{"branch":"feat/squash-gone","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0}}]
JSON
  echo "feat/squash-gone" > "$GH_PR_MERGED"
  _run_cleanup
  [ "$status" -eq 0 ]
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "an age removal keeps the branch" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/idle","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  grep -q "feat/idle" "$WT_REMOVE_LOG"
  ! grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "a branch merged and idle at once is still deleted" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/old-and-merged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --age 30
  [ "$status" -eq 0 ]
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

@test "a dry run records the deletion it would have made" {
  _write_worktrees <<'JSON'
[{"branch":"feat/would-go","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [ ! -f "$WT_REMOVE_LOG" ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  grep -q "DRY-REMOVE branch=feat/would-go" "$log_file"
  grep -q "delete_branch=true" "$log_file"
}

@test "a dry run of an age removal records the branch it would keep" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/would-stay","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --dry-run --age 30
  [ "$status" -eq 0 ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  grep -q "DRY-REMOVE branch=feat/would-stay" "$log_file"
  grep -q "delete_branch=false" "$log_file"
}

@test "the worktree force flag is still passed alongside" {
  _write_worktrees <<'JSON'
[{"branch":"feat/both","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  grep -q -- "--force " "$WT_REMOVE_LOG"
  grep -q -- "--force-delete" "$WT_REMOVE_LOG"
}

# ── Forensic logging ──────────────────────────────────────────────────────

@test "removal is logged even in quiet mode" {
  _write_worktrees <<'JSON'
[{"branch":"feat/logged","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  [ -f "$log_file" ]
  grep -q "REMOVE branch=feat/logged" "$log_file"
  grep -q "reason=merged" "$log_file"
  grep -q "delete_branch=true" "$log_file"
}

@test "an age removal logs that the branch was kept" {
  local old_timestamp
  old_timestamp=$(( $(date +%s) - 100 * 86400 ))
  _write_worktrees <<JSON
[{"branch":"feat/logged-idle","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑3","commit":{"timestamp":$old_timestamp}}]
JSON
  _run_cleanup --quiet --age 30
  [ "$status" -eq 0 ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  grep -q "REMOVE branch=feat/logged-idle" "$log_file"
  grep -q "delete_branch=false" "$log_file"
}

@test "open-PR skip is logged" {
  _write_worktrees <<'JSON'
[{"branch":"feat/logged-open","is_main":false,"is_current":false,"main_state":"ahead","symbols":"↑1","commit":{"timestamp":0}}]
JSON
  echo "feat/logged-open" > "$GH_PR_OPEN"
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  local log_file="$TMPDIR/logs/wt-cleanup.log"
  [ -f "$log_file" ]
  grep -q "SKIP-OPEN-PR branch=feat/logged-open" "$log_file"
}
