#!/usr/bin/env bats
# Tests that the sync commands never block on a git credential prompt.
# bin/otto-workbench dispatches on load, so the command functions cannot be
# sourced on their own; these read each function body rather than running a
# full sync. They check the export is a live line, not that a child process
# sees it.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# Prints the body of a top-level function in bin/otto-workbench.
_fn_body() {
  awk -v fn="$1" '$0 == fn "() {" {f=1} f&&/^\}/{exit} f' "$REPO_ROOT/bin/otto-workbench"
}

@test "cmd_sync exports GIT_TERMINAL_PROMPT=0" {
  run _fn_body cmd_sync
  [ "$status" -eq 0 ]
  grep -qx '  export GIT_TERMINAL_PROMPT=0' <<<"$output"
}

@test "cmd_ai_sync exports GIT_TERMINAL_PROMPT=0" {
  run _fn_body cmd_ai_sync
  [ "$status" -eq 0 ]
  grep -qx '  export GIT_TERMINAL_PROMPT=0' <<<"$output"
}
