#!/usr/bin/env bats
# The pre-push hook's bats selection: _select_bats is sourced out of the
# workbench hook and run against a stub select-tests, so the three outcomes —
# files selected, nothing affected, selector failed — are told apart without
# running a suite.
# shellcheck disable=SC2154  # _bats_* are assigned by the sourced _select_bats

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup

  HOOK="$REPO_ROOT/git/hooks/pre-push-workbench"

  # The function reads REPO_ROOT to find the selector and the tests dir, so it
  # points at a fixture tree for the duration — not at this checkout.
  FIXTURE="$TMPDIR/wb"
  mkdir -p "$FIXTURE/bin/local" "$FIXTURE/tests"
  touch "$FIXTURE/tests/a.bats" "$FIXTURE/tests/b.bats" "$FIXTURE/tests/c.bats"
  export REPO_ROOT="$FIXTURE"
  export GIT_REMOTE=origin
  # shellcheck disable=SC2034  # read by the sourced _select_bats
  _default_branch=main

  local fn
  fn="$(sed -n '/^_select_bats()/,/^}/p' "$HOOK")"
  [[ -n "$fn" ]] || { echo "_select_bats not found in $HOOK" >&2; return 1; }
  eval "$fn"
}

teardown() {
  common_teardown
}

# _stub_selector BODY — a select-tests whose script is BODY.
_stub_selector() {
  printf '#!/usr/bin/env bash\n%s\n' "$1" > "$FIXTURE/bin/local/select-tests"
  chmod +x "$FIXTURE/bin/local/select-tests"
}

@test "selected files are passed on and counted against the whole tests dir" {
  _stub_selector 'echo "$PWD/tests/a.bats"; echo "$PWD/tests/b.bats"'
  _select_bats
  [ "$_bats_none" = false ]
  [ "$(echo "$_bats_selected" | wc -l | tr -d ' ')" -eq 2 ]
  [ "$_bats_label" = "tests (2/3 files)" ]
}

@test "a selector that succeeds and selects nothing marks the step as none affected" {
  _stub_selector 'exit 0'
  _select_bats
  [ "$_bats_none" = true ]
  [ -z "$_bats_selected" ]
}

@test "a selector that fails falls back to the whole suite, not to none" {
  _stub_selector 'echo "$PWD/tests/a.bats"; exit 1'
  _select_bats
  [ "$_bats_none" = false ]
  [ -z "$_bats_selected" ]
  [ "$_bats_label" = "tests" ]
}

@test "the selector is handed the remote default branch as its base" {
  _stub_selector 'echo "$*" > "$REPO_ROOT/args"; echo "$PWD/tests/a.bats"'
  _select_bats
  [ "$(cat "$FIXTURE/args")" = "--base origin/main" ]
}
