#!/usr/bin/env bats
# Tests for bin/local/template — the scaffold new scripts are copied from.

setup() {
  load 'test_helper'
  common_setup
  SCRIPT="$REPO_ROOT/bin/local/template"
}

teardown() {
  common_teardown
}

@test "resolves its repo root from its own path, not an inherited GIT_DIR" {
  run env GIT_DIR="$TMPDIR/nowhere" GIT_WORK_TREE="$TMPDIR/nowhere" bash "$SCRIPT" --help
  [ "$status" -eq 0 ]
}
