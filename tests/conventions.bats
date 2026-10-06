#!/usr/bin/env bats
# Tests for lib/conventions.sh — the commit-convention SSOT that
# check-surface-compat and the git generators source.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/conventions.sh
  . "$REPO_ROOT/lib/conventions.sh"
}

teardown() {
  common_teardown
}

@test "conventions.sh defines BREAKING_CHANGE_FOOTER" {
  [[ "$BREAKING_CHANGE_FOOTER" == "BREAKING CHANGE" ]]
}

@test "conventions.sh defines NOT_BREAKING_FOOTER" {
  [[ "$NOT_BREAKING_FOOTER" == "Not-Breaking" ]]
}

@test "conventions.sh derives the hyphenated footer synonym" {
  [[ "$BREAKING_CHANGE_FOOTER_ALT" == "BREAKING-CHANGE" ]]
}

# No current caller is POSIX sh; the file stays POSIX and this case holds it.
@test "conventions.sh sources under a POSIX shell" {
  run sh -c ". '$REPO_ROOT/lib/conventions.sh' && printf '%s' \"\$BREAKING_CHANGE_FOOTER_ALT\""
  [ "$status" -eq 0 ]
  [[ "$output" == "BREAKING-CHANGE" ]]
}

# ── has_breaking_footer ────────────────────────────────────────────────────

@test "has_breaking_footer finds a BREAKING CHANGE footer in the body" {
  run has_breaking_footer "feat!: drop the legacy flag

BREAKING CHANGE: --legacy is gone; use --modern"
  [ "$status" -eq 0 ]
}

@test "has_breaking_footer accepts the hyphenated spelling" {
  run has_breaking_footer "feat!: drop the legacy flag

BREAKING-CHANGE: --legacy is gone; use --modern"
  [ "$status" -eq 0 ]
}

@test "has_breaking_footer rejects a footer with no reason after it" {
  run has_breaking_footer "feat!: drop the legacy flag

BREAKING CHANGE:"
  [ "$status" -eq 1 ]
}

@test "has_breaking_footer ignores the header's bang marker" {
  run has_breaking_footer "feat!: drop the legacy flag"
  [ "$status" -eq 1 ]
}
