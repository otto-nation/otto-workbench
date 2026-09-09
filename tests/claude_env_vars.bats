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

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 2 ]]
  [[ "${sources[0]}" == "FIRST_VAR" ]]
  [[ "${sources[1]}" == "SECOND_VAR" ]]
  # No target: field — targets default to source names
  [[ "${targets[0]}" == "FIRST_VAR" ]]
  [[ "${targets[1]}" == "SECOND_VAR" ]]
}

@test "target: field maps source to a different output name" {
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: true
env:
  - var: AI_MODEL
    target: ANTHROPIC_MODEL
  - var: PLAIN_VAR
tools: []'

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 2 ]]
  [[ "${sources[0]}" == "AI_MODEL" ]]
  [[ "${targets[0]}" == "ANTHROPIC_MODEL" ]]
  # No target — defaults to source
  [[ "${sources[1]}" == "PLAIN_VAR" ]]
  [[ "${targets[1]}" == "PLAIN_VAR" ]]
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

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 0 ]]
}

@test "claude_env: false contributes nothing" {
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: false
env:
  - var: SOME_API_TOKEN
tools: []'

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 0 ]]
}

@test "an entry with claude_env: false is held back from a flagged registry" {
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: true
env:
  - var: MIRRORED_VAR
  - var: HELD_BACK_VAR
    claude_env: false
  - var: ALSO_MIRRORED_VAR
tools: []'

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 2 ]]
  # Absent from both arrays: a name left in targets would still be swept out of
  # settings.json as a managed key.
  run bash -c 'printf "%s\n" "$@" | grep -qx HELD_BACK_VAR' _ "${sources[@]}" "${targets[@]}"
  [ "$status" -ne 0 ]
  # Its siblings are untouched — the opt-out is per entry, not a file-level veto.
  printf '%s\n' "${sources[@]}" | grep -qx MIRRORED_VAR
  printf '%s\n' "${sources[@]}" | grep -qx ALSO_MIRRORED_VAR
}

@test "an entry saying nothing about claude_env is mirrored" {
  # The entry-level field is an opt-out, so a variable is never withheld by an
  # omission — the audience question is answered once, by the registry flag.
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: true
env:
  - var: QUIET_VAR
tools: []'

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 1 ]]
  [[ "${sources[0]}" == "QUIET_VAR" ]]
}

@test "claude_env: false wins over a target on the same entry" {
  # A target names where a variable would land in settings.json. On an entry
  # that never gets there it decides nothing, and must not resurrect it.
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: true
env:
  - var: HELD_BACK_VAR
    target: SOME_OTHER_NAME
tools: []'

  local -a before=() before_targets=()
  collect_claude_env_vars before before_targets "$TMPDIR"
  [[ "${#before[@]}" -eq 1 ]]

  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: true
env:
  - var: HELD_BACK_VAR
    target: SOME_OTHER_NAME
    claude_env: false
tools: []'

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 0 ]]
  [[ "${#targets[@]}" -eq 0 ]]
}

@test "a flagged registry with no env block is skipped" {
  _write_env_registry "$TMPDIR/comp" 'meta:
  section: Test
  validation: none
  claude_env: true
tools: []'

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 0 ]]
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

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$TMPDIR"
  [[ "${#sources[@]}" -eq 2 ]]
  printf '%s\n' "${sources[@]}" | grep -qx ONE_VAR
  printf '%s\n' "${sources[@]}" | grep -qx TWO_VAR
}

# ── the real registries ──────────────────────────────────────────────────────

@test "the Vertex routing vars are on the allowlist" {
  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$REPO_ROOT"
  printf '%s\n' "${sources[@]}" | grep -qx CLAUDE_CODE_USE_VERTEX
  printf '%s\n' "${sources[@]}" | grep -qx GOOGLE_CLOUD_PROJECT
  printf '%s\n' "${sources[@]}" | grep -qx CLOUD_ML_REGION
}

@test "the Vertex project id maps to Claude Code's own name" {
  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$REPO_ROOT"
  printf '%s\n' "${targets[@]}" | grep -qx ANTHROPIC_VERTEX_PROJECT_ID
}

@test "the Google location never reaches the allowlist" {
  # GOOGLE_CLOUD_LOCATION sits in the flagged Vertex registry alongside the vars
  # that are mirrored, held back by `claude_env: false` on its own entry, for two
  # reasons: Claude Code reads CLOUD_ML_REGION and has no use for it, and a value
  # mirrored into settings.json cannot be overridden from a shell afterwards —
  # which would foreclose the divergence the entry exists to allow.
  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$REPO_ROOT"
  run bash -c 'printf "%s\n" "$@" | grep -qx GOOGLE_CLOUD_LOCATION' _ "${sources[@]}" "${targets[@]}"
  [ "$status" -ne 0 ]
}

@test "the model routing vars use generic AI_* names" {
  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$REPO_ROOT"
  # Source names (what ~/.env.local carries)
  printf '%s\n' "${sources[@]}" | grep -qx AI_MODEL
  printf '%s\n' "${sources[@]}" | grep -qx AI_OPUS_MODEL
  printf '%s\n' "${sources[@]}" | grep -qx AI_SONNET_MODEL
  printf '%s\n' "${sources[@]}" | grep -qx AI_HAIKU_MODEL
}

@test "model vars map to Claude Code's ANTHROPIC_* target names" {
  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$REPO_ROOT"
  printf '%s\n' "${targets[@]}" | grep -qx ANTHROPIC_MODEL
  printf '%s\n' "${targets[@]}" | grep -qx ANTHROPIC_DEFAULT_OPUS_MODEL
  printf '%s\n' "${targets[@]}" | grep -qx ANTHROPIC_DEFAULT_SONNET_MODEL
  printf '%s\n' "${targets[@]}" | grep -qx ANTHROPIC_DEFAULT_HAIKU_MODEL
}

@test "no credential the registries declare reaches the allowlist" {
  # Every var an unflagged registry declares, checked against the allowlist as a
  # set — a registry that gains the flag by mistake fails here rather than in a
  # settings file someone reads a token out of.
  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$REPO_ROOT"
  run bash -c 'printf "%s\n" "$@" | grep -Ex "(JIRA_API_TOKEN|LINEAR_API_KEY|CONTEXT7_API_KEY|AWS_PROFILE)"' _ "${sources[@]}"
  [ "$status" -ne 0 ]
}
