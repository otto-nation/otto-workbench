#!/usr/bin/env bats
# Tests for should-retro.sh — global retro cooldown checks.
#
# The stamp is global, but the session count behind it is per repo across every
# harness and worktree, so the fixtures here are registered repos rather than
# bare directories under `.claude/projects`.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # Fully resolved: on macOS $TMPDIR is a /var/folders path that git reports
  # as /private/var/folders, and the gate encodes the path git gives it into
  # the directory it looks for the memory in.
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  gate_sandbox
  SHOULD_RETRO="$REPO_ROOT/ai/skills/retro/should-retro.sh"
}

teardown() {
  common_teardown
}

# _make_project NAME NUM_SESSIONS [SESSION_MTIME] — a registered repo with a
# memory directory and Claude sessions. The retro stamp is global, so unlike
# the dream and promote fixtures this one writes none.
_make_project() {
  local name="$1" num_sessions="$2" session_mtime="${3:-}"
  local repo
  repo="$(gate_repo "$name")"
  gate_memory "$repo" > /dev/null
  gate_sessions "$(gate_claude_dir "$repo")" "$num_sessions" "$session_mtime"
}

# _stamp_file — where the gate records the global retro cooldown. Spelled out
# rather than sourced, so the test would catch the location changing out from
# under the gate. Unslugged, unlike the per-repo stamps beside it: a retro is
# one sweep over every repo rather than a per-repo pass.
_stamp_file() {
  printf '%s/gates/last-retro' "$WORKBENCH_STATE_DIR"
}

# _write_stamp TS — the global retro stamp at TS.
_write_stamp() {
  mkdir -p "$WORKBENCH_STATE_DIR/gates"
  echo "$1" > "$(_stamp_file)"
}

@test "should-retro: overdue (4 days) with enough sessions → fires" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"
  _make_project "test-proj" 6

  run "$SHOULD_RETRO"
  [[ "$status" -eq 0 ]]
}

@test "should-retro: recent (1 day ago) → does not fire" {
  local now
  now=$(date +%s)
  local one_day_ago=$((now - 86400))

  _write_stamp "$one_day_ago"
  _make_project "test-proj" 10

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: overdue but only 2 sessions → does not fire" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"
  _make_project "test-proj" 2

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: no stamp (first run) with enough sessions → fires" {
  _make_project "test-proj" 6

  run "$SHOULD_RETRO"
  [[ "$status" -eq 0 ]]
}

@test "should-retro: no projects → does not fire" {
  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: exactly 5 sessions meets minimum" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"
  _make_project "test-proj" 5

  run "$SHOULD_RETRO"
  [[ "$status" -eq 0 ]]
}

@test "should-retro: 4 sessions does not meet minimum" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"
  _make_project "test-proj" 4

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: sessions older than last retro are not counted" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"
  _make_project "test-proj" 6 "202001010000"

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: uses global timestamp, checks sessions across any project" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"

  _make_project "proj-a" 2
  _make_project "proj-b" 3

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: skips repos without memory/ directory" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"

  local repo
  repo="$(gate_repo "no-memory-proj")"
  gate_sessions "$(gate_claude_dir "$repo")" 10

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

# ── Harness and worktree coverage ────────────────────────────────────────────

@test "should-retro: Pi sessions count toward the minimum" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"

  local repo
  repo="$(gate_repo "pi-only")"
  gate_memory "$repo" > /dev/null
  gate_sessions "$(gate_pi_dir "$repo")" 6

  run "$SHOULD_RETRO"
  [[ "$status" -eq 0 ]]
}

@test "should-retro: sessions spread across worktrees count toward one repo" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  _write_stamp "$four_days_ago"

  local repo
  repo="$(gate_repo "spread")"
  gate_memory "$repo" > /dev/null

  # Two per worktree: under the minimum of 5 alone, over it together.
  gate_sessions "$(gate_claude_dir "$repo/main")" 2
  gate_sessions "$(gate_claude_dir "$repo/feature-a")" 2
  gate_sessions "$(gate_pi_dir "$repo/feature-b")" 2

  run "$SHOULD_RETRO"
  [[ "$status" -eq 0 ]]
}

# ── The completion script and the gate agree on the stamp ────────────────────

@test "retro-complete settles the gate it is paired with" {
  local now
  now=$(date +%s)
  _write_stamp "$((now - 345600))"
  _make_project "test-proj" 6

  run "$SHOULD_RETRO"
  [ "$status" -eq 0 ]

  run "$REPO_ROOT/ai/skills/retro/retro-complete.sh" "test-scan-id"
  [ "$status" -eq 0 ]

  run "$SHOULD_RETRO"
  [ "$status" -eq 1 ]
}

@test "retro-complete writes the stamp where the gate reads it" {
  # The stamp is written in bash and read again in Python by retro-scan, so a
  # location the two disagree on reads as a first run: every merged PR refetched
  # and every local review deleted.
  run "$REPO_ROOT/ai/skills/retro/retro-complete.sh" "test-scan-id"
  [ "$status" -eq 0 ]
  [ -f "$(_stamp_file)" ]
}
