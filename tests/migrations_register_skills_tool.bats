#!/usr/bin/env bats
# Tests for the migration that registers skills as an AI sub-tool.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  mkdir -p "$WORKBENCH_STATE_DIR"
  STATE_FILE="$WORKBENCH_STATE_DIR/install.yml"
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260901-register-skills-tool.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _seed_tools TOOL... — writes an install.yml whose ai.tools list holds TOOL...
# With no arguments it writes the list empty.
_seed_tools() {
  printf 'components:\n  ai:\n    tools: []\n' > "$STATE_FILE"
  local tool
  for tool in "$@"; do
    v="$tool" yq -i '.components.ai.tools += [strenv(v)]' "$STATE_FILE"
  done
}

# _run_migration — sources the migration the way lib/migrations.sh does and
# calls its function, returning its exit status. lib/migrations.sh is sourced
# too, not just lib/ui.sh — it is where MIGRATION_NOOP is defined.
_run_migration() {
  WORKBENCH_DIR="$REPO_ROOT" run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    . '$MIGRATION'
    migration_20260901_register_skills_tool
  "
}

# _tools — prints the recorded ai.tools list, one entry per line.
_tools() {
  yq '.components.ai.tools | (. // []) | .[]' "$STATE_FILE"
}

@test "registers skills on a machine that already selected AI tools" {
  _seed_tools pi

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(_tools)" = "$(printf 'pi\nskills')" ]
}

@test "leaves the tools already recorded in place" {
  _seed_tools claude serena

  _run_migration
  [ "$status" -eq 0 ]
  _tools | grep -qx claude
  _tools | grep -qx serena
  _tools | grep -qx skills
}

@test "is a no-op when skills is already registered" {
  _seed_tools pi skills

  _run_migration
  [ "$status" -eq 3 ]
}

@test "is a no-op when no AI tools have ever been selected" {
  _run_migration
  [ "$status" -eq 3 ]
  [ ! -f "$STATE_FILE" ]
}

@test "is a no-op when the tools list exists but is empty" {
  _seed_tools

  _run_migration
  [ "$status" -eq 3 ]
  [ "$(_tools)" = "" ]
}

@test "is idempotent — a second run reports no work" {
  _seed_tools pi

  _run_migration
  [ "$status" -eq 0 ]
  _run_migration
  [ "$status" -eq 3 ]
}
