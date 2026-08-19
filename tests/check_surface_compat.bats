#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  REMOTE="$TMPDIR/remote.git"
  LOCAL="$TMPDIR/local"
  make_git_remote "$REMOTE" "$LOCAL" "feat/surface"
  GATE="$REPO_ROOT/bin/local/check-surface-compat"
}

teardown() {
  cd / || return 1
  rm -rf "$TMPDIR"
  common_teardown
}

# _write_snapshot RELPATH PACKAGE ENTRIES_JSON — writes a snapshot in the clone.
_write_snapshot() {
  mkdir -p "$(dirname "$LOCAL/$1")"
  echo "{\"package\":\"$2\",\"entries\":$3}" > "$LOCAL/$1"
}

# _seed_base ENTRIES_JSON — commits a root snapshot on main and pushes it.
_seed_base() {
  git -C "$LOCAL" checkout main --quiet
  _write_snapshot "public-surface.json" "otto-workbench" "$1"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: seed surface" --quiet
  git -C "$LOCAL" push --quiet
  git -C "$LOCAL" checkout feat/surface --quiet
  git -C "$LOCAL" merge main --quiet -m "chore: sync"
}

# _commit_head ENTRIES_JSON MESSAGE — commits a new root snapshot on the branch.
_commit_head() {
  _write_snapshot "public-surface.json" "otto-workbench" "$1"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "$2" --quiet
}

@test "passes when nothing was removed" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha","command:beta","command:gamma"]' "feat: add gamma"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

@test "fails when an entry disappears with no declaration" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"command:beta"* ]]
}

@test "prints REMOVED lines on stdout for machine consumption" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta"
  run --separate-stderr "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [ "$output" = "REMOVED command:beta" ]
}

@test "passes when a BREAKING CHANGE footer is present" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta

BREAKING CHANGE: the beta command was removed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

@test "passes when a matching Not-Breaking footer is present" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta

Not-Breaking: command:beta — was never installed, registry entry was wrong"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

@test "fails when Not-Breaking names a different entry" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta

Not-Breaking: command:zeta — wrong entry named"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"command:beta"* ]]
}

@test "a Not-Breaking prefix match does not cover a longer entry" {
  _seed_base '["command:alpha","command:beta-two"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta-two

Not-Breaking: command:beta — the shorter name, not the one removed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"command:beta-two"* ]]
}

@test "declares each removal separately when several disappear" {
  _seed_base '["command:alpha","command:beta","command:gamma"]'
  _commit_head '["command:alpha"]' "chore: unpublish two

Not-Breaking: command:beta — never installed
Not-Breaking: command:gamma — never installed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

@test "fails a bang header with no footer even when nothing was removed" {
  _seed_base '["command:alpha"]'
  _commit_head '["command:alpha","command:beta"]' "feat!: add beta and change everything"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"BREAKING CHANGE"* ]]
}

@test "passes a bang header that also carries the footer" {
  _seed_base '["command:alpha"]'
  _commit_head '["command:alpha","command:beta"]' "feat!: add beta

BREAKING CHANGE: beta replaces the old entrypoint"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

@test "passes when the base has no snapshot at all" {
  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha"]'
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "feat: introduce the snapshot" --quiet
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

@test "checks the otto-ai-tools snapshot as well as the root one" {
  git -C "$LOCAL" checkout main --quiet
  _write_snapshot "ai/claude/public-surface.json" "otto-ai-tools" '["agent:reviewer","skill:retro"]'
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: seed ai surface" --quiet
  git -C "$LOCAL" push --quiet
  git -C "$LOCAL" checkout feat/surface --quiet
  git -C "$LOCAL" merge main --quiet -m "chore: sync"

  _write_snapshot "ai/claude/public-surface.json" "otto-ai-tools" '["agent:reviewer"]'
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "feat: drop the retro skill" --quiet

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"otto-ai-tools"* ]]
  [[ "$output" == *"skill:retro"* ]]
}

@test "skips comparison when the base ref does not resolve" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta"
  run "$GATE" --repo-dir "$LOCAL" --base origin/nonexistent
  [ "$status" -eq 0 ]
  [[ "$output" == *"No merge base"* ]]
}

@test "a corrupt head snapshot fails loudly instead of reporting no removals" {
  _seed_base '["command:alpha","command:beta"]'
  echo 'not json at all' > "$LOCAL/public-surface.json"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: corrupt the snapshot" --quiet
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -ne 0 ]
  [[ "$output" != *"public surface compatible"* ]]
}

@test "--quiet suppresses the success line" {
  _seed_base '["command:alpha"]'
  _commit_head '["command:alpha","command:beta"]' "feat: add beta"
  run "$GATE" --repo-dir "$LOCAL" --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "--help exits zero and documents the flags" {
  run "$GATE" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--base"* ]]
  [[ "$output" == *"--repo-dir"* ]]
}

@test "rejects an unknown argument" {
  run "$GATE" --nope
  [ "$status" -eq 1 ]
  [[ "$output" == *"Unknown argument"* ]]
}
