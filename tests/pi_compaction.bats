#!/usr/bin/env bats
# Tests for step_pi_compaction in ai/pi/steps.sh — sizing Pi's per-model
# compaction reserves from context.compact_at against the catalogue pi lists.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  load 'pi_settings_helper'
  common_setup
  pi_settings_setup
  # Read by _run_step in pi_settings_helper.bash.
  # shellcheck disable=SC2034
  PI_STEPS="$REPO_ROOT/ai/pi/steps.sh"
}

teardown() {
  common_teardown
}

# One id under two providers with different windows, so a reserve keyed by the
# bare id would be wrong for one of them; and a 200K model, at the ceiling.
_stub_pi_list_models() {
  cat > "$BIN/pi" << 'SCRIPT'
#!/usr/bin/env bash
[[ "$1" == "--list-models" ]] || exit 1
cat << 'LIST'
provider              model              context  max-out  thinking  images
anthropic             claude-opus-5-5    1M       128K     yes       yes
google-vertex-claude  claude-opus-5-5    500K     128K     yes       yes
anthropic             claude-haiku-4-5   200K     64K      yes       yes
LIST
SCRIPT
  chmod +x "$BIN/pi"
  PATH="$BIN:$PATH"
}

@test "each model wider than the ceiling gets the reserve that compacts it there" {
  _seed_config 'context:' '  compact_at: 200000'
  _write_live '{"defaultModel":"claude-opus-5-5"}'
  _stub_pi_list_models

  run _run_step step_pi_compaction
  [ "$status" -eq 0 ]
  run jq -c '.compaction.modelOverrides' "$LIVE"
  [ "$output" = '{"anthropic/claude-opus-5-5":{"reserveTokens":800000},"google-vertex-claude/claude-opus-5-5":{"reserveTokens":300000}}' ]
  run jq -r '.defaultModel' "$LIVE"
  [ "$output" = "claude-opus-5-5" ]
}

@test "a model at or under the ceiling loses the reserve an earlier ceiling gave it" {
  # Raising the ceiling past a window must not leave the old, lower one behind.
  _seed_config 'context:' '  compact_at: 200000'
  _write_live '{"compaction":{"reserveTokens":16384,"modelOverrides":{"anthropic/claude-haiku-4-5":{"reserveTokens":150000,"keepRecentTokens":10000},"google-vertex-claude/claude-opus-5-5":{"reserveTokens":1}}}}'
  _stub_pi_list_models

  run _run_step step_pi_compaction
  [ "$status" -eq 0 ]
  run jq -c '.compaction.modelOverrides["anthropic/claude-haiku-4-5"]' "$LIVE"
  [ "$output" = '{"keepRecentTokens":10000}' ]
  run jq -r '.compaction.modelOverrides["google-vertex-claude/claude-opus-5-5"].reserveTokens' "$LIVE"
  [ "$output" = "300000" ]
  run jq -r '.compaction.reserveTokens' "$LIVE"
  [ "$output" = "16384" ]
}

@test "an entry left empty is removed, and the section with it" {
  _seed_config 'context:' '  compact_at: 1000000'
  _write_live '{"compaction":{"modelOverrides":{"anthropic/claude-opus-5-5":{"reserveTokens":800000}}}}'
  _stub_pi_list_models

  run _run_step step_pi_compaction
  [ "$status" -eq 0 ]
  run jq -c 'has("compaction")' "$LIVE"
  [ "$output" = "false" ]
}

@test "a model the catalogue does not list keeps its entry" {
  _seed_config 'context:' '  compact_at: 200000'
  _write_live '{"compaction":{"modelOverrides":{"openai/gpt-6":{"reserveTokens":5}}}}'
  _stub_pi_list_models

  run _run_step step_pi_compaction
  [ "$status" -eq 0 ]
  run jq -r '.compaction.modelOverrides["openai/gpt-6"].reserveTokens' "$LIVE"
  [ "$output" = "5" ]
}

@test "with no ceiling the settings file is left exactly as it was" {
  _write_live '{"compaction":{"modelOverrides":{"anthropic/claude-opus-5-5":{"reserveTokens":800000}}}}'
  cp "$LIVE" "$TMPDIR/before.json"
  _stub_pi_list_models

  run _run_step step_pi_compaction
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  cmp "$TMPDIR/before.json" "$LIVE"
}

@test "a catalogue pi cannot list is reported rather than read as no models to cap" {
  _seed_config 'context:' '  compact_at: 200000'
  _write_live '{"defaultModel":"claude-opus-5-5"}'
  cp "$LIVE" "$TMPDIR/before.json"

  run _run_step step_pi_compaction
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN Pi lists no models, so context.compact_at was not applied"* ]]
  cmp "$TMPDIR/before.json" "$LIVE"
}

@test "an unreadable config warns and leaves the settings file alone" {
  _seed_config 'context:' '  compact_at: 0'
  _write_live '{"defaultModel":"claude-opus-5-5"}'
  cp "$LIVE" "$TMPDIR/before.json"
  _stub_pi_list_models

  run _run_step step_pi_compaction
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN Pi's compaction settings left as they were"* ]]
  cmp "$TMPDIR/before.json" "$LIVE"
}

@test "a second sync changes nothing" {
  _seed_config 'context:' '  compact_at: 200000'
  _write_live '{}'
  _stub_pi_list_models
  run _run_step step_pi_compaction
  cp "$LIVE" "$TMPDIR/first.json"

  run _run_step step_pi_compaction
  [ "$status" -eq 0 ]
  cmp "$TMPDIR/first.json" "$LIVE"
}
