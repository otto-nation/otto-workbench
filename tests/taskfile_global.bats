#!/usr/bin/env bats
# Tests for Taskfile.global.yml structure.

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "Taskfile.global.yml is valid YAML" {
  run yq '.' "$REPO_ROOT/Taskfile.global.yml"
  [ "$status" -eq 0 ]
}
