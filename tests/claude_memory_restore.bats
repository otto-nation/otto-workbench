#!/usr/bin/env bats
# Tests for step_claude_restore_memory — which directories under ai/memory/ it
# treats as project memory, and which it must leave alone.
#
# ai/memory/ holds more than per-project memory: the retro archive lives at
# ai/memory/retro/ and the machine profile backup at ai/memory/machine/. A
# restore that walks every subdirectory would invent a ~/.claude/projects/retro
# and fill it with archived retro reports, which are not any project's memory.
# A real project slug is a path-derived name and always starts with '-'.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  export NO_COLOR=1

  # Named so select-tests can map this suite to its subject. The step itself is
  # sourced through a positional inside _run_restore, where the path is not
  # visible to a grep.
  STEPS_SH="$REPO_ROOT/ai/claude/steps.sh"
  [ -f "$STEPS_SH" ]

  FAKE_WORKBENCH="$TMPDIR/workbench"
  MEMORY="$FAKE_WORKBENCH/ai/memory"
  PROJECTS="$HOME/.claude/projects"
  mkdir -p "$HOME" "$MEMORY"
}

teardown() {
  common_teardown
}

# _backup SLUG NAME — a file in a backed-up memory directory.
_backup() {
  local slug="$1" name="$2"
  mkdir -p "$MEMORY/$slug"
  printf 'remembered\n' > "$MEMORY/$slug/$name"
}

_run_restore() {
  run bash -c '
    HOME="$2"
    WORKBENCH_DIR="$3"
    . "$1/lib/ui.sh"
    . "$1/ai/claude/steps.sh"
    step_claude_restore_memory
  ' _ "$REPO_ROOT" "$HOME" "$FAKE_WORKBENCH"
}

@test "a project slug is restored into its own memory directory" {
  _backup "-Users-isaacg-git-widget" "notes.md"

  _run_restore
  [[ "$status" -eq 0 ]]
  [[ -f "$PROJECTS/-Users-isaacg-git-widget/memory/notes.md" ]]
}

@test "the retro archive is not restored as a project" {
  _backup "retro" "26c642e40b50.md"

  _run_restore
  [[ "$status" -eq 0 ]]
  [[ ! -e "$PROJECTS/retro" ]]
}

@test "the machine profile backup is not restored as a project" {
  _backup "machine" "machine.md"

  _run_restore
  [[ "$status" -eq 0 ]]
  [[ ! -e "$PROJECTS/machine" ]]
}

@test "a non-slug directory does not stop the slugs beside it from restoring" {
  _backup "retro" "26c642e40b50.md"
  _backup "-Users-isaacg-git-widget" "notes.md"

  _run_restore
  [[ "$status" -eq 0 ]]
  [[ -f "$PROJECTS/-Users-isaacg-git-widget/memory/notes.md" ]]
  [[ ! -e "$PROJECTS/retro" ]]
}

@test "existing session memory is never overwritten by a restore" {
  _backup "-Users-isaacg-git-widget" "notes.md"
  mkdir -p "$PROJECTS/-Users-isaacg-git-widget/memory"
  printf 'this session learned something\n' \
    > "$PROJECTS/-Users-isaacg-git-widget/memory/notes.md"

  _run_restore
  [[ "$status" -eq 0 ]]
  grep -q "this session learned something" \
    "$PROJECTS/-Users-isaacg-git-widget/memory/notes.md"
}
