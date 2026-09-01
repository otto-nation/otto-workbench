#!/usr/bin/env bats
# Tests for the migration draining Pi's old spliced-skill directory.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  MIGRATION="$REPO_ROOT/ai/pi/migrations/20260901-drop-spliced-pi-skills.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# The framework hands a migration lib/ui.sh for its output helpers and
# lib/migrations.sh for MIGRATION_NOOP; both are sourced here for the same reason.
_run_migration() {
  WORKBENCH_DIR="$REPO_ROOT" run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    . '$MIGRATION'
    migration_20260901_drop_spliced_pi_skills
  "
}

@test "removes the spliced copy so Pi sees one reviewer skill" {
  mkdir -p "$HOME/.pi/agent/skills/reviewer"
  echo "spliced" > "$HOME/.pi/agent/skills/reviewer/SKILL.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.pi/agent/skills/reviewer" ]
}

@test "leaves a hand-authored Pi skill alone" {
  mkdir -p "$HOME/.pi/agent/skills/mine"
  echo "mine" > "$HOME/.pi/agent/skills/mine/SKILL.md"

  _run_migration
  [ -f "$HOME/.pi/agent/skills/mine/SKILL.md" ]
}

@test "is a no-op once the copy is gone" {
  mkdir -p "$HOME/.pi/agent/skills"

  _run_migration
  [ "$status" -eq 3 ]
}
