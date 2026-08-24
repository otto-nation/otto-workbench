#!/usr/bin/env bats
# Tests for `otto-workbench permissions` — the dispatch in bin/otto-workbench
# and the one contract the sweep behind it owes its callers: it reports, so it
# exits 0 whether or not it found drift. The classification itself is Python and
# is covered by tests/permission_sweep_test.py.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # Fully resolved: on macOS mktemp hands back a /var/folders path that git
  # reports as /private/var/folders, and the container walk compares the two.
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export WORKBENCH_CACHE_DIR="$TMPDIR/cache"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"

  # The sweep reads ~/.claude/settings.json for the grants that apply in every
  # repo. Left pointing at the real home, what these tests report would depend
  # on the machine running them.
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"

  REGISTRY="$WORKBENCH_STATE_DIR/projects.registry"
  mkdir -p "$WORKBENCH_STATE_DIR"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# make_worktree_layout DIR — a bare container with `main` checked out beside it,
# which is the layout that hides a .claude/ above every worktree's walk.
make_worktree_layout() {
  local container="$1" seed="$1.seed"
  mkdir -p "$seed"
  git -C "$seed" init --quiet
  git -C "$seed" -c user.email=t@example.com -c user.name=t commit --allow-empty -qm init
  git -C "$seed" branch -qM main
  mkdir -p "$container"
  git clone --bare --quiet "$seed" "$container/.git"
  git --git-dir="$container/.git" worktree add "$container/main" main >/dev/null 2>&1
  rm -rf "$seed"
}

# write_settings FILE RULE... — a settings file granting each RULE.
write_settings() {
  local file="$1"
  shift
  mkdir -p "$(dirname "$file")"
  printf '%s\n' "$@" | python3 -c '
import json, sys
rules = [line.strip() for line in sys.stdin if line.strip()]
print(json.dumps({"permissions": {"allow": rules}}, indent=2))
' > "$file"
}

# ─── Dispatch ────────────────────────────────────────────────────────────────

@test "permissions --help prints usage and names its subcommand" {
  run "$REPO_ROOT/bin/otto-workbench" permissions --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: otto-workbench permissions"* ]]
  [[ "$output" == *"sweep"* ]]
}

@test "permissions with no subcommand sweeps" {
  run "$REPO_ROOT/bin/otto-workbench" permissions
  [ "$status" -eq 0 ]
  [[ "$output" == *"No repos registered yet"* ]]
}

@test "permissions sweep is the same command spelled out" {
  run "$REPO_ROOT/bin/otto-workbench" permissions sweep
  [ "$status" -eq 0 ]
  [[ "$output" == *"No repos registered yet"* ]]
}

@test "a bare flag reaches the sweep instead of reading as a subcommand" {
  run "$REPO_ROOT/bin/otto-workbench" permissions --prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"No repos registered yet"* ]]
}

@test "an unknown permissions subcommand prints usage and fails" {
  run "$REPO_ROOT/bin/otto-workbench" permissions nonsense
  [ "$status" -eq 1 ]
  [[ "$output" == *"Usage: otto-workbench permissions"* ]]
}

@test "an unknown flag is rejected by the sweep, not swallowed" {
  run "$REPO_ROOT/bin/otto-workbench" permissions sweep --nonsense
  [ "$status" -ne 0 ]
  [[ "$output" == *"--nonsense"* ]]
}

@test "permissions is listed in the top-level usage" {
  run "$REPO_ROOT/bin/otto-workbench" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"permissions"* ]]
}

# ─── The sweep, end to end ───────────────────────────────────────────────────

@test "the sweep reports drift in a registered repo's container and still exits 0" {
  make_worktree_layout "$TMPDIR/container"
  write_settings "$HOME/.claude/settings.json" "Bash(gh pr:*)"
  write_settings "$TMPDIR/container/.claude/settings.local.json" "Bash(gh pr view 12)"
  echo "$TMPDIR/container/main" > "$REGISTRY"

  run "$REPO_ROOT/bin/otto-workbench" permissions sweep
  [ "$status" -eq 0 ]
  [[ "$output" == *"1 registered repo(s), 1 with drift"* ]]
  [[ "$output" == *"already granted elsewhere"* ]]
}

@test "the sweep says so when a registered repo has nothing local" {
  make_worktree_layout "$TMPDIR/container"
  echo "$TMPDIR/container/main" > "$REGISTRY"

  run "$REPO_ROOT/bin/otto-workbench" permissions sweep
  [ "$status" -eq 0 ]
  [[ "$output" == *"0 with drift"* ]]
  [[ "$output" == *"no drift"* ]]
}

@test "--prune deletes the covered grant and leaves the one-off alone" {
  make_worktree_layout "$TMPDIR/container"
  local local_settings="$TMPDIR/container/.claude/settings.local.json"
  write_settings "$HOME/.claude/settings.json" "Bash(gh pr:*)"
  write_settings "$local_settings" "Bash(gh pr view 12)" "Bash(printenv)"
  echo "$TMPDIR/container/main" > "$REGISTRY"

  run "$REPO_ROOT/bin/otto-workbench" permissions --prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"pruned 1 grant(s)"* ]]

  run grep -c "gh pr view 12" "$local_settings"
  [ "$status" -ne 0 ]
  run grep -c "printenv" "$local_settings"
  [ "$status" -eq 0 ]
}
