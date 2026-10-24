#!/usr/bin/env bats
# Tests for step_pi_models in ai/pi/steps.sh — checking the models Pi's settings
# name against the catalog the user's own pi serves.
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

# ── unknown-model warning against `pi --list-models` ──────────────────────────

# Rows copied from a real `pi --list-models`. claude-opus-5 is deliberately
# absent so a substring of claude-opus-5-5 is not treated as listed.
_stub_pi_list_models() {
  cat > "$BIN/pi" << 'SCRIPT'
#!/usr/bin/env bash
[[ "$1" == "--list-models" ]] || exit 1
cat << 'LIST'
provider              model                               context  max-out  thinking  images
google-vertex         gemini-2.5-flash                    1.0M     65.5K    yes       yes
google-vertex-claude  claude-opus-5-5                     1M       128K     yes       yes
google-vertex-claude  claude-sonnet-5                     1M       128K     yes       yes
google-vertex-grok    xai/grok-4.6                        120K     32K      yes       no
LIST
SCRIPT
  chmod +x "$BIN/pi"
  PATH="$BIN:$PATH"
}

# _vertex_default ID — live settings defaulting to ID on google-vertex-claude.
_vertex_default() {
  _write_live "$(jq -nc --arg m "$1" '{defaultProvider: "google-vertex-claude", defaultModel: $m}')"
}

@test "an unknown defaultModel warns, naming the provider and models.*" {
  _vertex_default not-a-real-model
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"not-a-real-model is not listed under google-vertex-claude"* ]]
  [[ "$output" == *"models.* in config.yml"* ]]
}

@test "a stale defaultModel Pi kept on its own is still reported" {
  # With models.session unset the sync leaves Pi's own defaultModel in place,
  # and a retired id there fails every prompt the same way a wrong config would.
  _vertex_default claude-opus-4-8
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"claude-opus-4-8 is not listed under google-vertex-claude"* ]]
}

@test "every listed model is silent" {
  _write_live '{"defaultProvider": "google-vertex-claude", "defaultModel": "claude-sonnet-5", "enabledModels": ["google-vertex/gemini-2.5-flash", "claude-opus-5-5"]}'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"WARN"* ]]
}

@test "pi absent is silent and the step still succeeds" {
  _vertex_default not-a-real-model
  _hide_pi

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"is not listed under"* ]]
}

@test "pi --list-models failing is silent and the step still succeeds" {
  _vertex_default not-a-real-model
  # setup already shadows pi with a failing binary.

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"is not listed under"* ]]
}

@test "no settings file is silent" {
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"WARN"* ]]
}

@test "a model listed only under a different provider still warns" {
  _vertex_default gemini-2.5-flash
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"gemini-2.5-flash is not listed under google-vertex-claude"* ]]
}

@test "a provider/id entry is checked under its own provider" {
  _write_live '{"defaultProvider": "google-vertex-claude", "defaultModel": "claude-opus-5-5", "enabledModels": ["google-vertex/claude-opus-5-5"]}'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"claude-opus-5-5 is not listed under google-vertex —"* ]]
  [ "$(grep -c '^WARN' <<< "$output")" -eq 1 ]
}

@test "with no defaultProvider a bare id is checked across every provider" {
  _write_live '{"defaultModel": "gemini-2.5-flash", "enabledModels": ["not-a-real-model"]}'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pi lists no model not-a-real-model under any provider"* ]]
  [[ "$output" != *"gemini-2.5-flash"* ]]
}

@test "a substring of a listed id is not treated as listed" {
  _vertex_default claude-opus-5
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"claude-opus-5 is not listed under google-vertex-claude"* ]]
}

@test "a provider with no rows warns once that it did not load, not per model" {
  # The Vertex extension registers nothing when the shell lacks a project or
  # ADC. Every correct id then reads as unknown; one line naming the provider
  # is the truth, a line per model blames the config for it.
  _write_live '{"defaultProvider": "google-vertex-claude", "defaultModel": "claude-opus-5-5", "enabledModels": ["google-vertex-claude/claude-sonnet-5"]}'
  cat > "$BIN/pi" << 'SCRIPT'
#!/usr/bin/env bash
[[ "$1" == "--list-models" ]] || exit 1
printf 'provider       model             context\n'
printf 'google-vertex  gemini-2.5-flash  1.0M\n'
SCRIPT
  chmod +x "$BIN/pi"

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pi lists no models under google-vertex-claude"* ]]
  [[ "$output" == *"GOOGLE_CLOUD_PROJECT"* ]]
  [[ "$output" != *"is not listed under"* ]]
  [ "$(grep -c '^WARN' <<< "$output")" -eq 1 ]
}

@test "a non-Vertex provider with no rows does not get Vertex advice" {
  _write_live '{"defaultProvider": "anthropic", "defaultModel": "claude-opus-5-5"}'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pi lists no models under anthropic"* ]]
  [[ "$output" != *"GOOGLE_CLOUD_PROJECT"* ]]
  [[ "$output" != *"ADC"* ]]
}

@test "a provider listed only in part warns per model, not that it did not load" {
  # Rows exist for the provider, so it loaded: the unlisted id is the user's
  # to fix and the listed one stays quiet.
  _write_live '{"defaultProvider": "google-vertex-claude", "defaultModel": "claude-opus-5", "enabledModels": ["google-vertex-claude/claude-sonnet-5"]}'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"did not load"* ]]
  [[ "$output" == *"claude-opus-5 is not listed under google-vertex-claude"* ]]
  [[ "$output" != *"claude-sonnet-5"* ]]
  [ "$(grep -c '^WARN' <<< "$output")" -eq 1 ]
}

@test "the catalog comes from the user pi asked from HOME, not from the cwd" {
  # A repo pinning an older pi (or loading its own .pi packages) serves a
  # different catalog. The stub stands in for that: from anywhere but HOME it
  # omits the model the user's pi does list.
  mkdir -p "$TMPDIR/home" "$TMPDIR/project"
  _vertex_default claude-opus-5-5
  cat > "$BIN/pi" << SCRIPT
#!/usr/bin/env bash
[[ "\$1" == "--list-models" ]] || exit 1
echo 'provider              model'
echo 'google-vertex-claude  claude-sonnet-5'
[[ "\$PWD" == "$TMPDIR/home" ]] && echo 'google-vertex-claude  claude-opus-5-5'
exit 0
SCRIPT
  chmod +x "$BIN/pi"

  cd "$TMPDIR/project"
  HOME="$TMPDIR/home" run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"is not listed under"* ]]
}

@test "step_pi_settings does not ask pi for its catalog" {
  # The catalog comes from extension clones that step_pi_packages refreshes
  # later in the same sync; asked here, a clone about to be repaired reads as
  # every model unknown.
  _seed_config 'models:' '  session: google-vertex-claude/not-a-real-model'
  _stub_gh 'echo "{}"'
  _stub_pi_list_models
  # Record every invocation so the claim is pinned directly: a stub that is
  # never reached would leave the output assertion passing for any reason.
  mv "$BIN/pi" "$BIN/pi.real"
  cat > "$BIN/pi" << SCRIPT
#!/usr/bin/env bash
printf '%s\\n' "\$*" >> "$TMPDIR/pi-argv"
exec "$BIN/pi.real" "\$@"
SCRIPT
  chmod +x "$BIN/pi"

  run _run_step
  [ "$status" -eq 0 ]
  [[ "$output" != *"is not listed under"* ]]
  [ ! -e "$TMPDIR/pi-argv" ]
}
