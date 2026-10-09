#!/usr/bin/env bats
# The two alerts that come with AGENTS.md as the instructions file: `ai sync`
# warns when Claude Code is older than the release that reads AGENTS.md, and
# lists registered repos still on CLAUDE.md. Both only report — nothing here
# updates Claude Code or renames a repo's file.
#
# Sources are named as $REPO_ROOT literals so bin/local/select-tests maps this
# suite to lib/setup.sh, ai/claude/steps.sh and ai/steps.sh.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  STUBS="$TMPDIR/stubs"
  mkdir -p "$STUBS"
  UI="$REPO_ROOT/lib/ui.sh"
  CLAUDE_STEPS="$REPO_ROOT/ai/claude/steps.sh"
  AI_STEPS="$REPO_ROOT/ai/steps.sh"
}

teardown() {
  common_teardown
}

# _lib FN ARGS... — runs FN from lib/setup.sh with the ui helpers loaded.
_lib() {
  bash -c '. "$1"; shift; "$@"' _ "$UI" "$@"
}

@test "version_at_least compares numerically, field by field" {
  run _lib version_at_least 2.1.292 2.1.277
  [ "$status" -eq 0 ]
  run _lib version_at_least 2.1.277 2.1.277
  [ "$status" -eq 0 ]
  run _lib version_at_least 2.1.99 2.1.277
  [ "$status" -eq 1 ]
  run _lib version_at_least 2.10.0 2.9.9
  [ "$status" -eq 0 ]
  run _lib version_at_least 2.1 2.1.0
  [ "$status" -eq 0 ]
}

# _step_claude_version — runs step_claude_version with the claude stub in
# $STUBS ahead of PATH.
_step_claude_version() {
  bash -c '
    . "$1"
    . "$2"
    warn() { echo "WARN $*"; }
    PATH="$3:$PATH"
    step_claude_version
  ' _ "$UI" "$CLAUDE_STEPS" "$STUBS"
}

# _claude_reports TEXT — a claude stub whose --version prints TEXT.
_claude_reports() {
  printf '#!/bin/sh\necho "%s"\n' "$1" > "$STUBS/claude"
  chmod +x "$STUBS/claude"
}

@test "an older Claude Code is warned about, naming the update command" {
  _claude_reports "2.1.200 (Claude Code)"
  run _step_claude_version
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN Claude Code 2.1.200 is older than 2.1.277"* ]]
  [[ "$output" == *"claude update"* ]]
}

@test "a Claude Code at or past the minimum is not warned about" {
  _claude_reports "2.1.277 (Claude Code)"
  run _step_claude_version
  [ "$status" -eq 0 ]
  [[ "$output" != *WARN* ]]
}

@test "an unreadable Claude Code version is left alone" {
  _claude_reports "garbled"
  run _step_claude_version
  [ "$status" -eq 0 ]
  [[ "$output" != *WARN* ]]
}

# _report REPO... — runs step_instructions_report with the registry reading the
# named directories as one repo each. The registry is read into a variable
# first: a positional inside the stub's body would be the stub's own argument.
_report() {
  local lines="" dir
  for dir in "$@"; do lines+="id-${dir##*/}"$'\t'"$dir"$'\n'; done
  bash -c '
    registry="$3"
    . "$1"
    . "$2"
    info() { echo "INFO $*"; }
    project_repo_leaders() { printf "%s" "$registry"; }
    step_instructions_report
  ' _ "$UI" "$AI_STEPS" "$lines"
}

@test "sync lists registered repos still on CLAUDE.md and leaves them as they are" {
  mkdir -p "$TMPDIR/legacy" "$TMPDIR/moved"
  echo "# rules" > "$TMPDIR/legacy/CLAUDE.md"
  echo "# rules" > "$TMPDIR/moved/AGENTS.md"
  run _report "$TMPDIR/legacy" "$TMPDIR/moved"
  [ "$status" -eq 0 ]
  [[ "$output" == *"still in CLAUDE.md"* ]]
  [[ "$output" == *"$TMPDIR/legacy: CLAUDE.md"* ]]
  [[ "$output" != *"$TMPDIR/moved"* ]]
  [ -f "$TMPDIR/legacy/CLAUDE.md" ]
  [ ! -e "$TMPDIR/legacy/AGENTS.md" ]
}

@test "sync says nothing when every registered repo is on AGENTS.md" {
  # Two repos, so a report reading no registry at all would not pass this by
  # also printing nothing for the legacy case the test above covers.
  mkdir -p "$TMPDIR/moved" "$TMPDIR/also-moved"
  echo "# rules" > "$TMPDIR/moved/AGENTS.md"
  echo "# rules" > "$TMPDIR/also-moved/AGENTS.md"
  run _report "$TMPDIR/moved" "$TMPDIR/also-moved"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a registered repo whose directory is gone is skipped" {
  run _report "$TMPDIR/gone"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}
