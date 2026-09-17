#!/usr/bin/env bats
# Tests for bin/local/validate-test-deps.

setup() {
  load 'test_helper'
  common_setup
  VALIDATE="$REPO_ROOT/bin/local/validate-test-deps"
}

teardown() {
  common_teardown
}

@test "resolves its repo root from its own path, not an inherited GIT_DIR" {
  # validate-test-deps has no --help — --quiet is its only flag, and it still
  # runs the real check against the live repo (no fixture indirection exists
  # here yet), so this asserts the real check still passes with GIT_DIR
  # poisoned, not merely that some flag short-circuits before doing work.
  run env GIT_DIR="$TMPDIR/nowhere" GIT_WORK_TREE="$TMPDIR/nowhere" "$VALIDATE" --quiet
  [ "$status" -eq 0 ]
}
