#!/usr/bin/env bats
# Tests for state-gated sync in otto-workbench.
# Validates the building blocks that cmd_sync() uses to gate on installation state:
# path derivation, state checks, and infrastructure list matching.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: source libs with fake HOME so state file paths resolve to TMPDIR
_source_with_fake_home() {
  HOME="$FAKE_HOME"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"
}

# ─── State gating primitives ────────────────────────────────────────────────

@test "sync gates on state file: skips uninstalled component" {
  _source_with_fake_home

  state_record "ai"

  run state_file_exists
  [[ "$status" -eq 0 ]]

  run state_is_installed "docker"
  [[ "$status" -ne 0 ]]
}

@test "sync runs installed component" {
  _source_with_fake_home

  state_record "ai"

  run state_is_installed "ai"
  [[ "$status" -eq 0 ]]
}

@test "backward compat: no state file means state_file_exists returns false" {
  _source_with_fake_home

  # Do NOT create state file
  run state_file_exists
  [[ "$status" -ne 0 ]]
}

# ─── Infrastructure always-sync list ────────────────────────────────────────

@test "infrastructure components match always-sync list" {
  # Source the real constant — CORE_COMPONENTS is the SSOT for always-synced components
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/constants.sh"

  # Infrastructure entries match
  [[ " $CORE_COMPONENTS " == *" bin "* ]]
  [[ " $CORE_COMPONENTS " == *" task "* ]]
  [[ " $CORE_COMPONENTS " == *" git "* ]]
  [[ " $CORE_COMPONENTS " == *" zsh "* ]]

  # Non-infrastructure entries do not match
  [[ " $CORE_COMPONENTS " != *" docker "* ]]
  [[ " $CORE_COMPONENTS " != *" mise "* ]]
  [[ " $CORE_COMPONENTS " != *" ai "* ]]
  [[ " $CORE_COMPONENTS " != *" ai/claude "* ]]
}

# ─── Component path derivation ──────────────────────────────────────────────

@test "path derivation: top-level component resolves correctly" {
  local WORKBENCH_DIR="$REPO_ROOT"
  local _f="$WORKBENCH_DIR/bin/steps.sh"
  local _path

  _path="${_f#"$WORKBENCH_DIR/"}"
  _path="${_path%/steps.sh}"

  [[ "$_path" == "bin" ]]
}

@test "path derivation: nested component resolves correctly" {
  local WORKBENCH_DIR="$REPO_ROOT"
  local _f="$WORKBENCH_DIR/ai/claude/steps.sh"
  local _path

  _path="${_f#"$WORKBENCH_DIR/"}"
  _path="${_path%/steps.sh}"

  [[ "$_path" == "ai/claude" ]]
}

@test "path derivation: all step files produce expected paths" {
  local WORKBENCH_DIR="$REPO_ROOT"
  local -a _step_files=()
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/components.sh"
  discover_step_files _step_files

  local _f _path
  for _f in "${_step_files[@]}"; do
    _path="${_f#"$WORKBENCH_DIR/"}"
    _path="${_path%/steps.sh}"

    # Path must not start or end with /
    [[ "$_path" != /* ]]
    [[ "$_path" != */ ]]
    # Path must not contain steps.sh
    [[ "$_path" != *steps.sh* ]]
    # Path must be non-empty
    [[ -n "$_path" ]]
  done
}

# ─── Sub-tool state entries ──────────────────────────────────────────────────

@test "state file with sub-tool entries works independently" {
  _source_with_fake_home

  state_record "ai"
  state_record "ai/claude"

  run state_is_installed "ai"
  [[ "$status" -eq 0 ]]

  run state_is_installed "ai/claude"
  [[ "$status" -eq 0 ]]

  run state_is_installed "ai/serena"
  [[ "$status" -ne 0 ]]
}
