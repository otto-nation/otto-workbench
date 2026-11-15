#!/usr/bin/env bats
# Tests for maintenance/bin/sweep-worktrees — one wt-cleanup per registered
# repo, from the default-branch worktree of a bare container when it exists.
#
# select-on: maintenance/bin/sweep-worktrees maintenance/bin/otto-workbench-maintenance bin/wt-cleanup lib/projects.sh

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  gate_sandbox

  SCRIPT="$REPO_ROOT/maintenance/bin/sweep-worktrees"
  SCRIPT_MAINT="$REPO_ROOT/maintenance/bin/otto-workbench-maintenance"
  SWEEP_LOG="$TEST_HOME/sweep.log"
  STUB_BIN="$TEST_HOME/stub-bin"
  mkdir -p "$STUB_BIN"
  _stub_wt_cleanup
}

teardown() {
  common_teardown
}

# _stub_wt_cleanup — records "$PWD" per call, prints a count line, and exits 3
# when PWD matches FAIL_IN. STUB_NO_COUNT suppresses the count line.
# STUB_NO_DIRTY prints removed= without a dirty_merged token.
_stub_wt_cleanup() {
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$PWD" >> "%s"\nif [[ -n "${FAIL_IN:-}" && "$PWD" == "$FAIL_IN" ]]; then\n  exit 3\nfi\nif [[ -n "${STUB_NO_COUNT:-}" ]]; then\n  exit 0\nfi\nif [[ -n "${STUB_NO_DIRTY:-}" ]]; then\n  printf "removed=%%s\\n" "${STUB_REMOVED:-1}"\n  exit 0\nfi\nprintf "removed=%%s dirty_merged=%%s\\n" "${STUB_REMOVED:-1}" "${STUB_DIRTY:-0}"\n' \
    "$SWEEP_LOG" > "$STUB_BIN/wt-cleanup"
  chmod +x "$STUB_BIN/wt-cleanup"
}

# _run_sweep — from `/`, the cwd launchd gives the timer. env -i so the
# ambient PATH cannot smuggle in the machine's real wt-cleanup.
_run_sweep() {
  run env -i \
    HOME="$TEST_HOME" \
    PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" \
    WORKBENCH_CACHE_DIR="$WORKBENCH_CACHE_DIR" \
    WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" \
    WT_CLEANUP_BIN="$STUB_BIN/wt-cleanup" \
    FAIL_IN="${FAIL_IN:-}" \
    STUB_REMOVED="${STUB_REMOVED:-}" \
    STUB_DIRTY="${STUB_DIRTY:-}" \
    STUB_NO_COUNT="${STUB_NO_COUNT:-}" \
    STUB_NO_DIRTY="${STUB_NO_DIRTY:-}" \
    bash -c "cd / && '$SCRIPT'"
}

@test "two repos plus a second worktree of one are swept once each" {
  local repo_a repo_b extra
  repo_a="$(gate_repo a)"
  repo_b="$(gate_repo b)"
  extra="$TEST_HOME/a-feat"
  git -C "$repo_a" config user.email test@example.com
  git -C "$repo_a" config user.name Test
  git -C "$repo_a" commit --allow-empty -qm init
  git -C "$repo_a" worktree add -q -b feat "$extra"
  printf '%s\n' "$extra" >> "$WORKBENCH_STATE_DIR/projects.registry"

  _run_sweep
  [ "$status" -eq 0 ]
  [ "$output" = "repos=2 removed=2 dirty=0 failed=0" ]
  [ "$(wc -l < "$SWEEP_LOG" | tr -d ' ')" = "2" ]
  grep -qx "$repo_a" "$SWEEP_LOG"
  grep -qx "$repo_b" "$SWEEP_LOG"
  ! grep -qx "$extra" "$SWEEP_LOG"
}

@test "a failed repo is counted and the rest still run" {
  local repo_a
  repo_a="$(gate_repo a)"
  gate_repo b >/dev/null
  FAIL_IN="$repo_a"

  _run_sweep
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARNING: wt-cleanup failed in"* ]]
  [[ "$output" == *"repos=2 removed=1 dirty=0 failed=1"* ]]
  [ "$(wc -l < "$SWEEP_LOG" | tr -d ' ')" = "2" ]
}

@test "a bare container registered only at a feature worktree is swept from main" {
  local seed container
  seed="$(cd "$TMPDIR" && pwd -P)/seed"
  container="$(cd "$TMPDIR" && pwd -P)/c"
  mkdir -p "$seed"
  printf 'x\n' > "$seed/a.txt"
  make_container_seed "$seed"
  make_worktree_container "$container" "$seed"
  git -C "$container" worktree add -q "$container/feat" feat
  printf '%s\n' "$container/feat" >> "$WORKBENCH_STATE_DIR/projects.registry"

  _run_sweep
  [ "$status" -eq 0 ]
  [ "$output" = "repos=1 removed=1 dirty=0 failed=0" ]
  [ "$(cat "$SWEEP_LOG")" = "$container/main" ]
}

@test "an empty registry visits nothing" {
  _run_sweep
  [ "$status" -eq 0 ]
  [ "$output" = "repos=0 removed=0 dirty=0 failed=0" ]
  [ ! -f "$SWEEP_LOG" ]
}

@test "a missing count line is counted as zero removals, not a failure" {
  gate_repo a >/dev/null
  STUB_NO_COUNT=1

  _run_sweep
  [ "$status" -eq 0 ]
  [ "$output" = "repos=1 removed=0 dirty=0 failed=0" ]
  [ "$(wc -l < "$SWEEP_LOG" | tr -d ' ')" = "1" ]
}

@test "dirty_merged counts sum across repos" {
  gate_repo a >/dev/null
  gate_repo b >/dev/null
  STUB_DIRTY=2

  _run_sweep
  [ "$status" -eq 0 ]
  [ "$output" = "repos=2 removed=2 dirty=4 failed=0" ]
}

@test "a missing dirty_merged token is counted as zero" {
  gate_repo a >/dev/null
  STUB_NO_DIRTY=1

  _run_sweep
  [ "$status" -eq 0 ]
  [ "$output" = "repos=1 removed=1 dirty=0 failed=0" ]
}
