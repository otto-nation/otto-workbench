#!/usr/bin/env bats
# ai sync's notice when github.owners is unset — every GitHub write is refused
# until it is configured, so the sync says so before the first refusal does.
# Sources named as $REPO_ROOT literals so bin/local/select-tests maps this
# suite to ai/steps.sh and ai/bin/owners-check.

setup() {
  load 'test_helper'
  common_setup
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  mkdir -p "$WORKBENCH_CONFIG_DIR"
}

teardown() {
  common_teardown
}

_notice() {
  bash -c '
    . "$1"
    . "$2"
    warn() { echo "WARN $*"; }
    step_github_owners_notice
  ' _ "$REPO_ROOT/lib/ui.sh" "$REPO_ROOT/ai/steps.sh"
}

@test "sync warns with the fix when github.owners is unset" {
  run _notice
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"* ]]
  [[ "$output" == *"otto-workbench config set github.owners."* ]]
}

@test "sync is silent once github.owners names an owner" {
  printf 'github:\n  owners:\n    work: [otto-nation]\n' > "$WORKBENCH_CONFIG_DIR/config.yml"
  run _notice
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "sync warns about an owner listed in two groups and still succeeds" {
  printf 'github:\n  owners:\n    work: [acme]\n    personal: [acme]\n' > "$WORKBENCH_CONFIG_DIR/config.yml"
  run _notice
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"acme"* ]]
}
