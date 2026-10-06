#!/usr/bin/env bats
# Tests for Taskfile.global.yml structure and the help task.

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

# help must see the path of its own Taskfile. go-task exposes TASKFILE as a
# template variable only, so reading it from the shell environment leaves the
# "Using global Taskfile" banner permanently off.
@test "help recognises the installed global Taskfile path" {
  local task_bin="" candidate
  while IFS= read -r candidate; do
    if "$candidate" --version 2>/dev/null | grep -qE '^[0-9]+[.][0-9]+[.][0-9]+'; then
      task_bin="$candidate"
      break
    fi
  done < <(type -aP task)
  [ -n "$task_bin" ] || skip "go-task binary not installed"

  mkdir -p "$BATS_TEST_TMPDIR/.config/task"
  cp "$REPO_ROOT/Taskfile.global.yml" "$BATS_TEST_TMPDIR/.config/task/Taskfile.yml"
  run "$task_bin" -t "$BATS_TEST_TMPDIR/.config/task/Taskfile.yml" help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Using global Taskfile"* ]]
}
