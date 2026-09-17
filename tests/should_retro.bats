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
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and the gate encodes the path git gives it
  # into the directory it looks for the memory in.
  TEST_HOME="$(cd "$(mktemp -d)" && pwd -P)"
  export HOME="$TEST_HOME"
  gate_sandbox
  SHOULD_RETRO="$REPO_ROOT/ai/skills/retro/should-retro.sh"
}

teardown() {
  rm -rf "$TEST_HOME"
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

@test "should-retro: skips repos without memory/ directory" {
  local now
  now=$(date +%s)
  local four_days_ago=$((now - 345600))

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"

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

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"

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

  mkdir -p "$HOME/.claude"
  echo "$four_days_ago" > "$HOME/.claude/.last-retro"

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
