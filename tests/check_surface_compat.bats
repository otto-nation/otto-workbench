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

# _commit_on_main MESSAGE — commits everything on main, pushes, and merges main
# back into the feature branch so the merge base moves forward with it.
_commit_on_main() {
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "$1" --quiet
  git -C "$LOCAL" push --quiet
  git -C "$LOCAL" checkout feat/surface --quiet
  git -C "$LOCAL" merge main --quiet -m "chore: sync"
}

# _seed_base ENTRIES_JSON — commits a root snapshot on main and pushes it.
_seed_base() {
  git -C "$LOCAL" checkout main --quiet
  _write_snapshot "public-surface.json" "otto-workbench" "$1"
  _commit_on_main "chore: seed surface"
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

# Conventional Commits v1.0.0 lists the hyphenated spelling as a synonym and
# release-please honours it, so rejecting it would fail a contributor who
# declared correctly — a false failure on the one thing this gate rewards.
@test "accepts the hyphenated BREAKING-CHANGE spelling" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta

BREAKING-CHANGE: the beta command was removed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

@test "a bang header is satisfied by the hyphenated spelling too" {
  _seed_base '["command:alpha"]'
  _commit_head '["command:alpha","command:beta"]' "feat!: add beta

BREAKING-CHANGE: beta replaces the old entrypoint"
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

# The direction a substring search gets wrong: the DECLARED key contains the
# REMOVED one, so `grep -F "Not-Breaking: command:beta"` finds a hit inside
# "Not-Breaking: command:beta-two" and silently covers a removal nobody
# declared. Only a whole-line fixed-string match rejects it.
@test "a Not-Breaking footer for a longer key does not cover a shorter removal" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta-two

Not-Breaking: command:beta-two — a longer key, not the one removed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:beta"* ]]
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
  _commit_on_main "chore: seed ai surface"

  _write_snapshot "ai/claude/public-surface.json" "otto-ai-tools" '["agent:reviewer"]'
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "feat: drop the retro skill" --quiet

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"otto-ai-tools"* ]]
  [[ "$output" == *"skill:retro"* ]]
}

# release-please has no per-package footer syntax, so the single BREAKING
# CHANGE footer that would satisfy this branch majors both packages. The gate
# has to say so, not just print two independent per-package blocks.
@test "names both packages and advises splitting when both shrink" {
  git -C "$LOCAL" checkout main --quiet
  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha","command:beta"]'
  _write_snapshot "ai/claude/public-surface.json" "otto-ai-tools" '["agent:reviewer","skill:retro"]'
  _commit_on_main "chore: seed both surfaces"

  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha"]'
  _write_snapshot "ai/claude/public-surface.json" "otto-ai-tools" '["agent:reviewer"]'
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "feat: drop one entry from each package" --quiet

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"otto-workbench"* ]]
  [[ "$output" == *"otto-ai-tools"* ]]
  [[ "$output" == *"Split the commit"* ]]
}

@test "does not advise splitting when only one package shrinks" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" != *"Split the commit"* ]]
}

@test "skips comparison when the branch shares no history with the base" {
  git -C "$LOCAL" checkout --orphan solo --quiet
  git -C "$LOCAL" rm -rq --cached .
  rm -f "$LOCAL/README.md" "$LOCAL/feature.txt"
  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha"]'
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "feat: unrelated root" --quiet
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
  [[ "$output" == *"No merge base"* ]]
}

# A base ref that does not resolve is not "nothing to compare against" — it is
# the check never running. Reporting it as a pass is how a green tick in CI
# comes to mean nothing.
@test "fails when the base ref does not resolve" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta"
  run "$GATE" --repo-dir "$LOCAL" --base origin/nonexistent
  [ "$status" -ne 0 ]
  [ "$status" -ne 1 ]
  [[ "$output" == *"Could not resolve a merge base"* ]]
  [[ "$output" != *"skipping surface comparison"* ]]
}

@test "fails when the repo dir is not a git repository" {
  mkdir -p "$TMPDIR/notarepo"
  run "$GATE" --repo-dir "$TMPDIR/notarepo"
  [ "$status" -ne 0 ]
  [ "$status" -ne 1 ]
  [[ "$output" == *"Could not resolve a merge base"* ]]
  [[ "$output" != *"skipping surface comparison"* ]]
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

# The failure is inside _removed_entries, not at the top level: .package parses
# fine, so the gate reaches the helper before anything goes wrong. A snapshot
# read as "no entries" here would report either the whole base surface as
# removed or nothing at all — both lies.
@test "a head snapshot whose entries is not an array fails loudly" {
  _seed_base '["command:alpha","command:beta"]'
  echo '{"package":"otto-workbench","entries":"command:alpha"}' > "$LOCAL/public-surface.json"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: break the entries array" --quiet
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -ne 0 ]
  [ "$status" -ne 1 ]
  [[ "$output" == *".entries is not an array"* ]]
  [[ "$output" != *"public surface compatible"* ]]
}

@test "a head snapshot with no package field fails loudly" {
  _seed_base '["command:alpha","command:beta"]'
  echo '{"entries":["command:alpha"]}' > "$LOCAL/public-surface.json"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: drop the package field" --quiet
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -ne 0 ]
  [ "$status" -ne 1 ]
  [[ "$output" == *".package is missing"* ]]
  [[ "$output" != *"null:"* ]]
}

# What the cat-file probe buys over `git show ... || true`: the probe answers
# only "does this path exist at the base", and the type check answers "is it a
# file". Anything else is a git failure, and a git failure must not read as an
# absent snapshot — which is exactly what an empty capture looks like.
@test "a base path that exists but is not a file fails with a typed error" {
  git -C "$LOCAL" checkout main --quiet
  mkdir -p "$LOCAL/public-surface.json"
  echo "placeholder" > "$LOCAL/public-surface.json/README"
  _commit_on_main "chore: seed a directory where the snapshot goes"

  rm -rf "$LOCAL/public-surface.json"
  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha"]'
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "feat: replace the directory with a snapshot" --quiet

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 2 ]
  [[ "$output" == *"is a tree at"* ]]
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
  [ "$status" -eq 2 ]
  [[ "$output" == *"Unknown argument"* ]]
}
