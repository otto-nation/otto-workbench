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
  [ "$status" -eq 3 ]
  [ -f "$HOME/.pi/agent/skills/mine/SKILL.md" ]
}

# The only path where the migration both removes something and finds the parent
# still occupied, so the rmdir it ends on has to fail harmlessly. Unguarded, that
# non-zero return would be the function's own — reported as a failed migration
# against work that in fact completed.
@test "removes the spliced copy without disturbing a hand-authored neighbour" {
  mkdir -p "$HOME/.pi/agent/skills/reviewer" "$HOME/.pi/agent/skills/mine"
  echo "spliced" > "$HOME/.pi/agent/skills/reviewer/SKILL.md"
  echo "mine" > "$HOME/.pi/agent/skills/mine/SKILL.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.pi/agent/skills/reviewer" ]
  [ -f "$HOME/.pi/agent/skills/mine/SKILL.md" ]
}

@test "is a no-op once the copy is gone" {
  mkdir -p "$HOME/.pi/agent/skills"

  _run_migration
  [ "$status" -eq 3 ]
}
