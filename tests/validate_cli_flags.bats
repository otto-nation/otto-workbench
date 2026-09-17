#!/usr/bin/env bats
# Tests for bin/local/validate-cli-flags.

setup() {
  load 'test_helper'
  common_setup
  VALIDATE="$REPO_ROOT/bin/local/validate-cli-flags"
}

teardown() {
  common_teardown
}

@test "resolves its repo root from its own path, not an inherited GIT_DIR" {
  run env GIT_DIR="$TMPDIR/nowhere" GIT_WORK_TREE="$TMPDIR/nowhere" "$VALIDATE" --help
  [ "$status" -eq 0 ]
}
