#!/usr/bin/env bats
# Tests for step_pi_models in ai/pi/steps.sh — checking the model ids
# ~/.env.local names against the catalog the user's own pi serves.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  load 'pi_settings_helper'
  pi_settings_setup
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

@test "an unknown model warns with the variable name" {
  _seed_env_local 'export AI_MODEL=not-a-real-model'
  _stub_gh 'echo "{}"'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"AI_MODEL=not-a-real-model"* ]]
  [[ "$output" == *"google-vertex-claude"* ]]
  [[ "$output" == *"~/.env.local"* ]]
  _teardown_env_local
}

@test "every listed model is silent" {
  _seed_env_local 'export AI_MODEL=claude-sonnet-5'
  _stub_gh 'echo "{}"'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"is not listed under"* ]]
  _teardown_env_local
}

@test "pi absent is silent and the step still succeeds" {
  _seed_env_local 'export AI_MODEL=not-a-real-model'
  _stub_gh 'echo "{}"'
  _hide_pi

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"is not listed under"* ]]
  _teardown_env_local
}

@test "pi --list-models failing is silent and the step still succeeds" {
  _seed_env_local 'export AI_MODEL=not-a-real-model'
  _stub_gh 'echo "{}"'
  # setup already shadows pi with a failing binary.

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"is not listed under"* ]]
  _teardown_env_local
}

@test "a model listed only under a different provider still warns" {
  _seed_env_local 'export AI_MODEL=gemini-2.5-flash'
  _stub_gh 'echo "{}"'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"AI_MODEL=gemini-2.5-flash"* ]]
  [[ "$output" == *"google-vertex-claude"* ]]
  _teardown_env_local
}

@test "a substring of a listed id is not treated as listed" {
  _seed_env_local 'export AI_MODEL=claude-opus-5'
  _stub_gh 'echo "{}"'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"AI_MODEL=claude-opus-5"* ]]
  [[ "$output" == *"is not listed under google-vertex-claude"* ]]
  _teardown_env_local
}

@test "a provider with no rows warns once that it did not load, not per variable" {
  # The Vertex extension registers nothing when the shell lacks a project or
  # ADC. Every correct id then reads as unknown; one line naming the provider
  # is the truth, a line per variable blames ~/.env.local for it.
  _seed_env_local 'export AI_MODEL=claude-opus-5-5
export AI_SONNET_MODEL=claude-sonnet-5'
  _stub_gh 'echo "{}"'
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
  _teardown_env_local
}

@test "a non-Vertex provider with no rows does not get Vertex advice" {
  _write_template '[]'
  jq '.defaultProvider = "anthropic"' "$TEMPLATE" > "$TEMPLATE.new" && mv "$TEMPLATE.new" "$TEMPLATE"
  _seed_env_local 'export AI_MODEL=claude-opus-5-5'
  _stub_gh 'echo "{}"'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" == *"Pi lists no models under anthropic"* ]]
  [[ "$output" != *"GOOGLE_CLOUD_PROJECT"* ]]
  [[ "$output" != *"ADC"* ]]
  _teardown_env_local
}

@test "a provider listed only in part warns per variable, not that it did not load" {
  # Rows exist for the provider, so it loaded: the unlisted id is the user's
  # to fix and the listed one stays quiet.
  _seed_env_local 'export AI_MODEL=claude-opus-5
export AI_SONNET_MODEL=claude-sonnet-5'
  _stub_gh 'echo "{}"'
  _stub_pi_list_models

  run _run_step step_pi_models
  [ "$status" -eq 0 ]
  [[ "$output" != *"did not load"* ]]
  [[ "$output" == *"AI_MODEL=claude-opus-5 is not listed under google-vertex-claude"* ]]
  [[ "$output" != *"AI_SONNET_MODEL"* ]]
  [ "$(grep -c '^WARN' <<< "$output")" -eq 1 ]
  _teardown_env_local
}

@test "the catalog comes from the user pi asked from HOME, not from the cwd" {
  # A repo pinning an older pi (or loading its own .pi packages) serves a
  # different catalog. The stub stands in for that: from anywhere but HOME it
  # omits the model the user's pi does list.
  mkdir -p "$TMPDIR/home" "$TMPDIR/project"
  _seed_env_local 'export AI_MODEL=claude-opus-5-5'
  _stub_gh 'echo "{}"'
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
  _teardown_env_local
}

@test "step_pi_settings no longer asks pi for its catalog" {
  # The catalog comes from extension clones that step_pi_packages refreshes
  # later in the same sync; asked here, a clone about to be repaired reads as
  # every model unknown.
  _seed_env_local 'export AI_MODEL=not-a-real-model'
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
  _teardown_env_local
}
