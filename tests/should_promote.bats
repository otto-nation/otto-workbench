#!/usr/bin/env bats
# Tests for should-promote.sh — per-project promote cooldown checks.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(mktemp -d)"
  export HOME="$TEST_HOME"
  SHOULD_PROMOTE="$REPO_ROOT/ai/claude/skills/promote/should-promote.sh"
}

teardown() {
  rm -rf "$TEST_HOME"
  common_teardown
}

# Helper: create a project with a .last-promote timestamp and session files.
# Usage: _make_project <name> <last-promote-ts> <num-sessions> [session-mtime-ts]
# If last-promote-ts is empty, no .last-promote file is created (first run).
# session-mtime-ts defaults to now (recent sessions).
_make_project() {
  local name="$1"
  local last_promote_ts="$2"
  local num_sessions="$3"
  local session_mtime="${4:-}"

  local dir="$HOME/.claude/projects/$name"
  mkdir -p "$dir/memory"

  if [ -n "$last_promote_ts" ]; then
    echo "$last_promote_ts" > "$dir/memory/.last-promote"
  fi

  local i
  for i in $(seq 1 "$num_sessions"); do
    printf '{"type":"user"}\n' > "$dir/session-$i.jsonl"
    if [ -n "$session_mtime" ]; then
      touch -t "$session_mtime" "$dir/session-$i.jsonl"
    fi
  done
}

# ── Per-project cooldown logic ───────────────────────────────────────────────

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
  local dir="$HOME/.claude/projects/new-project"
  mkdir -p "$dir/memory"

  local i
  for i in $(seq 1 11); do
    printf '{"type":"user"}\n' > "$dir/session-$i.jsonl"
  done

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

# ── Projects without memory/ ────────────────────────────────────────────────

@test "should-promote: project without memory/ dir is ignored even with many sessions" {
  local dir="$HOME/.claude/projects/no-memory-project"
  mkdir -p "$dir"

  local i
  for i in $(seq 1 20); do
    printf '{"type":"user"}\n' > "$dir/session-$i.jsonl"
  done

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 1 ]]
}

@test "should-promote: project without memory/ does not block eligible project" {
  local now
  now=$(date +%s)
  local eight_days_ago=$((now - 691200))

  local no_mem_dir="$HOME/.claude/projects/aaa-no-memory"
  mkdir -p "$no_mem_dir"
  local i
  for i in $(seq 1 20); do
    printf '{"type":"user"}\n' > "$no_mem_dir/session-$i.jsonl"
  done

  _make_project "zzz-has-memory" "$eight_days_ago" 12

  run "$SHOULD_PROMOTE"
  [[ "$status" -eq 0 ]]
}
