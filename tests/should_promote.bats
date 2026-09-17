#!/usr/bin/env bats
# Tests for should-promote.sh — per-repo promote cooldown checks.
#
# The gate sweeps the project registry and counts a repo's sessions across
# every harness and every worktree, so the fixtures here are registered repos
# rather than bare directories under `.claude/projects`.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and the gate encodes the path git gives it
  # into the directory it looks for the memory in.
  TEST_HOME="$(cd "$(mktemp -d)" && pwd -P)"
  export HOME="$TEST_HOME"
  gate_sandbox
  SHOULD_PROMOTE="$REPO_ROOT/ai/skills/promote/should-promote.sh"
}

teardown() {
  rm -rf "$TEST_HOME"
  common_teardown
}

# _make_project NAME LAST_PROMOTE_TS NUM_SESSIONS [SESSION_MTIME]
# A registered repo with a memory directory, a promote stamp and Claude
# sessions. An empty LAST_PROMOTE_TS leaves no stamp, which is the first-run
# case.
_make_project() {
  local name="$1" last_promote_ts="$2" num_sessions="$3" session_mtime="${4:-}"
  local repo memory
  repo="$(gate_repo "$name")"
  memory="$(gate_memory "$repo")"

  if [ -n "$last_promote_ts" ]; then
    echo "$last_promote_ts" > "$memory/.last-promote"
  fi

  gate_sessions "$(gate_claude_dir "$repo")" "$num_sessions" "$session_mtime"
}

# ── Per-repo cooldown logic ───────────────────────────────────────────────

@test "should-promote: project A recent, project B overdue with enough sessions → fires" {
  local now
  now=$(date +%s)
  local two_hours_ago=$((now - 7200))
  local eight_days_ago=$((now - 691200))

  _make_project "project-a" "$two_hours_ago" 15
  _make_project "project-b" "$eight_days_ago" 12

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}

@test "should-promote: both projects promoted recently → does not fire" {
  local now
  now=$(date +%s)
  local two_hours_ago=$((now - 7200))

  _make_project "project-a" "$two_hours_ago" 15
  _make_project "project-b" "$two_hours_ago" 15

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 1 ]]
}

@test "should-promote: project overdue on time but only 2 sessions → does not fire" {
  local now
  now=$(date +%s)
  local eight_days_ago=$((now - 691200))

  _make_project "project-a" "$eight_days_ago" 2

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 1 ]]
}

@test "should-promote: no .last-promote files (first run) with enough sessions → fires" {
  _make_project "new-project" "" 11

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}

@test "should-promote: single project, overdue, enough sessions → fires" {
  local now
  now=$(date +%s)
  local ten_days_ago=$((now - 864000))

  _make_project "solo-project" "$ten_days_ago" 12

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}

@test "should-promote: no projects at all → does not fire" {
  mkdir -p "$HOME/.claude/projects"

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 1 ]]
}

# ── Edge cases ───────────────────────────────────────────────────────────────

@test "should-promote: sessions older than last promote are not counted" {
  local now
  now=$(date +%s)
  local eight_days_ago=$((now - 691200))

  # Project overdue on time, but all sessions have mtimes before last promote.
  _make_project "stale-project" "$eight_days_ago" 12 "202001010000"

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 1 ]]
}

@test "should-promote: just under 168h does not fire" {
  local now
  now=$(date +%s)
  # 167h 59m = 604740 seconds
  local just_under=$((now - 604740))

  _make_project "borderline" "$just_under" 15

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 1 ]]
}

@test "should-promote: just over 168h fires" {
  local now
  now=$(date +%s)
  # 168h 1m = 604860 seconds
  local just_over=$((now - 604860))

  _make_project "borderline-over" "$just_over" 15

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}

@test "should-promote: exactly 10 sessions meets minimum" {
  local now
  now=$(date +%s)
  local eight_days_ago=$((now - 691200))

  _make_project "exact-min" "$eight_days_ago" 10

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}

@test "should-promote: 9 sessions does not meet minimum" {
  local now
  now=$(date +%s)
  local eight_days_ago=$((now - 691200))

  _make_project "under-min" "$eight_days_ago" 9

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 1 ]]
}

# ── Repos without memory/ ───────────────────────────────────────────────────

@test "should-promote: repo without memory/ dir is ignored even with many sessions" {
  local repo
  repo="$(gate_repo "no-memory-project")"
  gate_sessions "$(gate_claude_dir "$repo")" 20

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 1 ]]
}

@test "should-promote: repo without memory/ does not block eligible repo" {
  local now
  now=$(date +%s)
  local eight_days_ago=$((now - 691200))

  local no_mem
  no_mem="$(gate_repo "aaa-no-memory")"
  gate_sessions "$(gate_claude_dir "$no_mem")" 20

  _make_project "zzz-has-memory" "$eight_days_ago" 12

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}

# ── Harness and worktree coverage ────────────────────────────────────────────
# The bug this gate was rebuilt for: sessions counted under Claude Code only,
# in the one directory named for the repo root. Interactive work moved to Pi
# and spread across worktrees, and the gate stopped firing for months.

@test "should-promote: Pi sessions count toward the minimum" {
  local now
  now=$(date +%s)
  local eight_days_ago=$((now - 691200))

  local repo memory
  repo="$(gate_repo "pi-only")"
  memory="$(gate_memory "$repo")"
  echo "$eight_days_ago" > "$memory/.last-promote"
  gate_sessions "$(gate_pi_dir "$repo")" 12

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}

@test "should-promote: sessions spread across worktrees count toward one repo" {
  local now
  now=$(date +%s)
  local eight_days_ago=$((now - 691200))

  local repo memory
  repo="$(gate_repo "spread")"
  memory="$(gate_memory "$repo")"
  echo "$eight_days_ago" > "$memory/.last-promote"

  # Four sessions in each of three worktrees: under the minimum of 10 alone,
  # over it together. Counting any single directory would leave this shut.
  gate_sessions "$(gate_claude_dir "$repo/main")" 4
  gate_sessions "$(gate_claude_dir "$repo/feature-a")" 4
  gate_sessions "$(gate_pi_dir "$repo/feature-b")" 4

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}
