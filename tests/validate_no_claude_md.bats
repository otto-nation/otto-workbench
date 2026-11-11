#!/usr/bin/env bats
# Tests for bin/local/validate-no-claude-md — the gate that keeps a CLAUDE.md
# from shadowing an AGENTS.md. Claude Code reads AGENTS.md only when no
# CLAUDE.md is in the directory chain, so one anywhere hides it silently.
#
# Every case runs against a fixture repo built in TMPDIR; the one assertion
# about the live checkout is read-only.

setup() {
  load 'test_helper'
  common_setup
  VALIDATOR="$REPO_ROOT/bin/local/validate-no-claude-md"
}

teardown() {
  common_teardown
}

# _make_repo DIR — a fixture repo with a committed AGENTS.md.
_make_repo() {
  local dir="$1"
  mkdir -p "$dir"
  GIT_CEILING_DIRECTORIES="$(dirname "$dir")" git -C "$dir" init --quiet
  git -C "$dir" config user.email "test@example.com"
  git -C "$dir" config user.name "Test"
  echo "# rules" > "$dir/AGENTS.md"
  git -C "$dir" add AGENTS.md
  git -C "$dir" commit -m "initial" --quiet
}

# _commit_file DIR RELPATH — commits a file at RELPATH.
_commit_file() {
  local dir="$1" relpath="$2"
  mkdir -p "$(dirname "$dir/$relpath")"
  echo "content" > "$dir/$relpath"
  git -C "$dir" add "$relpath"
  git -C "$dir" commit -m "add $relpath" --quiet
}

@test "passes for a repo with only an AGENTS.md" {
  _make_repo "$TMPDIR/repo"
  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 0 ]
}

@test "fails on a CLAUDE.md pointer file beside AGENTS.md" {
  _make_repo "$TMPDIR/repo"
  _commit_file "$TMPDIR/repo" "CLAUDE.md"
  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 1 ]
  [[ "$output" == *"CLAUDE.md"* ]]
  [[ "$output" == *"ignore AGENTS.md"* ]]
}

@test "fails on a CLAUDE.md in place of an AGENTS.md" {
  mkdir -p "$TMPDIR/repo"
  GIT_CEILING_DIRECTORIES="$TMPDIR" git -C "$TMPDIR/repo" init --quiet
  echo "# rules" > "$TMPDIR/repo/CLAUDE.md"
  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 1 ]
}

@test "fails on a nested CLAUDE.md that shadows the root AGENTS.md" {
  _make_repo "$TMPDIR/repo"
  _commit_file "$TMPDIR/repo" "services/api/CLAUDE.md"
  run "$VALIDATOR" "$TMPDIR/repo"
  [ "$status" -eq 1 ]
  [[ "$output" == *"services/api/CLAUDE.md"* ]]
}

@test "--quiet still reports a failure" {
  _make_repo "$TMPDIR/repo"
  _commit_file "$TMPDIR/repo" "CLAUDE.md"
  run "$VALIDATOR" --quiet "$TMPDIR/repo"
  [ "$status" -eq 1 ]
  [[ "$output" == *"CLAUDE.md"* ]]
}

@test "the workbench itself tracks no shadowing CLAUDE.md" {
  run "$VALIDATOR" --quiet
  [ "$status" -eq 0 ]
}

@test "--help prints usage and exits 0" {
  run "$VALIDATOR" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: validate-no-claude-md"* ]]
}

@test "rejects an unknown flag with exit 2" {
  run "$VALIDATOR" --nope
  [ "$status" -eq 2 ]
}
