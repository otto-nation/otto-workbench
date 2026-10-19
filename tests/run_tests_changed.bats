#!/usr/bin/env bats
# Tests for bin/local/run-tests --changed: which base the selectors are handed.
#
# The runner is sourced and the selectors replaced by stubs that print the
# arguments they were given, so what --changed asks for is readable without
# starting a suite.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  unset TEST_JOBS CI WORKBENCH_TEST_SLOTS_GRANTED WORKBENCH_TEST_SLOTS WORKBENCH_FIX_BASE
  # shellcheck source=../bin/local/run-tests
  source "$REPO_ROOT/bin/local/run-tests"
}

teardown() {
  common_teardown
}

# _stub_selectors — point WORKBENCH_DIR at stubs that echo their own argv.
_stub_selectors() {
  local dir="$BATS_TEST_TMPDIR/wb" name
  mkdir -p "$dir/bin/local"
  for name in select-tests select-pytest; do
    printf '#!/usr/bin/env bash\necho "%s $*"\n' "$name" > "$dir/bin/local/$name"
    chmod +x "$dir/bin/local/$name"
  done
  # shellcheck disable=SC2034  # read by select_changed in bin/local/run-tests
  WORKBENCH_DIR="$dir"
}

@test "--base reaches both selectors" {
  _stub_selectors
  CHANGED_BASE=feature-base
  select_changed all
  [ "$BATS_FILES" = "select-tests --base feature-base" ]
  [ "$PYTEST_FILES" = "select-pytest --base feature-base" ]
}

@test "WORKBENCH_FIX_BASE is the default base" {
  _stub_selectors
  CHANGED_BASE=""
  # shellcheck disable=SC2034  # read by select_changed in bin/local/run-tests
  WORKBENCH_FIX_BASE=abc123
  select_changed all
  [ "$BATS_FILES" = "select-tests --base abc123" ]
}

@test "with neither, the base stays origin/main" {
  _stub_selectors
  # shellcheck disable=SC2034  # read by select_changed in bin/local/run-tests
  CHANGED_BASE=""
  select_changed all
  [ "$PYTEST_FILES" = "select-pytest --base origin/main" ]
}

@test "--base without --changed is refused" {
  run main --base x
  [ "$status" -eq 1 ]
  [[ "$output" == *"--base only applies with --changed"* ]]
}

@test "--base with no value is a usage error" {
  run main --changed --base
  [ "$status" -eq 2 ]
}

# _stub_select_tests BODY — a select-tests whose script is BODY, and a quiet
# select-pytest, under a stub WORKBENCH_DIR.
_stub_select_tests() {
  local dir="$BATS_TEST_TMPDIR/wb"
  mkdir -p "$dir/bin/local"
  printf '#!/usr/bin/env bash\n%s\n' "$1" > "$dir/bin/local/select-tests"
  printf '#!/usr/bin/env bash\necho tests/x_test.py\n' > "$dir/bin/local/select-pytest"
  chmod +x "$dir/bin/local/select-tests" "$dir/bin/local/select-pytest"
  # shellcheck disable=SC2034  # read by select_changed in bin/local/run-tests
  WORKBENCH_DIR="$dir"
}

@test "a selector that succeeds and selects nothing marks bats as none affected" {
  _stub_select_tests 'exit 0'
  select_changed all
  [ -z "$BATS_FILES" ]
  [ "$BATS_NONE" = true ]
}

@test "a selector that fails leaves the whole bats suite selected" {
  _stub_select_tests 'echo partial; exit 1'
  select_changed all
  [ -z "$BATS_FILES" ]
  [ "$BATS_NONE" = false ]
}

@test "run_bats with nothing affected says so and runs no bats" {
  # shellcheck disable=SC2034  # read by run_bats in bin/local/run-tests
  BATS_NONE=true
  local bin="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bin"
  printf '#!/bin/sh\necho RAN-BATS\n' > "$bin/bats"
  chmod +x "$bin/bats"
  PATH="$bin:$PATH" run --separate-stderr run_bats
  [ "$status" -eq 0 ]
  # shellcheck disable=SC2154  # stderr is set by bats' run --separate-stderr
  [[ "$stderr" == *"no test file is affected by this diff"* ]]
  [[ "$output" != *RAN-BATS* ]]
}
