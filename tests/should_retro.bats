#!/usr/bin/env bats
# Tests for should-retro.sh — global retro cooldown checks.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(mktemp -d)"
  export HOME="$TEST_HOME"
  SHOULD_RETRO="$REPO_ROOT/ai/claude/skills/retro/should-retro.sh"
}

teardown() {
  rm -rf "$TEST_HOME"
  common_teardown
}

_make_project() {
  local name="$1"
  local num_sessions="$2"
  local session_mtime="${3:-}"

  local dir="$HOME/.claude/projects/$name"
  mkdir -p "$dir/memory"

  local i
  for i in $(seq 1 "$num_sessions"); do
    printf '{"type":"user"}\n' > "$dir/session-$i.jsonl"
    if [ -n "$session_mtime" ]; then
      touch -t "$session_mtime" "$dir/session-$i.jsonl"
    fi
  done
}

@test "should-retro: overdue (4 days) with enough sessions → fires" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"
  _make_project "test-proj" 6

  run "$SHOULD_RETRO"
  [[ "$status" -eq 0 ]]
}

@test "should-retro: recent (1 day ago) → does not fire" {
  local now
  now=$(date +%s)
  local one_day_ago=$((now - 86400))

  mkdir -p "$HOME/.claude"
  echo "$one_day_ago" > "$HOME/.claude/.last-retro"
  _make_project "test-proj" 10

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: overdue but only 2 sessions → does not fire" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"
  _make_project "test-proj" 2

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: no .last-retro (first run) with enough sessions → fires" {
  _make_project "test-proj" 6

  run "$SHOULD_RETRO"
  [[ "$status" -eq 0 ]]
}

@test "should-retro: no projects → does not fire" {
  mkdir -p "$HOME/.claude/projects"

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: exactly 5 sessions meets minimum" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"
  _make_project "test-proj" 5

  run "$SHOULD_RETRO"
  [[ "$status" -eq 0 ]]
}

@test "should-retro: 4 sessions does not meet minimum" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"
  _make_project "test-proj" 4

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: sessions older than last retro are not counted" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"
  _make_project "test-proj" 6 "202001010000"

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}

@test "should-retro: uses global timestamp, checks sessions across any project" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"

  _make_project "proj-a" 2
  _make_project "proj-b" 3

  run "$SHOULD_RETRO"
  [[ "$status" -eq 1 ]]
}
