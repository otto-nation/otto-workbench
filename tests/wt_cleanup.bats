#!/usr/bin/env bats
# Tests for wt-cleanup — merge detection, age-based removal, dry-run mode,
# quiet mode, and worktree protection (main/current).

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  WT_CLEANUP="$REPO_ROOT/bin/wt-cleanup"

  # Create mock bin directory with fake wt
  MOCK_BIN="$TMPDIR/bin"
  mkdir -p "$MOCK_BIN"

  # Fake wt that reads JSON from a file and logs remove calls
  WT_JSON="$TMPDIR/wt-list.json"
  WT_REMOVE_LOG="$TMPDIR/wt-removes.log"
  cat > "$MOCK_BIN/wt" <<'FAKEWT'
#!/usr/bin/env bash
if [[ "$1" == "list" ]]; then
  cat "$WT_JSON_FILE"
elif [[ "$1" == "remove" ]]; then
  echo "$2" >> "$WT_REMOVE_LOG_FILE"
fi
FAKEWT
  chmod +x "$MOCK_BIN/wt"

  # Fake gh — branches listed in GH_PR_MERGED_FILE return "MERGED"
  GH_PR_MERGED="$TMPDIR/gh-pr-merged.txt"
  cat > "$MOCK_BIN/gh" <<'FAKEGH'
#!/usr/bin/env bash
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  [[ -f "$GH_PR_MERGED_FILE" ]] && exit 0
  exit 1
elif [[ "$1" == "pr" && "$2" == "view" ]]; then
  branch="$3"
  if [[ -f "$GH_PR_MERGED_FILE" ]] && grep -qx "$branch" "$GH_PR_MERGED_FILE"; then
    echo "MERGED"
    exit 0
  fi
  exit 1
fi
exit 1
FAKEGH
  chmod +x "$MOCK_BIN/gh"
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
[{"branch":"feat/old","is_main":false,"is_current":false,"main_state":"merged","symbols":"⊂","commit":{"timestamp":0}}]
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

@test "merged via main_state field is removed" {
  _write_worktrees <<'JSON'
[{"branch":"feat/merged","is_main":false,"is_current":false,"main_state":"merged","symbols":"","commit":{"timestamp":0}}]
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
[{"branch":"main","is_main":true,"is_current":false,"main_state":"merged","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale worktrees"* ]]
}

@test "current worktree is skipped even if merged" {
  _write_worktrees <<'JSON'
[{"branch":"feat/active","is_main":false,"is_current":true,"main_state":"merged","symbols":"⊂","commit":{"timestamp":0}}]
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
[{"branch":"feat/bye","is_main":false,"is_current":false,"main_state":"merged","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"would remove: feat/bye"* ]]
  [ ! -f "$WT_REMOVE_LOG" ]
}

# ── Quiet mode ───────────────────────────────────────────────────────────────

@test "--quiet suppresses output" {
  _write_worktrees <<'JSON'
[{"branch":"feat/silent","is_main":false,"is_current":false,"main_state":"merged","symbols":"⊂","commit":{"timestamp":0}}]
JSON
  _run_cleanup --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}
