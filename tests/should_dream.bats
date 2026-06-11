#!/usr/bin/env bats
# Tests for should-dream.sh — per-project dream cooldown checks.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR"
  SHOULD_DREAM="$REPO_ROOT/ai/claude/skills/dream/should-dream.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: create a project with a .last-dream timestamp and session files.
# Usage: _make_project <name> <last-dream-ts> <num-sessions> [session-mtime-ts]
# If last-dream-ts is empty, no .last-dream file is created (first run).
# session-mtime-ts defaults to now (recent sessions).
_make_project() {
  local name="$1"
  local last_dream_ts="$2"
  local num_sessions="$3"
  local session_mtime="${4:-}"

  local dir="$HOME/.claude/projects/$name"
  mkdir -p "$dir/memory"

  if [ -n "$last_dream_ts" ]; then
    echo "$last_dream_ts" > "$dir/memory/.last-dream"
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

@test "should-dream: project A recent, project B overdue with enough sessions → fires" {
  local now
  now=$(date +%s)
  local two_hours_ago=$((now - 7200))
  local forty_eight_hours_ago=$((now - 172800))

  _make_project "project-a" "$two_hours_ago" 10
  _make_project "project-b" "$forty_eight_hours_ago" 6

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: both projects dreamed recently → does not fire" {
  local now
  now=$(date +%s)
  local two_hours_ago=$((now - 7200))

  _make_project "project-a" "$two_hours_ago" 10
  _make_project "project-b" "$two_hours_ago" 10

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: project overdue on time but only 2 sessions → does not fire" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  _make_project "project-a" "$forty_eight_hours_ago" 2

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: no .last-dream files (first run) with enough sessions → fires" {
  local dir="$HOME/.claude/projects/new-project"
  mkdir -p "$dir/memory"

  local i
  for i in $(seq 1 6); do
    printf '{"type":"user"}\n' > "$dir/session-$i.jsonl"
  done

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: single project, overdue, enough sessions → fires" {
  local now
  now=$(date +%s)
  local thirty_hours_ago=$((now - 108000))

  _make_project "solo-project" "$thirty_hours_ago" 7

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: no projects at all → does not fire" {
  mkdir -p "$HOME/.claude/projects"

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

# ── Edge cases ───────────────────────────────────────────────────────────────

@test "should-dream: sessions older than last dream are not counted" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))
  local seventy_two_hours_ago=$((now - 259200))

  # Project overdue on time, but all 6 sessions have mtimes before last dream.
  # We use touch -t with a date in 2020 to ensure they're before last_dream.
  _make_project "stale-project" "$forty_eight_hours_ago" 6 "202001010000"

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: exactly at threshold (24h) does not fire" {
  local now
  now=$(date +%s)
  # Just under 24 hours ago (23h 59m)
  local just_under=$((now - 86340))

  _make_project "borderline" "$just_under" 10

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}

@test "should-dream: exactly 5 sessions meets minimum" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  _make_project "exact-min" "$forty_eight_hours_ago" 5

  run "$SHOULD_DREAM"
  [[ "$status" -eq 0 ]]
}

@test "should-dream: 4 sessions does not meet minimum" {
  local now
  now=$(date +%s)
  local forty_eight_hours_ago=$((now - 172800))

  _make_project "under-min" "$forty_eight_hours_ago" 4

  run "$SHOULD_DREAM"
  [[ "$status" -eq 1 ]]
}
