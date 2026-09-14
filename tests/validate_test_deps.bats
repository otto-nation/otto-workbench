#!/usr/bin/env bats
# Tests for bin/local/validate-test-deps.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATE="$REPO_ROOT/bin/local/validate-test-deps"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

@test "resolves its repo root from its own path, not an inherited GIT_DIR" {
  run env GIT_DIR="$TMPDIR/nowhere" GIT_WORK_TREE="$TMPDIR/nowhere" "$VALIDATE" --help
  [ "$status" -eq 0 ]
}
