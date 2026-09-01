#!/usr/bin/env bats
# Tests for the ceiling-debt Stop hook — where it scans and what it says when it cannot.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1

  SCRIPT="$REPO_ROOT/ai/skills/ceiling-debt/generate-ceiling-debt.sh"
  # Physical path: on macOS mktemp hands back /var/..., git reports the
  # /private/var/... it resolves to, and every path comparison below would fail.
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  SEED="$TMPDIR/seed"
  CONTAINER="$TMPDIR/container"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _make_seed — a one-commit repo carrying a .claude/ and one ceiling marker.
# The marker is written through printf so this file's own lines never open with
# one — bin/local/validate-ceiling scans the test suite too.
_make_seed() {
  git init -q --initial-branch=main "$SEED"
  git -C "$SEED" config user.email test@example.com
  git -C "$SEED" config user.name Test
  mkdir -p "$SEED/.claude"
  # A tracked file inside .claude/, so a worktree cloned from this one has the
  # directory at all — git carries no empty directories.
  printf '{}\n' > "$SEED/.claude/settings.json"
  printf '%s\n' "# ceiling: one lock for every writer, upgrade when a second writer appears" > "$SEED/lock.sh"
  git -C "$SEED" add -A
  git -C "$SEED" commit -qm init
}

# _make_container — container/.git bare with a worktree on main beside it.
_make_container() {
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
  git -C "$CONTAINER" worktree add -q "$CONTAINER/main" main
}

# ── Scanning ─────────────────────────────────────────────────────────────────

@test "writes the ledger for an ordinary repo" {
  _make_seed

  run "$SCRIPT" "$SEED"
  [ "$status" -eq 0 ]
  [ -f "$SEED/.claude/ceiling-debt.md" ]
  grep -q "one lock for every writer" "$SEED/.claude/ceiling-debt.md"
}

@test "scans the resolved worktree when started at a bare container" {
  _make_seed
  _make_container

  run "$SCRIPT" "$CONTAINER"
  [ "$status" -eq 0 ]
  [ -f "$CONTAINER/main/.claude/ceiling-debt.md" ]
  grep -q "one lock for every writer" "$CONTAINER/main/.claude/ceiling-debt.md"
  [ ! -e "$CONTAINER/.claude/ceiling-debt.md" ]
}

@test "defaults to the current directory" {
  _make_seed
  _make_container

  cd "$CONTAINER"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [ -f "$CONTAINER/main/.claude/ceiling-debt.md" ]
}

# ── Skips ────────────────────────────────────────────────────────────────────

@test "reports a container it cannot resolve instead of vanishing" {
  _make_seed
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"

  run "$SCRIPT" "$CONTAINER"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no worktree resolved for $CONTAINER"* ]]
}

@test "says nothing outside a repo" {
  local loose="$TMPDIR/loose"
  mkdir -p "$loose"
  export GIT_CEILING_DIRECTORIES="$TMPDIR"

  run "$SCRIPT" "$loose"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "says nothing for a repo with no .claude directory" {
  _make_seed
  rm -rf "$SEED/.claude"

  run "$SCRIPT" "$SEED"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -e "$SEED/.claude" ]
}
