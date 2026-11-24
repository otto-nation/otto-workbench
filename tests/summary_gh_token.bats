#!/usr/bin/env bats
# print_workbench_summary GH_TOKEN line: github.env first, then taskfile.env.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  export NO_COLOR=1
  # shellcheck source=../lib/constants.sh
  source "$REPO_ROOT/lib/constants.sh"
  # shellcheck source=../lib/output.sh
  source "$REPO_ROOT/lib/output.sh"
  # shellcheck source=../lib/summary.sh
  source "$REPO_ROOT/lib/summary.sh"
}

teardown() {
  common_teardown
}

@test "summary reports GH_TOKEN from github.env when the legacy file is absent" {
  mkdir -p "$(dirname "$WORKBENCH_GITHUB_ENV")"
  printf 'GH_TOKEN=from-new\n' > "$WORKBENCH_GITHUB_ENV"
  run print_workbench_summary
  [ "$status" -eq 0 ]
  [[ "$output" == *"configured"* ]]
  [[ "$output" == *"github.env"* ]]
  [[ "$output" != *"not set"* ]]
}

@test "summary reports GH_TOKEN from taskfile.env when github.env has no token" {
  mkdir -p "$(dirname "$TASKFILE_ENV")"
  printf 'GH_TOKEN=from-old\n' > "$TASKFILE_ENV"
  run print_workbench_summary
  [ "$status" -eq 0 ]
  [[ "$output" == *"configured"* ]]
  [[ "$output" == *"taskfile.env"* ]]
}
