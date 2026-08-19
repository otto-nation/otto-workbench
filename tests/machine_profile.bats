#!/usr/bin/env bats
# Tests for ai/claude/skills/machine/generate-machine-profile.sh — how it names
# the workbench's own location, which used to be a list of three hardcoded
# candidate paths, and how it names the repos on the machine, which used to be a
# `find` over four guessed-at git roots.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"

  # A test's repos are all temporary, which is what the default exclusion list
  # refuses; the sandboxed state root keeps the writes out of the real registry.
  # shellcheck disable=SC2034  # read by lib/projects.sh, sourced through ui.sh
  PROJECTS_EXCLUDED_PREFIXES=("$WORKBENCH_STATE_DIR" "$WORKBENCH_CACHE_DIR")

  # shellcheck source=../lib/ui.sh
  . "$REPO_ROOT/lib/ui.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# make_repo DIR — a git work tree at DIR.
make_repo() {
  mkdir -p "$1"
  GIT_CEILING_DIRECTORIES="$(dirname "$1")" git -C "$1" init --quiet
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

@test "the machine profile lists the registered repos" {
  make_repo "$TMPDIR/alpha"
  project_register "$TMPDIR/alpha"

  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  grep -q "| alpha | $TMPDIR/alpha |" "$TMPDIR/home/.claude/machine/machine.md"
}

@test "the machine profile says so when nothing is registered" {
  # The heading used to be inside the conditional, so an empty list took the
  # whole section with it and the profile read as though the machine had no
  # repos rather than as though nothing had registered yet.
  HOME="$TMPDIR/home" run "$REPO_ROOT/ai/claude/skills/machine/generate-machine-profile.sh" --force
  [ "$status" -eq 0 ]
  grep -q '^## Project Registry' "$TMPDIR/home/.claude/machine/machine.md"
  grep -q 'No repos registered yet' "$TMPDIR/home/.claude/machine/machine.md"
}
