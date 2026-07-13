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
  echo "$2" >> "$WT_REMOVE_LOG_FILE"
fi
FAKEWT
  chmod +x "$MOCK_BIN/wt"

  cat > "$MOCK_BIN/gh" <<'FAKEGH'
#!/usr/bin/env bash
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  [[ -f "$GH_PR_MERGED_FILE" || -f "$GH_PR_OPEN_FILE" || -f "$GH_PR_CLOSED_FILE" ]] && exit 0
  exit 1
elif [[ "$1" == "pr" && "$2" == "view" ]]; then
  branch="$3"
  if [[ -f "$GH_PR_MERGED_FILE" ]] && grep -qx "$branch" "$GH_PR_MERGED_FILE"; then
    echo "MERGED"
    exit 0
  elif [[ -f "$GH_PR_OPEN_FILE" ]] && grep -qx "$branch" "$GH_PR_OPEN_FILE"; then
    echo "OPEN"
    exit 0
  elif [[ -f "$GH_PR_CLOSED_FILE" ]] && grep -qx "$branch" "$GH_PR_CLOSED_FILE"; then
    echo "CLOSED"
    exit 0
  fi
  exit 1
fi
exit 1
FAKEGH
  chmod +x "$MOCK_BIN/gh"

  export MOCK_BIN
  export WT_CLEANUP="$REPO_ROOT/bin/wt-cleanup"
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
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run wt-cleanup with mocked wt and gh
_run_cleanup() {
  PATH="$MOCK_BIN:$PATH" \
    WT_JSON_FILE="$WT_JSON" \
    WT_REMOVE_LOG_FILE="$WT_REMOVE_LOG" \
    GH_PR_MERGED_FILE="$GH_PR_MERGED" \
    GH_PR_OPEN_FILE="$GH_PR_OPEN" \
    GH_PR_CLOSED_FILE="$GH_PR_CLOSED" \
    CLEANUP_LOG_DIR="$TMPDIR/logs" \
    NO_COLOR=1 \
    run "$WT_CLEANUP" "$@"
}

# Helper: write worktree JSON
_write_worktrees() {
  cat > "$WT_JSON"
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "wt-cleanup --help exits 0" {
  run "$WT_CLEANUP" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"worktrees"* ]]
}

@test "wt-cleanup -h exits 0" {
  run "$WT_CLEANUP" -h
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
