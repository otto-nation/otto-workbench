#!/usr/bin/env bats
# Taskfile test targets must declare the tree via run-tests, not invoke
# bats or pytest directly.

setup() {
  load 'test_helper'
  common_setup
  TASKFILE="$REPO_ROOT/Taskfile.yml"
}

@test "no task target invokes bats directly" {
  # Every suite must go through run-tests, which declares the tree. A direct
  # bats call is a validation nothing can see.
  run grep -nE '^\s+- bats ' "$TASKFILE"
  [ "$status" -ne 0 ]
}

@test "no task target invokes pytest directly" {
  run grep -nE '^\s+- pytest ' "$TASKFILE"
  [ "$status" -ne 0 ]
}

@test "test:* routes a single file through run-tests" {
  run yq -r '.tasks."test:*".cmds[0]' "$TASKFILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"run-tests"* ]]
  [[ "$output" == *"--files"* ]]
}
