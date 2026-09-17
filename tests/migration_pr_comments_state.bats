#!/usr/bin/env bats
# Tests for ai/claude/migrations/20260915-pr-comments-state-root.sh — carries a
# work tree's ignore/pr-comments/state.json to its run target under the state
# root.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260915-pr-comments-state-root.sh"
  STATE="$TMPDIR/state"
  REPO="$TMPDIR/repo"
  mkdir -p "$STATE"
  _init_repo "$REPO" "git@github.com:acme/widget.git"
}

teardown() {
  common_teardown
}

# A work tree the migration can key: an origin to canonicalise and a branch to
# slug. Committed, because the key is read out of git rather than guessed.
_init_repo() {
  local path="$1" origin="$2"
  mkdir -p "$path"
  git -C "$path" init -q -b main
  git -C "$path" remote add origin "$origin"
  git -C "$path" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
}

# The framework passes the work tree path as the migration's only argument, and
# exports WORKBENCH_STATE_DIR. The migration shells out to python3 for the key,
# which reads the state root from the same variable, so it is exported here
# rather than only set for the bash -c.
_run_migration() {
  WORKBENCH_STATE_DIR="$STATE" run bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    MIGRATION_NOOP=3
    MIGRATION_DEFERRED=4
    export WORKBENCH_STATE_DIR
    . "$1"
    migration_20260915_pr_comments_state_root "$3"
  ' _ "$MIGRATION" "$REPO_ROOT" "${1:-$REPO}"
}

# Where pr.target says this work tree's ledger belongs. Asked of the same module
# the migration asks, because a literal here would be the second implementation
# of the key the migration exists not to have.
_expected_ledger() {
  WORKBENCH_STATE_DIR="$STATE" python3 - "$REPO_ROOT" "$REPO" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "ai" / "lib"))
from pr import target as pr_target

print(pr_target.target_dir_for_checkout(Path(sys.argv[2])) / "pr-comments" / "state.json")
PY
}

_seed_ledger() {
  mkdir -p "$REPO/ignore/pr-comments"
  printf '%s' "${1:-{\"pr_number\": 7\}}" > "$REPO/ignore/pr-comments/state.json"
}

# ─── The carry ───────────────────────────────────────────────────────────────

@test "carries the ledger to the run target" {
  _seed_ledger '{"pr_number": 7, "threads": {"T_a": {"classification": "suggestion"}}}'
  local dest
  dest=$(_expected_ledger)

  _run_migration
  [ "$status" -eq 0 ]
  [ -f "$dest" ]
  # The triage decisions, which no API call reproduces, are what the carry is
  # for — an empty file at the right path would pass a mere existence check.
  [[ "$(cat "$dest")" == *'"classification": "suggestion"'* ]]
}

@test "moves rather than copies, so one ledger answers for the PR" {
  _seed_ledger

  _run_migration
  [ ! -e "$REPO/ignore/pr-comments/state.json" ]
}

@test "removes the directories the ledger vacated" {
  _seed_ledger

  _run_migration
  [ ! -d "$REPO/ignore/pr-comments" ]
  [ ! -d "$REPO/ignore" ]
}

@test "leaves an ignore/ that holds anything else alone" {
  # `ignore/` is a real directory in some repos, holding plans and specs. The
  # migration reclaims what it emptied and nothing more.
  _seed_ledger
  mkdir -p "$REPO/ignore/plans"
  echo "a plan" > "$REPO/ignore/plans/one.md"

  _run_migration
  [ -f "$REPO/ignore/plans/one.md" ]
  [ ! -d "$REPO/ignore/pr-comments" ]
}

# ─── Idempotence ─────────────────────────────────────────────────────────────

@test "is a no-op for a work tree with no ledger" {
  _run_migration
  [ "$status" -eq 3 ]
}

@test "running it twice carries once and then reports nothing to do" {
  _seed_ledger
  local dest
  dest=$(_expected_ledger)

  _run_migration
  [ "$status" -eq 0 ]

  _run_migration
  [ "$status" -eq 3 ]
  [ -f "$dest" ]
}

@test "drops an old ledger the new location has already superseded" {
  # Both present means a run wrote the new location after the old file was last
  # touched, so the old one is staler than what it would overwrite. Left in
  # place it would be found again by every later sync.
  local dest
  dest=$(_expected_ledger)
  mkdir -p "$(dirname "$dest")"
  printf '%s' '{"pr_number": 9}' > "$dest"
  _seed_ledger '{"pr_number": 7}'

  _run_migration
  [ "$status" -eq 0 ]
  [ ! -e "$REPO/ignore/pr-comments/state.json" ]
  [[ "$(cat "$dest")" == *'"pr_number": 9'* ]]
}

# ─── A work tree with no key ─────────────────────────────────────────────────

@test "leaves the ledger in place when the work tree has no origin" {
  # No origin is no key, and no key is no destination. Failing rather than
  # succeeding keeps the framework from recording it, so a repo that gains a
  # remote is carried by the next sync instead of having been retired against a
  # path nothing read.
  local bare="$TMPDIR/no-origin"
  mkdir -p "$bare/ignore/pr-comments"
  git -C "$bare" init -q -b main
  git -C "$bare" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  printf '%s' '{"pr_number": 7}' > "$bare/ignore/pr-comments/state.json"

  _run_migration "$bare"
  [ "$status" -ne 0 ]
  [ "$status" -ne 3 ]
  [ -f "$bare/ignore/pr-comments/state.json" ]
  [[ "$output" == *"WARN"* ]]
}

@test "keys two branches of one work tree to two ledgers" {
  # The move is not only out of the repo, it is onto the run's target — which is
  # what stops one worktree used for two branches in turn from having a single
  # ledger answering for both.
  _seed_ledger '{"pr_number": 7}'
  local first
  first=$(_expected_ledger)

  _run_migration
  [ -f "$first" ]

  git -C "$REPO" checkout -q -b other
  _seed_ledger '{"pr_number": 8}'
  local second
  second=$(_expected_ledger)
  [ "$first" != "$second" ]

  _run_migration
  [ -f "$second" ]
  [[ "$(cat "$second")" == *'"pr_number": 8'* ]]
  [[ "$(cat "$first")" == *'"pr_number": 7'* ]]
}
