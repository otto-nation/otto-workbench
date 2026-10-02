#!/usr/bin/env bats
# load_gh_token is a shim over ai/lib/pr/gh_token.py; resolution itself is
# tested in tests/pr_gh_token_test.py. These cases pin only the hand-off.

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_HOME="$HOME"
  ORIG_DIR="$PWD"
  export HOME="$TMPDIR"
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  unset GH_TOKEN
  mkdir -p "$HOME/.config/task"
  cd "$TMPDIR" || return 1
}

teardown() {
  export HOME="$ORIG_HOME"
  cd "$ORIG_DIR" || return 1
  unset GH_TOKEN
  common_teardown
}

@test "exports the token the resolver prints" {
  printf 'GH_TOKEN=ghp_shim\n' > "$HOME/.config/task/taskfile.env"
  load_gh_token
  [ "$GH_TOKEN" = "ghp_shim" ]
  [ "$(sh -c 'printf %s "$GH_TOKEN"')" = "ghp_shim" ]
}

@test "returns 1 and leaves GH_TOKEN unset when nothing is configured" {
  run load_gh_token
  [ "$status" -eq 1 ]
  [[ "$output" == *"GH_TOKEN not configured"* ]]
  load_gh_token || true
  [ -z "${GH_TOKEN:-}" ]
}

@test "unsets _gh_token after a failed call" {
  load_gh_token || true
  [ -z "${_gh_token+x}" ]
}
