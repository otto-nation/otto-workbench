#!/usr/bin/env bats
# Node test runners read the code they compile from a node_body file, never
# from argv (see node_body in tests/test_helper.bash).
# select-on: tests/*.bats tests/*.bash

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "no suite compiles code taken from argv" {
  # eval / Function / AsyncFunction over process.argv, outside node_body_read.
  # Heuristic: same-line only; a split-line or variable-mediated compile is not caught.
  run grep -nE "(eval\(|Function\(|constructor\()[^;]*process\.argv\[" \
    "$REPO_ROOT"/tests/*.bats "$REPO_ROOT"/tests/*.bash
  [ "$status" -eq 1 ]
  [ -z "$output" ]
}

@test "node_body hands a multi-kilobyte body to a runner through a file" {
  local body file
  body="$(printf '/*%*s*/' 4000 '') return 41 + 1;"
  file=$(node_body "$body")
  [[ "$file" == "$BATS_TEST_TMPDIR"/node-body.* ]]
  [ "$(cat "$file")" = "$body" ]
  run node --input-type=module -e "
    const body = new (async () => {}).constructor($(node_body_read 1));
    process.stdout.write(String(await body()));
  " -- "$file"
  [ "$status" -eq 0 ]
  [ "$output" = "42" ]
}
