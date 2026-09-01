#!/usr/bin/env bats
# Tests for the skills user-override root migration.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260901-skills-override-root.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _run_migration — sources the migration the way lib/migrations.sh does and
# calls its function, returning its exit status. lib/migrations.sh is sourced
# too, not just lib/ui.sh — it is where MIGRATION_NOOP is defined.
_run_migration() {
  WORKBENCH_DIR="$REPO_ROOT" run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    . '$REPO_ROOT/lib/migrations.sh'
    . '$MIGRATION'
    migration_20260901_skills_override_root
  "
}

@test "moves an existing override into the harness-neutral root" {
  mkdir -p "$TMPDIR/config/overrides/ai/claude/skills/anatomy"
  echo "custom" > "$TMPDIR/config/overrides/ai/claude/skills/anatomy/SKILL.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/config/overrides/ai/skills/anatomy/SKILL.md" ]
  [ "$(cat "$TMPDIR/config/overrides/ai/skills/anatomy/SKILL.md")" = "custom" ]
  [ ! -d "$TMPDIR/config/overrides/ai/claude/skills" ]
}

@test "is a no-op when the old root is absent" {
  mkdir -p "$TMPDIR/config/overrides/ai"

  _run_migration
  [ "$status" -eq 3 ]
}

@test "refuses to clobber an override already at the new root" {
  mkdir -p "$TMPDIR/config/overrides/ai/claude/skills/anatomy"
  mkdir -p "$TMPDIR/config/overrides/ai/skills/anatomy"
  echo "old" > "$TMPDIR/config/overrides/ai/claude/skills/anatomy/SKILL.md"
  echo "new" > "$TMPDIR/config/overrides/ai/skills/anatomy/SKILL.md"

  _run_migration
  [ "$status" -eq 1 ]
  [ "$(cat "$TMPDIR/config/overrides/ai/skills/anatomy/SKILL.md")" = "new" ]
}

@test "is idempotent — a second run reports no work" {
  mkdir -p "$TMPDIR/config/overrides/ai/claude/skills/anatomy"
  echo "custom" > "$TMPDIR/config/overrides/ai/claude/skills/anatomy/SKILL.md"

  _run_migration
  [ "$status" -eq 0 ]
  _run_migration
  [ "$status" -eq 3 ]
}
