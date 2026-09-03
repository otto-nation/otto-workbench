#!/usr/bin/env bats
# Tests for collect_claude_env_vars — the allowlist of env vars that
# ai/claude/steps.sh mirrors from ~/.env.local into ~/.claude/settings.json.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"

  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/registries.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

_write_env_registry() {
  local dir="$1" content="$2"
  mkdir -p "$dir"
  printf '%s\n' "$content" > "$dir/thing.env.yml"
}

@test "claude_env: true collects every var the registry declares" {
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: true
env:
  - var: FIRST_VAR
    comment: "first"
  - var: SECOND_VAR
tools: []'

  local -a vars=()
  collect_claude_env_vars vars "$TMPDIR"
  [[ "${#vars[@]}" -eq 2 ]]
  [[ "${vars[0]}" == "FIRST_VAR" ]]
  [[ "${vars[1]}" == "SECOND_VAR" ]]
}

@test "an unflagged registry contributes nothing" {
  # The default matters more than the flag does: ~/.env.local holds API tokens,
  # and ~/.claude/settings.json is written world-readable.
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
env:
  - var: SOME_API_TOKEN
tools: []'

  local -a vars=()
  collect_claude_env_vars vars "$TMPDIR"
  [[ "${#vars[@]}" -eq 0 ]]
}

@test "claude_env: false contributes nothing" {
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: false
env:
  - var: SOME_API_TOKEN
tools: []'

  local -a vars=()
  collect_claude_env_vars vars "$TMPDIR"
  [[ "${#vars[@]}" -eq 0 ]]
}

@test "a flagged registry with no env block is skipped" {
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: true
tools: []'

  local -a vars=()
  collect_claude_env_vars vars "$TMPDIR"
  [[ "${#vars[@]}" -eq 0 ]]
}

@test "vars from several flagged registries are all collected" {
  _write_env_registry "$TMPDIR/one" 'meta:
  section: One
  validation: none
  claude_env: true
env:
  - var: ONE_VAR
tools: []'
  _write_env_registry "$TMPDIR/two" 'meta:
  section: Two
  validation: none
  claude_env: true
env:
  - var: TWO_VAR
tools: []'

  local -a vars=()
  collect_claude_env_vars vars "$TMPDIR"
  [[ "${#vars[@]}" -eq 2 ]]
  printf '%s\n' "${vars[@]}" | grep -qx ONE_VAR
  printf '%s\n' "${vars[@]}" | grep -qx TWO_VAR
}

# ── the real registries ──────────────────────────────────────────────────────

@test "the Vertex routing vars are on the allowlist" {
  local -a vars=()
  collect_claude_env_vars vars "$REPO_ROOT"
  printf '%s\n' "${vars[@]}" | grep -qx CLAUDE_CODE_USE_VERTEX
  printf '%s\n' "${vars[@]}" | grep -qx ANTHROPIC_VERTEX_PROJECT_ID
  printf '%s\n' "${vars[@]}" | grep -qx CLOUD_ML_REGION
}

@test "the model routing vars are on the allowlist" {
  local -a vars=()
  collect_claude_env_vars vars "$REPO_ROOT"
  printf '%s\n' "${vars[@]}" | grep -qx ANTHROPIC_MODEL
  printf '%s\n' "${vars[@]}" | grep -qx ANTHROPIC_DEFAULT_OPUS_MODEL
  printf '%s\n' "${vars[@]}" | grep -qx ANTHROPIC_DEFAULT_SONNET_MODEL
  printf '%s\n' "${vars[@]}" | grep -qx ANTHROPIC_DEFAULT_HAIKU_MODEL
}

@test "no credential the registries declare reaches the allowlist" {
  # Every var an unflagged registry declares, checked against the allowlist as a
  # set — a registry that gains the flag by mistake fails here rather than in a
  # settings file someone reads a token out of.
  local -a vars=()
  collect_claude_env_vars vars "$REPO_ROOT"
  run bash -c 'printf "%s\n" "$@" | grep -Ex "(JIRA_API_TOKEN|LINEAR_API_KEY|CONTEXT7_API_KEY|AWS_PROFILE)"' _ "${vars[@]}"
  [ "$status" -ne 0 ]
}
