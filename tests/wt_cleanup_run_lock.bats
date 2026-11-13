#!/usr/bin/env bats
# Tests for wt-cleanup's live-run guard: a worktree whose checkout lock a live
# `pr` run holds is kept, merged or not, because removing it pulls the tree out
# from under that run. The holder takes the lock through core.run_lock itself,
# so a change to where the lock lives moves both sides at once.
bats_require_minimum_version 1.5.0
# select-on: bin/wt-cleanup lib/run_lock_cli.py ai/lib/core/run_lock.py

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
  HOLDER_PID=""
}

teardown() {
  if [[ -n "$HOLDER_PID" ]]; then
    kill "$HOLDER_PID" 2>/dev/null || true
    wait "$HOLDER_PID" 2>/dev/null || true
  fi
  common_teardown
}

# _hold_pr_checkout WT — start a process holding WT's checkout lock the way a `pr` run
# does (core.run_lock.acquire with worktree=), and return once it holds it.
# The markers are cleared so a suite run under `pr` cannot pass the lock through.
_hold_pr_checkout() {
  local wt="$1" ready="$TMPDIR/holder-ready"
  env -u WORKBENCH_RUN_LOCK -u WORKBENCH_RUN_LOCK_TREE \
    python3 - "$REPO_ROOT/ai/lib" "$TMPDIR/target" "$wt" "$ready" 3>&- <<'PY' &
import pathlib
import sys
import time

sys.path.insert(0, sys.argv[1])
import core.run_lock

with core.run_lock.acquire(pathlib.Path(sys.argv[2]), "pr review 7", "t",
                           worktree=pathlib.Path(sys.argv[3])):
    pathlib.Path(sys.argv[4]).touch()
    time.sleep(60)
PY
  HOLDER_PID=$!
  for _ in $(seq 1 100); do
    [[ -f "$ready" ]] && return 0
    sleep 0.1
  done
  return 1
}

# _end_holder — stop the holder and wait for it, so the kernel has dropped its flock.
_end_holder() {
  kill "$HOLDER_PID"
  wait "$HOLDER_PID" 2>/dev/null || true
  HOLDER_PID=""
}

# _write_merged_feature — main, plus `feature` (FEAT_WT) reported merged.
_write_merged_feature() {
  _write_worktrees <<JSON
[
  {"branch":"main","path":"$MAIN_WT","is_main":true,"is_current":false,"main_state":"clean","symbols":"","commit":{"timestamp":0}},
  {"branch":"feature","path":"$FEAT_WT","is_main":false,"is_current":false,"main_state":"integrated","symbols":"⊂","commit":{"timestamp":0}}
]
JSON
}

@test "a merged worktree a live pr run holds is kept" {
  _make_worktrees
  _write_merged_feature
  _hold_pr_checkout "$FEAT_WT"
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" != *"removing: feature"* ]]
  [ ! -s "$WT_REMOVE_LOG" ]
  grep -q "SKIP-HELD branch=feature reason=merged lock=held" "$CLEANUP_LOG_DIR/wt-cleanup.log"
}

# passes-at-base: a lock its run has released must not keep the worktree
@test "the same worktree is removed once that run has ended" {
  _make_worktrees
  _write_merged_feature
  _hold_pr_checkout "$FEAT_WT"
  _end_holder
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [[ "$output" == *"removing: feature"* ]]
  grep -q "feature" "$WT_REMOVE_LOG"
}

@test "dry-run names the live run as the reason" {
  _make_worktrees
  _write_merged_feature
  _hold_pr_checkout "$FEAT_WT"
  _run_cleanup --no-grace-period --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipping: feature (a live pr run holds it)"* ]]
}

@test "an existing worktree git cannot resolve is kept as unknown" {
  _make_worktrees
  _write_merged_feature
  rm -rf "$FEAT_WT"
  mkdir -p "$FEAT_WT"
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -s "$WT_REMOVE_LOG" ]
  grep -q "SKIP-HELD branch=feature reason=merged lock=unknown" "$CLEANUP_LOG_DIR/wt-cleanup.log"
}

@test "a probe that cannot answer keeps the worktree" {
  _make_worktrees
  _write_merged_feature
  printf 'raise SystemExit(1)\n' > "$TMPDIR/broken_cli.py"
  RUN_LOCK_CLI="$TMPDIR/broken_cli.py"
  _run_cleanup --no-grace-period
  [ "$status" -eq 0 ]
  [ ! -s "$WT_REMOVE_LOG" ]
  grep -q "SKIP-HELD branch=feature reason=merged lock=unknown" "$CLEANUP_LOG_DIR/wt-cleanup.log"
}
