#!/usr/bin/env bats
# Tests for the migration that drains the machine-local rule layers out of
# Claude Code's installed rules directory.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  INSTALLED="$HOME/.claude/rules"
  OVERRIDE_RULES="$WORKBENCH_CONFIG_DIR/overrides/ai/guidelines/rules"
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260903-local-rules-to-overrides.sh"
  mkdir -p "$INSTALLED"
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
    migration_20260903_local_rules_to_overrides
  "
}

@test "moves a machine-local rule into the override layer" {
  echo "- run pytest bare" > "$INSTALLED/testing.local.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$OVERRIDE_RULES/testing.local.md")" = "- run pytest bare" ]
  [ ! -e "$INSTALLED/testing.local.md" ]
}

@test "drops the generated workbench.md rather than moving it" {
  # It is rewritten under the state root on every sync, so the copy here is
  # output nothing needs to carry forward.
  echo "# Workbench" > "$INSTALLED/workbench.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$INSTALLED/workbench.md" ]
  [ ! -e "$OVERRIDE_RULES/workbench.md" ]
}

@test "leaves the installed symlinks alone" {
  echo "# General" > "$TMPDIR/general.md"
  ln -s "$TMPDIR/general.md" "$INSTALLED/general.md"
  echo "- local" > "$INSTALLED/go.local.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ -L "$INSTALLED/general.md" ]
}

@test "leaves a *.local.md that is already a symlink where it is" {
  # Such a link points at a layer source, so it is an install rather than the
  # hand-written layer this migration exists to rescue.
  echo "- installed" > "$TMPDIR/go.local.md"
  ln -s "$TMPDIR/go.local.md" "$INSTALLED/go.local.md"

  _run_migration
  [ "$status" -eq 3 ]
  [ -L "$INSTALLED/go.local.md" ]
  [ ! -e "$OVERRIDE_RULES/go.local.md" ]
}

@test "refuses to clobber an override of the same name" {
  echo "- old" > "$INSTALLED/go.local.md"
  mkdir -p "$OVERRIDE_RULES"
  echo "- new" > "$OVERRIDE_RULES/go.local.md"

  _run_migration
  [ "$status" -eq 1 ]
  [ "$(cat "$OVERRIDE_RULES/go.local.md")" = "- new" ]
}

@test "is a no-op when there is no rules directory at all" {
  rm -rf "$HOME/.claude"

  _run_migration
  [ "$status" -eq 3 ]
}

@test "is idempotent — a second run reports no work" {
  echo "- run pytest bare" > "$INSTALLED/testing.local.md"

  _run_migration
  [ "$status" -eq 0 ]
  _run_migration
  [ "$status" -eq 3 ]
}
