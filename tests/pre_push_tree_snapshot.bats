#!/usr/bin/env bats
# Tests for the pre-push gate's self-invalidation check.
#
# The two functions are sourced out of the workbench hook and run against a
# fixture repository, the same way pre_push_open_pr_notice.bats drives the open
# PR notice: what is under test is how they read `git status --porcelain`, and
# running the whole gate would make that reading the one thing a test cannot
# vary — each case would cost a full validate-all and two test suites.
#
# The check must fail the push when it fires. Everything above it in the hook
# validated a tree that no longer exists, so its green says nothing, and
# reporting a pass would be worse than not checking at all.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup

  HOOK="$REPO_ROOT/git/hooks/pre-push-workbench"

  # Both functions read REPO_ROOT, so it points at the fixture for the duration
  # rather than at this checkout — a snapshot of the live worktree would change
  # under the test for reasons that have nothing to do with it.
  FIXTURE="$TMPDIR/repo"
  mkdir -p "$FIXTURE"
  git -C "$FIXTURE" init -q -b feat/thing .
  git -C "$FIXTURE" config user.email test@example.com
  git -C "$FIXTURE" config user.name Test
  git -C "$FIXTURE" commit -q --allow-empty -m "x"
  export REPO_ROOT="$FIXTURE"

  # The hook's own colour and error helpers, which the functions below call.
  # shellcheck disable=SC2034  # read by _fail_if_tree_moved, eval'd from the hook below
  RED='' NC=''
  err() { printf 'ERR: %s\n' "$*" >&2; }
  export -f err

  eval "$(sed -n '/^_tree_snapshot()/,/^}/p' "$HOOK")"
  eval "$(sed -n '/^_fail_if_tree_moved()/,/^}/p' "$HOOK")"
}

teardown() {
  common_teardown
}

@test "an unchanged tree passes the check" {
  local before
  before=$(_tree_snapshot)
  run _fail_if_tree_moved "$before"
  [ "$status" -eq 0 ]
}

@test "a file written during the gate fails the push" {
  local before
  before=$(_tree_snapshot)
  echo "written by somebody else" > "$FIXTURE/new.txt"
  run _fail_if_tree_moved "$before"
  [ "$status" -eq 1 ]
  [[ "$output" == *"working tree changed while the gate ran"* ]]
  [[ "$output" == *"validated a tree that no longer exists"* ]]
}

@test "a tracked file edited during the gate fails the push" {
  echo "original" > "$FIXTURE/tracked.txt"
  git -C "$FIXTURE" add tracked.txt
  git -C "$FIXTURE" commit -q -m "add tracked"
  local before
  before=$(_tree_snapshot)
  echo "edited underneath" > "$FIXTURE/tracked.txt"
  run _fail_if_tree_moved "$before"
  [ "$status" -eq 1 ]
  [[ "$output" == *"working tree changed while the gate ran"* ]]
}

@test "the failure names the file that moved" {
  local before
  before=$(_tree_snapshot)
  echo "x" > "$FIXTURE/appeared.txt"
  run _fail_if_tree_moved "$before"
  [[ "$output" == *"appeared.txt"* ]]
}

@test "a file staged during the gate fails the push" {
  # Staging changes the porcelain status letter without changing the worktree
  # content, and it is what a generator committing its own output looks like.
  echo "x" > "$FIXTURE/staged.txt"
  local before
  before=$(_tree_snapshot)
  git -C "$FIXTURE" add staged.txt
  run _fail_if_tree_moved "$before"
  [ "$status" -eq 1 ]
}

@test "a gitignored write during the gate is not a change" {
  # This is what lets the check run with no exclude list: the generators the
  # gate invokes write only ignored paths, and porcelain does not report them.
  echo "ignored/" > "$FIXTURE/.gitignore"
  git -C "$FIXTURE" add .gitignore
  git -C "$FIXTURE" commit -q -m "ignore"
  mkdir -p "$FIXTURE/ignored"
  local before
  before=$(_tree_snapshot)
  echo "generated output" > "$FIXTURE/ignored/out.md"
  run _fail_if_tree_moved "$before"
  [ "$status" -eq 0 ]
}

@test "a tree already dirty before the gate is not a change by itself" {
  # The check is about movement during the run, not cleanliness: pushing from a
  # dirty worktree is ordinary, and failing it here would be a different rule.
  echo "work in progress" > "$FIXTURE/wip.txt"
  local before
  before=$(_tree_snapshot)
  run _fail_if_tree_moved "$before"
  [ "$status" -eq 0 ]
}

@test "the hook takes its snapshot before the first validator and compares after the last" {
  # Source-level, because the ordering is the whole point: a snapshot taken
  # after gitleaks would not cover it, and a comparison before pytest would
  # miss the suite that takes longest and is likeliest to be edited under.
  local start_line end_line gitleaks_line pytest_line notice_line
  start_line=$(grep -n '^_start_tree=$(_tree_snapshot)$' "$HOOK" | head -1 | cut -d: -f1)
  end_line=$(grep -n '^_fail_if_tree_moved "$_start_tree"$' "$HOOK" | head -1 | cut -d: -f1)
  gitleaks_line=$(grep -n 'Scanning for secrets' "$HOOK" | head -1 | cut -d: -f1)
  pytest_line=$(grep -n 'Running \$_pytest_label' "$HOOK" | head -1 | cut -d: -f1)
  notice_line=$(grep -n '^_warn_if_pr_is_open$' "$HOOK" | tail -1 | cut -d: -f1)

  [ -n "$start_line" ]
  [ -n "$end_line" ]
  [ -n "$notice_line" ]
  [ "$start_line" -lt "$gitleaks_line" ]
  [ "$end_line" -gt "$pytest_line" ]
  [ "$end_line" -lt "$notice_line" ]
}
