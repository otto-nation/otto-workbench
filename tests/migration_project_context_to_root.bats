#!/usr/bin/env bats
# Tests for the migration moving a project's context file to the repo root.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  REPO="$TMPDIR/repo"
  mkdir -p "$REPO/.claude"
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260902-project-context-to-root.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# The framework passes the work tree path as the migration's only argument.
_run_migration() {
  run bash -c '
    WORKBENCH_DIR="$1"
    . "$1/lib/ui.sh"
    . "$1/lib/migrations.sh"
    . "$2"
    migration_20260902_project_context_to_root "$3"
  ' _ "$REPO_ROOT" "$MIGRATION" "$REPO"
}

@test "moves the context file to the repo root" {
  echo "PROJECT CONTEXT" > "$REPO/.claude/CLAUDE.md"

  _run_migration
  [ "$status" -eq 0 ]
  [ "$(cat "$REPO/CLAUDE.md")" = "PROJECT CONTEXT" ]
  [ ! -e "$REPO/.claude/CLAUDE.md" ]
}

@test "moves rather than copies, so the two cannot drift" {
  echo "PROJECT CONTEXT" > "$REPO/.claude/CLAUDE.md"

  _run_migration
  [ ! -e "$REPO/.claude/CLAUDE.md" ]
}

@test "leaves an existing root CLAUDE.md alone and says so" {
  echo "ROOT" > "$REPO/CLAUDE.md"
  echo "NESTED" > "$REPO/.claude/CLAUDE.md"

  _run_migration
  [ "$(cat "$REPO/CLAUDE.md")" = "ROOT" ]
  [ "$(cat "$REPO/.claude/CLAUDE.md")" = "NESTED" ]
  [[ "$output" == *"CLAUDE.md"* ]]
}

@test "leaves the pair alone when the root file is an AGENTS.md" {
  echo "ROOT" > "$REPO/AGENTS.md"
  echo "NESTED" > "$REPO/.claude/CLAUDE.md"

  _run_migration
  [ -f "$REPO/.claude/CLAUDE.md" ]
}

@test "is a no-op for a repo already scaffolded at the root" {
  echo "ROOT" > "$REPO/CLAUDE.md"

  _run_migration
  [ "$status" -eq 3 ]
}

@test "defers for a repo with no .claude yet" {
  # A repo may be scaffolded after this migration first runs. NOOP here retires
  # it against a directory it never saw.
  rm -rf "$REPO/.claude"

  _run_migration
  [ "$status" -eq 4 ]
}
