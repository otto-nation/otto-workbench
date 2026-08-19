#!/usr/bin/env bats
# Tests for ai/claude/skills/machine/generate-machine-profile.sh — specifically
# how it names the workbench's own location, which used to be a list of three
# hardcoded candidate paths (#780).
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

@test "the machine profile names a workbench location that really exists" {
  # The three hardcoded candidates could promise no such thing: each was a guess
  # at where the repo might be, and a machine that cloned it anywhere else got a
  # profile with the line silently missing.
  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]

  local named
  named="$(sed -n 's|^- otto-workbench: ||p' "$TMPDIR/home/.claude/machine/machine.md")"
  [ -n "$named" ]
  [ -f "$named/lib/constants.sh" ]
}

@test "the machine profile reports an unresolvable workbench location" {
  # An empty workbench_dir used to drop the line entirely, with nothing anywhere
  # reporting the miss. It is now visible in the profile and on stderr.
  export WORKBENCH_STABLE_DIR="$TMPDIR/no-such-workbench"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  [[ "$output" == *"did not resolve"* ]]
  grep -q '^- otto-workbench: location unresolved' "$TMPDIR/home/.claude/machine/machine.md"
}
