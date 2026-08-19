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

# _commit_head_verbatim ENTRIES_JSON MESSAGE_FILE — the same, with the message
# taken byte for byte from MESSAGE_FILE. git's default cleanup strips trailing
# whitespace from every line and would rewrite a footer whose reason is only
# whitespace into one with no separator at all — a different code path from the
# one such a test means to exercise. A CRLF body needs the same treatment.
_commit_head_verbatim() {
  _write_snapshot "public-surface.json" "otto-workbench" "$1"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit --cleanup=verbatim -F "$2" --quiet
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

# An entry with a space in it (a settings key, a skill name) is the case a
# whitespace-field read of the footer can never declare: it truncates the key
# at the first space, no footer ever matches, and the author's only remaining
# escape is BREAKING CHANGE — which majors both packages, the exact outcome
# CONTRIBUTING.md tells them to avoid. No committed snapshot holds such an
# entry today, so this guards a latent hole rather than a live one.
@test "a Not-Breaking footer declares an entry containing a space" {
  _seed_base '["command:alpha","setting:permissions deny"]'
  _commit_head '["command:alpha"]' "chore: unpublish the spaced entry

Not-Breaking: setting:permissions deny — it was never a documented key"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

# The reason landing in git history is the entire argument for a footer over a
# checked-in allowlist, so a footer that carries no reason has declared nothing.
# This one carries no separator either, so it never reaches the reason check.
@test "a Not-Breaking footer with no separator declares nothing" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta

Not-Breaking: command:beta"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:beta"* ]]
}

# The separator is there and the reason is not, which is the case the parser's
# empty-reason guard exists for. Committed verbatim on purpose: git's default
# cleanup would strip the trailing space and turn this into the no-separator
# test above, leaving the guard covered by nothing.
@test "a Not-Breaking footer whose reason is only whitespace declares nothing" {
  _seed_base '["command:alpha","command:beta"]'
  printf 'chore: unpublish beta\n\nNot-Breaking: command:beta \xe2\x80\x94 \n' > "$TMPDIR/msg"
  _commit_head_verbatim '["command:alpha"]' "$TMPDIR/msg"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:beta"* ]]
}

# A CRLF body ends every line with a carriage return, and "\r" is not
# whitespace — so a reason of nothing but the line ending would read as a real
# reason and declare the entry, defeating the guard above.
@test "a carriage return is not a Not-Breaking reason" {
  _seed_base '["command:alpha","command:beta"]'
  printf 'chore: unpublish beta\r\n\r\nNot-Breaking: command:beta \xe2\x80\x94 \r\n' > "$TMPDIR/msg"
  _commit_head_verbatim '["command:alpha"]' "$TMPDIR/msg"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:beta"* ]]
}

# A CRLF body must still declare a footer that does carry a reason: stripping
# the carriage return is what keeps the reason from being whitespace-only, and
# it must not take anything else with it.
@test "a CRLF footer with a real reason still declares its entry" {
  _seed_base '["command:alpha","command:beta"]'
  printf 'chore: unpublish beta\r\n\r\nNot-Breaking: command:beta \xe2\x80\x94 never installed\r\n' > "$TMPDIR/msg"
  _commit_head_verbatim '["command:alpha"]' "$TMPDIR/msg"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

# A second space before the separator lands inside the key, so without the trim
# the gate would look for "command:beta " and no footer could ever declare the
# entry — the author's only escape left being BREAKING CHANGE.
@test "extra space before the separator does not change the declared key" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta

Not-Breaking: command:beta  — never installed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

# A human or a model typing the footer by hand reaches for "-" or "--" long
# before U+2014. Rejecting those spellings would push a correct declaration
# toward BREAKING CHANGE for a formatting reason.
@test "an ASCII hyphen or an en dash separates the entry from its reason" {
  _seed_base '["command:alpha","command:beta","command:gamma","command:delta"]'
  _commit_head '["command:alpha"]' "chore: unpublish three

Not-Breaking: command:beta - never installed
Not-Breaking: command:gamma -- never installed
Not-Breaking: command:delta – never installed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

# Splitting on the last separator instead of the first would read the key as
# "command:beta — renamed" and leave command:beta undeclared.
@test "a reason containing another dash does not extend the declared key" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta

Not-Breaking: command:beta — renamed — the old name is still symlinked"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

# A colon is not a separator: "Not-Breaking: command:beta: gone" names no
# reason the gate can find, and treating the key's own colon as the split point
# would declare "command" instead.
@test "a colon does not separate the entry from its reason" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta

Not-Breaking: command:beta: it was never installed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:beta"* ]]
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

# ── Head side: the working tree and HEAD are both read ─────────────────────

# `git push` publishes HEAD, so a gate that judges only the working tree can be
# talked into a green pre-push by restoring the snapshot without committing it
# — git stash, an uncommitted `git revert --no-commit`, a partially staged
# revert. validate-all does not save it either: with the registry reverted too,
# the tree is self-consistent and every validator is green.
@test "a removal committed at HEAD is caught even when the working tree restores it" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta"
  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha","command:beta"]'

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:beta"* ]]
}

# The other half of the union, and the reason the working-tree read cannot just
# be swapped for a HEAD one: lib/ai/commit.sh consults this gate before the
# commit exists, so an uncommitted removal has to be visible or the
# commit-message prompt loses the entries it is supposed to name.
@test "an uncommitted working-tree removal is still reported" {
  _seed_base '["command:alpha","command:beta"]'
  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha"]'

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:beta"* ]]
}

# A declared removal must stay declared under the union — the HEAD side adds
# entries to the removal set, and a footer covering them has to be honoured on
# both sides or the gate fails a contributor who did everything right.
@test "a Not-Breaking footer still covers a removal both sides agree on" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "chore: unpublish beta

Not-Breaking: command:beta — never installed"
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 0 ]
}

# Deleting the snapshot wipes a whole package's surface. Treating an absent
# head-side file as "nothing to compare" made that the quietest way past the
# gate; every entry the base held is gone, so that is what it must report.
@test "deleting the working-tree snapshot reports every entry as removed" {
  _seed_base '["command:alpha","command:beta"]'
  rm "$LOCAL/public-surface.json"

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:alpha"* ]]
  [[ "$output" == *"REMOVED command:beta"* ]]
  # The package name has to come from the base snapshot — there is no
  # working-tree file left to read it from.
  [[ "$output" == *"otto-workbench"* ]]
}

@test "committing a snapshot deletion reports every entry as removed" {
  _seed_base '["command:alpha","command:beta"]'
  git -C "$LOCAL" rm -q public-surface.json
  git -C "$LOCAL" commit -m "chore: delete the snapshot" --quiet

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:alpha"* ]]
  [[ "$output" == *"REMOVED command:beta"* ]]
}

# The deletion equivalent of the restored-removal case above, and the same
# reasoning: `git push` publishes the deletion whatever the working tree holds.
# Reading an absent blob at HEAD as "no HEAD constraint" made this the quietest
# way to wipe a package's whole surface — a committed `git rm` plus a stash pop,
# an uncommitted revert, or a generator re-run that was never staged. By the
# time the HEAD side is read the merge base is known to hold the snapshot and
# HEAD descends from it, so absent there can only mean the branch deleted it.
@test "a snapshot deleted at HEAD is caught even when the working tree restores it" {
  _seed_base '["command:alpha","command:beta"]'
  git -C "$LOCAL" rm -q public-surface.json
  git -C "$LOCAL" commit -m "chore: delete the snapshot" --quiet
  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha","command:beta"]'

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:alpha"* ]]
  [[ "$output" == *"REMOVED command:beta"* ]]
}

# Replacing the snapshot with a directory is a deletion wearing a hat. `git show
# HEAD:<tree>` exits 0 and prints a tree listing, so without the type check that
# listing would reach jq and the run would end in a parse error rather than the
# verdict the surface actually deserves.
@test "a directory at the snapshot path at HEAD reports every entry as removed" {
  _seed_base '["command:alpha","command:beta"]'
  git -C "$LOCAL" rm -q public-surface.json
  mkdir -p "$LOCAL/public-surface.json"
  echo "placeholder" > "$LOCAL/public-surface.json/README"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: put a directory where the snapshot goes" --quiet

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 1 ]
  [[ "$output" == *"REMOVED command:alpha"* ]]
  [[ "$output" == *"REMOVED command:beta"* ]]
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
#
# The status is pinned to git's own 128, not merely asserted non-zero: the
# script's header tells callers they may branch on 2 vs 5 vs 128, and an
# assertion of "not 0 and not 1" makes all three interchangeable, so nothing
# guards the contract the header advertises.
@test "fails with git's own status when the base ref does not resolve" {
  _seed_base '["command:alpha","command:beta"]'
  _commit_head '["command:alpha"]' "feat: drop beta"
  run "$GATE" --repo-dir "$LOCAL" --base origin/nonexistent
  [ "$status" -eq 128 ]
  [[ "$output" == *"Could not resolve a merge base"* ]]
  [[ "$output" != *"skipping surface comparison"* ]]
}

# git writes advisories to stderr on commands that succeed. A tag and a branch
# sharing a name draws "warning: refname 'dup' is ambiguous" from a merge-base
# that exits 0, so capturing stderr into the same variable as the SHA would
# make the resolved base "warning: …\n<sha>" and kill the next git call.
@test "an ambiguous base ref does not corrupt the resolved merge base" {
  _seed_base '["command:alpha"]'
  git -C "$LOCAL" branch dup main
  git -C "$LOCAL" tag dup main

  run "$GATE" --repo-dir "$LOCAL" --base dup
  [ "$status" -eq 0 ]
  [[ "$output" == *"public surface compatible with dup"* ]]
  [[ "$output" != *"invalid object name"* ]]
}

@test "fails with git's own status when the repo dir is not a git repository" {
  mkdir -p "$TMPDIR/notarepo"
  run "$GATE" --repo-dir "$TMPDIR/notarepo"
  [ "$status" -eq 128 ]
  [[ "$output" == *"Could not resolve a merge base"* ]]
  [[ "$output" != *"skipping surface comparison"* ]]
}

# jq's status is passed through rather than translated, so this asserts the
# gate's status *is* what jq itself returns for the same input instead of
# hardcoding a number that has moved between jq releases (a parse error is not
# one of the statuses the script's header enumerates).
@test "a corrupt head snapshot exits with jq's own parse-error status" {
  _seed_base '["command:alpha","command:beta"]'
  echo 'not json at all' > "$LOCAL/public-surface.json"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: corrupt the snapshot" --quiet

  local jq_status=0
  jq -s '.' "$LOCAL/public-surface.json" >/dev/null 2>&1 || jq_status=$?
  [ "$jq_status" -ne 0 ]

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq "$jq_status" ]
  [[ "$output" != *"public surface compatible"* ]]
}

# An empty snapshot is the shape jq reads as "no input at all": the program
# never runs, jq prints nothing and exits 0, and the caller would take that
# empty capture for an empty surface — reporting either the whole base surface
# as removed or nothing at all, both lies.
@test "an empty head snapshot fails loudly instead of reading as no entries" {
  _seed_base '["command:alpha","command:beta"]'
  : > "$LOCAL/public-surface.json"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: empty the snapshot" --quiet
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 5 ]
  [[ "$output" == *"not a single JSON document"* ]]
  [[ "$output" != *"public surface compatible"* ]]
}

# The same hole one read further in. The test above is caught by the package
# read, which runs first and against the working-tree file, so it says nothing
# about the entries read — leave a valid file in the tree and the empty document
# reaches only the HEAD-side entries read, where slurping is the sole guard.
@test "an empty snapshot at HEAD fails loudly instead of reading as no entries" {
  _seed_base '["command:alpha","command:beta"]'
  : > "$LOCAL/public-surface.json"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: empty the snapshot" --quiet
  _write_snapshot "public-surface.json" "otto-workbench" '["command:alpha","command:beta"]'

  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 5 ]
  [[ "$output" == *"HEAD:public-surface.json: not a single JSON document"* ]]
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
  # 5 exactly: jq's status for an uncaught error(), passed through. "Not 0 and
  # not 1" would let this swap places with 2 or 128 unnoticed.
  [ "$status" -eq 5 ]
  [[ "$output" == *".entries is not an array"* ]]
  [[ "$output" != *"public surface compatible"* ]]
}

@test "a head snapshot with no package field fails loudly" {
  _seed_base '["command:alpha","command:beta"]'
  echo '{"entries":["command:alpha"]}' > "$LOCAL/public-surface.json"
  git -C "$LOCAL" add -A
  git -C "$LOCAL" commit -m "chore: drop the package field" --quiet
  run "$GATE" --repo-dir "$LOCAL"
  [ "$status" -eq 5 ]
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
