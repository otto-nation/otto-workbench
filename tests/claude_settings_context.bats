#!/usr/bin/env bats
# Tests for the compaction half of step_claude_settings: config.yml's context.compact_at written into ~/.claude/settings.json.
# select-on: ai/lib/agent/context_ceiling.py lib/config_cli.py lib/config.sh

setup() {
  load 'test_helper'
  load 'claude_settings_helper'
  common_setup
  HOME_DIR="$BATS_TEST_TMPDIR/h"
  STATE_DIR="$BATS_TEST_TMPDIR/s"
  SETTINGS_OUT="$HOME_DIR/.claude/settings.json"
}

teardown() {
  common_teardown
}

# _seed_config LINE... — writes the sandbox's machine-wide config.yml.
_seed_config() {
  mkdir -p "$HOME_DIR/.config/workbench"
  printf '%s\n' "$@" > "$HOME_DIR/.config/workbench/config.yml"
}

# _seed_settings JSON — writes the sandbox's live settings.json before a sync.
_seed_settings() {
  mkdir -p "$HOME_DIR/.claude"
  printf '%s\n' "$1" > "$SETTINGS_OUT"
}

@test "the ceiling becomes Claude Code's auto-compact window" {
  _seed_settings '{"autoCompactWindow":900000}'
  _seed_config 'context:' '  compact_at: 200000'
  run _sync_run "$HOME_DIR" "$STATE_DIR"
  [ "$status" -eq 0 ]
  run jq -r '.autoCompactWindow' "$SETTINGS_OUT"
  [ "$output" = "200000" ]
}

@test "a ceiling below what Claude Code takes runs at its floor, with a warning" {
  _seed_config 'context:' '  compact_at: 50000'
  run _sync_run "$HOME_DIR" "$STATE_DIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"context.compact_at: 50,000 is outside"*"so it compacts at 100,000"* ]]
  run jq -r '.autoCompactWindow' "$SETTINGS_OUT"
  [ "$output" = "100000" ]
}

@test "unsetting the ceiling withdraws the window a sync wrote" {
  _seed_config 'context:' '  compact_at: 200000'
  _sync_run "$HOME_DIR" "$STATE_DIR"
  _seed_config 'models: {}'
  run _sync_run "$HOME_DIR" "$STATE_DIR"
  [ "$status" -eq 0 ]
  run jq -c 'has("autoCompactWindow")' "$SETTINGS_OUT"
  [ "$output" = "false" ]
}

@test "unsetting the ceiling keeps a window changed since the sync wrote it" {
  _seed_config 'context:' '  compact_at: 200000'
  _sync_run "$HOME_DIR" "$STATE_DIR"
  jq '.autoCompactWindow = 600000' "$SETTINGS_OUT" > "$BATS_TEST_TMPDIR/edited.json"
  mv "$BATS_TEST_TMPDIR/edited.json" "$SETTINGS_OUT"
  _seed_config 'models: {}'
  run _sync_run "$HOME_DIR" "$STATE_DIR"
  [ "$status" -eq 0 ]
  run jq -r '.autoCompactWindow' "$SETTINGS_OUT"
  [ "$output" = "600000" ]
}

@test "with no ceiling the window an older /autocompact saved is kept" {
  _seed_settings '{"autoCompactWindow":500000}'
  run _sync_run "$HOME_DIR" "$STATE_DIR"
  [ "$status" -eq 0 ]
  run jq -r '.autoCompactWindow' "$SETTINGS_OUT"
  [ "$output" = "500000" ]
}

@test "an unreadable ceiling warns and leaves the window as it was" {
  _seed_settings '{"autoCompactWindow":500000}'
  _seed_config 'context:' '  compact_at: -1'
  run _sync_run "$HOME_DIR" "$STATE_DIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Claude Code's auto-compact window left as it was"* ]]
  run jq -r '.autoCompactWindow' "$SETTINGS_OUT"
  [ "$output" = "500000" ]
}
