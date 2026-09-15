#!/usr/bin/env bats
# Taskfile test targets must declare the tree via run-tests, not invoke
# bats or pytest directly.

setup() {
  load 'test_helper'
  common_setup
  TASKFILE="$REPO_ROOT/Taskfile.yml"
  [ -f "$TASKFILE" ]
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
  # Filename must be the VALUE of --files, not an argument after a bare --.
  # Passing it after -- would run the entire tests/ tree plus a stray
  # argument. Matching only the substrings run-tests and --files would still
  # pass that regression.
  run yq -r '.tasks."test:*".cmds[0]' "$TASKFILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"run-tests"* ]]
  [[ "$output" == *'--files "tests/{{.FILENAME}}"'* ]]
}
