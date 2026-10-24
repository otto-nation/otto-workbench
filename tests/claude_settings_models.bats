#!/usr/bin/env bats
# Tests for the model half of step_claude_settings: config.yml's models.* written into ~/.claude/settings.json.
# select-on: ai/lib/agent/harness_models.py lib/config_cli.py lib/config.sh

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

@test "each tier becomes the Claude alias standing behind it" {
  _seed_config 'models:' \
    '  deep: claude-opus-5-5:high' \
    '  standard: anthropic/claude-sonnet-5-5' \
    '  light: claude-haiku-5-5'
  _sync_run "$HOME_DIR" "$STATE_DIR"
  run jq -c '.env | {ANTHROPIC_DEFAULT_OPUS_MODEL, ANTHROPIC_DEFAULT_SONNET_MODEL, ANTHROPIC_DEFAULT_HAIKU_MODEL}' "$SETTINGS_OUT"
  [ "$status" -eq 0 ]
  [ "$output" = '{"ANTHROPIC_DEFAULT_OPUS_MODEL":"claude-opus-5-5","ANTHROPIC_DEFAULT_SONNET_MODEL":"claude-sonnet-5-5","ANTHROPIC_DEFAULT_HAIKU_MODEL":"claude-haiku-5-5"}' ]
}

@test "the session takes model and its level as effortLevel" {
  _seed_config 'models:' '  deep: claude-opus-5-5' '  session: deep:xhigh'
  _sync_run "$HOME_DIR" "$STATE_DIR"
  run jq -r '.model + " " + .effortLevel' "$SETTINGS_OUT"
  [ "$status" -eq 0 ]
  [ "$output" = "claude-opus-5-5 xhigh" ]
}

@test "a session with no Claude effort keeps the effortLevel /effort set" {
  _seed_settings '{"model":"old","effortLevel":"high"}'
  _seed_config 'models:' '  session: claude-sonnet-5-5:minimal'
  _sync_run "$HOME_DIR" "$STATE_DIR"
  run jq -r '.model + " " + .effortLevel' "$SETTINGS_OUT"
  [ "$output" = "claude-sonnet-5-5 high" ]
}

@test "with no session, model and effortLevel stay what /model left" {
  _seed_settings '{"model":"claude-opus-5-5","effortLevel":"low"}'
  _seed_config 'models:' '  light: claude-haiku-5-5'
  _sync_run "$HOME_DIR" "$STATE_DIR"
  run jq -r '.model + " " + .effortLevel' "$SETTINGS_OUT"
  [ "$output" = "claude-opus-5-5 low" ]
}

@test "an alias whose tier is unset is withdrawn, and so is ANTHROPIC_MODEL" {
  _seed_settings '{"env":{"ANTHROPIC_DEFAULT_OPUS_MODEL":"stale","ANTHROPIC_MODEL":"stale","OPERATOR_OWN_VAR":"keep-me"}}'
  _seed_config 'models:' '  light: claude-haiku-5-5'
  _sync_run "$HOME_DIR" "$STATE_DIR"
  run jq -c '.env' "$SETTINGS_OUT"
  [ "$output" = '{"OPERATOR_OWN_VAR":"keep-me","ANTHROPIC_DEFAULT_HAIKU_MODEL":"claude-haiku-5-5"}' ]
}

@test "a tier on a provider the Claude CLI cannot run is left unset, with a warning" {
  _seed_settings '{"env":{"ANTHROPIC_DEFAULT_HAIKU_MODEL":"stale"}}'
  _seed_config 'models:' '  light: openai/gpt-6-luna' '  deep: google-vertex-claude/claude-opus-5-5'
  run _sync_run "$HOME_DIR" "$STATE_DIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"models.light: openai/gpt-6-luna is not a Claude model"* ]]
  run jq -c '.env' "$SETTINGS_OUT"
  [ "$output" = '{"ANTHROPIC_DEFAULT_OPUS_MODEL":"claude-opus-5-5"}' ]
}

@test "a config naming an unset tier leaves the model settings as they were" {
  _seed_settings '{"model":"claude-opus-5-5","env":{"ANTHROPIC_DEFAULT_OPUS_MODEL":"claude-opus-5-5"}}'
  _seed_config 'models:' '  session: standard'
  run _sync_run "$HOME_DIR" "$STATE_DIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Claude Code's model settings left as they were"* ]]
  run jq -c '{model, env}' "$SETTINGS_OUT"
  [ "$output" = '{"model":"claude-opus-5-5","env":{"ANTHROPIC_DEFAULT_OPUS_MODEL":"claude-opus-5-5"}}' ]
}

@test "a second sync reproduces the model settings byte for byte" {
  _seed_config 'models:' '  deep: claude-opus-5-5:high' '  session: deep'
  _sync_run "$HOME_DIR" "$STATE_DIR"
  cp "$SETTINGS_OUT" "$BATS_TEST_TMPDIR/first.json"
  _sync_run "$HOME_DIR" "$STATE_DIR"
  diff "$BATS_TEST_TMPDIR/first.json" "$SETTINGS_OUT"
}
