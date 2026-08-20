#!/usr/bin/env bats
# Tests for git/bin/local/generate-git-rules — the generator that renders
# ai/guidelines/rules/git.generated.md from the constants in lib/conventions.sh.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  OUT="$TMPDIR/git.generated.md"
  GENERATOR="$REPO_ROOT/git/bin/local/generate-git-rules"
  # The generator resolves its repo root from the working directory, not from
  # its own path, so the suite has to run from the repo for the real
  # conventions file and lib/ui.sh to resolve.
  cd "$REPO_ROOT" || return 1
}

teardown() {
  cd / || return 1
  rm -rf "$TMPDIR"
  common_teardown
}

# _write_conventions OMITTED — writes a conventions file carrying every
# constant the generator reads except the named one.
_write_conventions() {
  local omitted="$1" name
  : > "$TMPDIR/conventions.sh"
  for name in COMMIT_TYPES COMMIT_HEADER_MAX_LEN COMMIT_BODY_MAX_LEN \
    BREAKING_CHANGE_FOOTER NOT_BREAKING_FOOTER; do
    [[ "$name" == "$omitted" ]] && continue
    grep -E "^${name}=" "$REPO_ROOT/lib/conventions.sh" >> "$TMPDIR/conventions.sh"
  done
}

@test "generates the rules file from the real conventions" {
  run env GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 0 ]
  [ -s "$OUT" ]
}

# Both footers are rendered from lib/conventions.sh, so a rename there has to
# carry the shipped rules with it rather than leaving them naming a token the
# gate no longer accepts.
@test "the rules name both declaration footers" {
  env GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  run grep -cF "BREAKING CHANGE: <what broke>" "$OUT"
  [ "$output" -ge 1 ]
  run grep -cF "Not-Breaking: <entry> — <reason>" "$OUT"
  [ "$output" -ge 1 ]
}

# This file lands in ai/guidelines/rules/ in every repo, so a wrong claim here
# propagates the wrong mental model wider than anything else in the feature.
# The gate reads the working tree and HEAD, never the committed snapshots
# alone — see bin/local/check-surface-compat's per-package loop.
@test "the surface gate bullet describes the working tree and HEAD" {
  env GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  run grep -F "check-surface-compat" "$OUT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"working tree"* ]]
  [[ "$output" == *"HEAD"* ]]
  [[ "$output" != *"committed public surface snapshots"* ]]
}

@test "the rules say the Not-Breaking reason is required" {
  env GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  run grep -F "no reason declares nothing" "$OUT"
  [ "$status" -eq 0 ]
}

# Under `set -euo pipefail` an unguarded extraction pipeline exits 1 on a
# missing constant and errexit kills the script at the assignment, so the
# guard below it never ran and the contributor got no message at all — while
# the error line itself named an $AI_CORE_SH that no longer exists, which
# `set -u` would have turned into an unbound-variable abort had it been
# reached. One test per constant: a guard that only fires for the first name
# in the list is the shape this is guarding against.
@test "a missing COMMIT_TYPES is reported, not swallowed" {
  _write_conventions COMMIT_TYPES
  run env CONVENTIONS_SH="$TMPDIR/conventions.sh" GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not extract constants from $TMPDIR/conventions.sh"* ]]
}

@test "a missing BREAKING_CHANGE_FOOTER is reported, not swallowed" {
  _write_conventions BREAKING_CHANGE_FOOTER
  run env CONVENTIONS_SH="$TMPDIR/conventions.sh" GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not extract constants from $TMPDIR/conventions.sh"* ]]
}

@test "a missing NOT_BREAKING_FOOTER is reported, not swallowed" {
  _write_conventions NOT_BREAKING_FOOTER
  run env CONVENTIONS_SH="$TMPDIR/conventions.sh" GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not extract constants from $TMPDIR/conventions.sh"* ]]
}

# The escape hatch in _extract_const is scoped to grep's exit 1 ("no such
# constant"). A conventions file that cannot be read at all is a missing
# source of truth, not a missing constant, and must still abort.
@test "an unreadable conventions file aborts rather than reporting empty constants" {
  run env CONVENTIONS_SH="$TMPDIR/no-such-dir/conventions.sh" GIT_RULES_OUTPUT="$OUT" \
    "$GENERATOR" --quiet
  [ "$status" -ne 0 ]
  [[ "$output" != *"Generated"* ]]
}

@test "--help exits zero and documents the environment overrides" {
  run "$GENERATOR" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"CONVENTIONS_SH"* ]]
  [[ "$output" == *"GIT_RULES_OUTPUT"* ]]
}
