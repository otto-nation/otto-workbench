#!/usr/bin/env bats
# Tests for the workbench config reader (lib/config.sh).

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_CONFIG="$TMPDIR/config"
  mkdir -p "$FAKE_CONFIG"

  export WORKBENCH_CONFIG_FILE="$FAKE_CONFIG/config.yml"

  # Project scope resolves through `git rev-parse --show-toplevel`, so a test
  # left standing in the real checkout would read this repo's own
  # .workbench.yml the day one lands. Run from the sandbox instead, and cap the
  # upward walk there so no ancestor repo can stand in for the project either.
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  cd "$TMPDIR" || return 1

  # shellcheck source=../lib/config.sh
  . "$REPO_ROOT/lib/config.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _make_project — a git repo at $TMPDIR/project, cd'd into, holding CONTENT
# as its .workbench.yml. Project scope resolves through git rev-parse, so the
# directory has to actually be a repo.
_make_project() {
  mkdir -p "$TMPDIR/project"
  printf '%s' "$1" > "$TMPDIR/project/.workbench.yml"
  git -C "$TMPDIR/project" init --quiet
  cd "$TMPDIR/project" || return 1
}

@test "wb_config_get reads a nested key" {
  printf 'reuse:\n  level: ultra\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_get "reuse.level"
  [ "$status" -eq 0 ]
  [ "$output" = "ultra" ]
}

@test "wb_config_get prints nothing for a missing key" {
  printf 'reuse:\n  level: ultra\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_get "review.model"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "wb_config_get prints nothing when there is no config file" {
  run wb_config_get "reuse.level"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "wb_config_get returns the given default for a missing key" {
  run wb_config_get "reuse.level" "full"
  [ "$status" -eq 0 ]
  [ "$output" = "full" ]
}

@test "wb_config_get prefers the project file over the global one" {
  printf 'review:\n  model: sonnet\n' > "$WORKBENCH_CONFIG_FILE"
  _make_project 'review:
  model: opus
'
  run wb_config_get "review.model"
  [ "$status" -eq 0 ]
  [ "$output" = "opus" ]
}

@test "wb_config_get falls back to the global file for a key the project omits" {
  printf 'review:\n  model: sonnet\n  effort: high\n' > "$WORKBENCH_CONFIG_FILE"
  _make_project 'review:
  model: opus
'
  run wb_config_get "review.effort"
  [ "$status" -eq 0 ]
  [ "$output" = "high" ]
}

@test "wb_config_get rejects a key that is not a literal path" {
  printf 'reuse:\n  level: ultra\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_get 'reuse.level | ("x")'
  [ "$status" -eq 1 ]
  [[ "$output" == *"invalid config key"* ]]
}

@test "wb_config_get survives a malformed config file" {
  printf 'review:\n  model: [unclosed\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_get "review.model" "sonnet"
  [ "$status" -eq 0 ]
  [ "$output" = "sonnet" ]
}
