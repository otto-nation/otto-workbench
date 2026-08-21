#!/usr/bin/env bats
# Tests for git/bin/local/generate-git-rules — the generator that renders
# ai/guidelines/rules/git.generated.md from the constants in lib/conventions.sh.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  OUT="$TMPDIR/git.generated.md"
  GENERATOR="$REPO_ROOT/git/bin/local/generate-git-rules"
  # The synonym a fixture conventions file declares. Deliberately not the
  # hyphenation of BREAKING_CHANGE_FOOTER: no re-derivation of the base token
  # can produce it, so a rules file naming it can only have read it.
  FIXTURE_SYNONYM="FIXTURE-SYNONYM"
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

# _write_conventions [OMITTED] — writes a conventions file carrying every
# constant the generator reads except the named one. With no argument nothing
# is omitted, which is the fixture for reading a value back out of the rules.
_write_conventions() {
  local omitted="${1:-}" name
  : > "$TMPDIR/conventions.sh"
  for name in COMMIT_TYPES COMMIT_HEADER_MAX_LEN COMMIT_BODY_MAX_LEN \
    BREAKING_CHANGE_FOOTER NOT_BREAKING_FOOTER; do
    [[ "$name" == "$omitted" ]] && continue
    grep -E "^${name}=" "$REPO_ROOT/lib/conventions.sh" >> "$TMPDIR/conventions.sh"
  done
  # The real file derives the synonym from BREAKING_CHANGE_FOOTER, so copying
  # that line would leave the fixture omitting the base unsourceable. A literal
  # independent of the base stands in for it.
  if [[ "$omitted" != "BREAKING_CHANGE_FOOTER_ALT" ]]; then
    printf 'BREAKING_CHANGE_FOOTER_ALT="%s"\n' "$FIXTURE_SYNONYM" >> "$TMPDIR/conventions.sh"
  fi
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

# The hyphenated spelling is a Conventional Commits synonym that release-please
# reads and the gate accepts, so the shipped rules have to name it too.
@test "the rules name the hyphenated synonym of the breaking change footer" {
  env GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  run grep -cF "BREAKING-CHANGE:" "$OUT"
  [ "$output" -ge 1 ]
}

# lib/conventions.sh owns the synonym; the generator renders whatever it finds
# there instead of hyphenating BREAKING_CHANGE_FOOTER for itself. A fixture
# synonym that no hyphenation of the base could produce is what tells the two
# apart — re-derivation would render BREAKING-CHANGE and miss it entirely.
@test "the synonym comes from conventions.sh, not from a second derivation" {
  _write_conventions
  run env CONVENTIONS_SH="$TMPDIR/conventions.sh" GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 0 ]
  run grep -cF "$FIXTURE_SYNONYM:" "$OUT"
  [ "$output" -ge 1 ]
}

# The `!` marker is lost to a squash merge on a multi-commit PR, so the shipped
# rules must not present it as an alternative to the footer.
@test "the rules say the bang marker never replaces the footer" {
  env GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  run grep -F "never replaces the footer" "$OUT"
  [ "$status" -eq 0 ]
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

# A constant the conventions file never defined must be reported rather than
# rendered as a blank bullet in rules that ship to every repo. One test per
# constant: a guard that only fires for the first name in the list is the shape
# this is guarding against.
@test "a missing COMMIT_TYPES is reported, not swallowed" {
  _write_conventions COMMIT_TYPES
  run env CONVENTIONS_SH="$TMPDIR/conventions.sh" GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not read constants from $TMPDIR/conventions.sh"* ]]
}

@test "a missing BREAKING_CHANGE_FOOTER is reported, not swallowed" {
  _write_conventions BREAKING_CHANGE_FOOTER
  run env CONVENTIONS_SH="$TMPDIR/conventions.sh" GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not read constants from $TMPDIR/conventions.sh"* ]]
}

# The synonym is a constant of the conventions file like any other, so its
# absence is a missing source of truth rather than something the generator
# quietly makes up from the base token.
@test "a missing BREAKING_CHANGE_FOOTER_ALT is reported, not swallowed" {
  _write_conventions BREAKING_CHANGE_FOOTER_ALT
  run env CONVENTIONS_SH="$TMPDIR/conventions.sh" GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not read constants from $TMPDIR/conventions.sh"* ]]
}

@test "a missing NOT_BREAKING_FOOTER is reported, not swallowed" {
  _write_conventions NOT_BREAKING_FOOTER
  run env CONVENTIONS_SH="$TMPDIR/conventions.sh" GIT_RULES_OUTPUT="$OUT" "$GENERATOR" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not read constants from $TMPDIR/conventions.sh"* ]]
}

# A conventions file that cannot be read at all is a missing source of truth,
# not a missing constant, and must abort before anything is written.
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
