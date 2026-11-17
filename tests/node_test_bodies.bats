#!/usr/bin/env bats
# Node never reads a long value from argv (see node_body in
# tests/test_helper.bash): test code goes through a node_body file, and command
# output never goes to node as an argument.
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

@test "no suite hands command output to node as an argument" {
  # $output is unbounded; a node reading it from argv dies past ~1000 characters.
  run grep -nE 'node .*process\.argv.*"\$output"' \
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
