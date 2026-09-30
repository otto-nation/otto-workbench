#!/usr/bin/env bats
# Tests for bin/migrations/20260930-plans-specs-to-workspace.sh — carries a
# work tree's ignore/plans and ignore/specs to the repository's workspace at
# the container.
#
# The move is the easy half. What these mostly cover is the two ways it could
# destroy something: a filename that exists in two work trees, where taking
# the second would silently overwrite a different document; and an ordinary
# clone, which has no container, where inventing a destination would put the
# artifact back inside the checkout the migration exists to get it out of.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup

  MIGRATION="$REPO_ROOT/bin/migrations/20260930-plans-specs-to-workspace.sh"
  # Physical path: on macOS mktemp hands back /var/..., git reports the
  # /private/var/... it resolves to, and the path comparisons below fail.
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  SEED="$TMPDIR/seed"
  CONTAINER="$TMPDIR/container"

  git init -q --initial-branch=main "$SEED"
  git -C "$SEED" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
}

teardown() {
  common_teardown
}

_make_container() {
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
}

_add_worktree() {
  git -C "$CONTAINER" rev-parse --verify "$1" >/dev/null 2>&1 \
    || git -C "$CONTAINER" branch "$1" HEAD
  git -C "$CONTAINER" worktree add -q "$CONTAINER/$1" "$1"
}

# The framework passes the work tree path as the migration's only argument and
# provides BIN_SRC_DIR, the status constants, and the ui helpers.
_run_migration() {
  run bash -c '
    info() { echo "INFO $*"; }
    warn() { echo "WARN $*"; }
    BIN_SRC_DIR="$2/bin"
    MIGRATION_NOOP=3
    MIGRATION_DEFERRED=4
    . "$1"
    migration_20260930_plans_specs_to_workspace "$3"
  ' _ "$MIGRATION" "$REPO_ROOT" "$1"
}

# ─── The move ───────────────────────────────────────────────────────────────

@test "plans and specs are carried to the container's workspace" {
  _make_container
  _add_worktree feature
  local wt="$CONTAINER/feature"
  mkdir -p "$wt/ignore/plans" "$wt/ignore/specs"
  echo p > "$wt/ignore/plans/a-plan.md"
  echo s > "$wt/ignore/specs/a-spec.md"

  _run_migration "$wt"

  [ "$status" -eq 0 ]
  [ -f "$CONTAINER/workspace/plans/a-plan.md" ]
  [ -f "$CONTAINER/workspace/specs/a-spec.md" ]
  [ ! -e "$wt/ignore/plans/a-plan.md" ]
}

@test "the moved file keeps its contents" {
  _make_container
  _add_worktree feature
  local wt="$CONTAINER/feature"
  mkdir -p "$wt/ignore/plans"
  printf 'the reasoning\n' > "$wt/ignore/plans/a-plan.md"

  _run_migration "$wt"

  [ "$(cat "$CONTAINER/workspace/plans/a-plan.md")" = "the reasoning" ]
}

@test "a leading-dot file is carried too" {
  # dotglob: without it the file is left behind and the rmdir below fails
  # silently, so the migration reports success over a directory it did not
  # empty.
  _make_container
  _add_worktree feature
  local wt="$CONTAINER/feature"
  mkdir -p "$wt/ignore/specs"
  echo x > "$wt/ignore/specs/.hidden.md"

  _run_migration "$wt"

  [ -f "$CONTAINER/workspace/specs/.hidden.md" ]
}

@test "an emptied ignore directory is removed" {
  _make_container
  _add_worktree feature
  local wt="$CONTAINER/feature"
  mkdir -p "$wt/ignore/plans"
  echo p > "$wt/ignore/plans/p.md"

  _run_migration "$wt"

  [ ! -d "$wt/ignore/plans" ]
  [ ! -d "$wt/ignore" ]
}

@test "an ignore directory holding something else is left standing" {
  # `rmdir` only: an entry this migration never wrote is not its to delete.
  _make_container
  _add_worktree feature
  local wt="$CONTAINER/feature"
  mkdir -p "$wt/ignore/plans" "$wt/ignore/promote"
  echo p > "$wt/ignore/plans/p.md"

  _run_migration "$wt"

  [ ! -d "$wt/ignore/plans" ]
  [ -d "$wt/ignore/promote" ]
}

# ─── The two ways it could destroy something ────────────────────────────────

@test "a name already in the workspace is left where it is, not overwritten" {
  _make_container
  _add_worktree one
  _add_worktree two
  mkdir -p "$CONTAINER/one/ignore/plans" "$CONTAINER/two/ignore/plans"
  printf 'from one\n' > "$CONTAINER/one/ignore/plans/notes.md"
  printf 'from two\n' > "$CONTAINER/two/ignore/plans/notes.md"

  _run_migration "$CONTAINER/one"
  _run_migration "$CONTAINER/two"

  [ "$(cat "$CONTAINER/workspace/plans/notes.md")" = "from one" ]
  [ "$(cat "$CONTAINER/two/ignore/plans/notes.md")" = "from two" ]
}

@test "a clash is reported rather than passing silently" {
  _make_container
  _add_worktree one
  _add_worktree two
  mkdir -p "$CONTAINER/one/ignore/plans" "$CONTAINER/two/ignore/plans"
  echo a > "$CONTAINER/one/ignore/plans/notes.md"
  echo b > "$CONTAINER/two/ignore/plans/notes.md"
  _run_migration "$CONTAINER/one"

  _run_migration "$CONTAINER/two"

  [[ "$output" == *"WARN"*"notes.md"* ]]
}

@test "an ordinary clone is left alone rather than given an invented destination" {
  mkdir -p "$SEED/ignore/plans"
  echo p > "$SEED/ignore/plans/p.md"

  _run_migration "$SEED"

  [ "$status" -eq 3 ]
  [ -f "$SEED/ignore/plans/p.md" ]
  [ ! -e "$SEED/workspace" ]
}

# ─── Nothing to do ──────────────────────────────────────────────────────────

@test "a work tree with no plans or specs is a no-op" {
  _make_container
  _add_worktree feature

  _run_migration "$CONTAINER/feature"

  [ "$status" -eq 3 ]
}

@test "an empty plans directory is a no-op rather than a reported move" {
  # nullglob: without it the loop runs once over the literal `*`, and the
  # migration reports having moved a file named `*`.
  _make_container
  _add_worktree feature
  mkdir -p "$CONTAINER/feature/ignore/plans"

  _run_migration "$CONTAINER/feature"

  [ "$status" -eq 3 ]
  [ ! -e "$CONTAINER/workspace/plans/*" ]
}

@test "re-running after a move changes nothing" {
  _make_container
  _add_worktree feature
  local wt="$CONTAINER/feature"
  mkdir -p "$wt/ignore/plans"
  echo p > "$wt/ignore/plans/p.md"
  _run_migration "$wt"

  _run_migration "$wt"

  [ "$status" -eq 3 ]
  [ -f "$CONTAINER/workspace/plans/p.md" ]
}
