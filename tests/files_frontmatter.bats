#!/usr/bin/env bats
# Tests for frontmatter_field — the one frontmatter reader lib/files.sh publishes.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FIXTURE="$TMPDIR/rule.md"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

_field() {
  run bash -c '
    . "$1/lib/ui.sh"
    frontmatter_field "$2" "$3"
  ' _ "$REPO_ROOT" "$FIXTURE" "$1"
}

@test "reads a scalar value" {
  printf -- '---\nharness: claude\n---\nbody\n' > "$FIXTURE"
  _field harness
  [ "$output" = "claude" ]
}

@test "returns an inline list verbatim, brackets included" {
  # The brackets stay so an empty list is distinguishable from an absent key.
  printf -- '---\nharness: [claude, pi]\n---\nbody\n' > "$FIXTURE"
  _field harness
  [ "$output" = "[claude, pi]" ]
}

@test "reads a block sequence, one entry per line" {
  # The form every path-scoped rule in ai/guidelines/rules/ actually uses. Read
  # as a same-line value it answers empty, and every caller asking "is this rule
  # path-scoped?" silently answers no.
  printf -- '---\npaths:\n  - "**/*.sh"\n  - "bin/**"\n---\nbody\n' > "$FIXTURE"
  _field paths
  [ "${lines[0]}" = "**/*.sh" ]
  [ "${lines[1]}" = "bin/**" ]
}

@test "a block sequence with no entries answers empty" {
  printf -- '---\npaths:\n---\nbody\n' > "$FIXTURE"
  _field paths
  [ -z "$output" ]
}

@test "strips surrounding quotes but keeps the ones inside a value" {
  # Several skill descriptions carry an apostrophe. A parser that deletes every
  # quote character corrupts the prose it was only meant to unwrap.
  printf -- '---\ndescription: "Analyze a project'"'"'s codebase"\n---\nbody\n' > "$FIXTURE"
  _field description
  [ "$output" = "Analyze a project's codebase" ]
}

@test "answers empty for an absent key" {
  printf -- '---\nname: x\n---\nbody\n' > "$FIXTURE"
  _field harness
  [ -z "$output" ]
}

@test "answers empty for a file with no frontmatter" {
  printf -- '# heading\nharness: claude\n' > "$FIXTURE"
  _field harness
  [ -z "$output" ]
}

@test "ignores a --- further down the body" {
  # A horizontal rule is not a frontmatter delimiter. Reading a key out of prose
  # would scope a rule away on the strength of its own documentation.
  printf -- '---\nname: x\n---\nbody\n\n---\nharness: claude\n' > "$FIXTURE"
  _field harness
  [ -z "$output" ]
}

@test "answers empty for a file that does not exist" {
  _field harness
  [ -z "$output" ]
  [ "$status" -eq 0 ]
}

# ── rule_harness_ok ──────────────────────────────────────────────────────────

_harness_ok() {
  run bash -c '
    . "$1/lib/ui.sh"
    rule_harness_ok "$2" "$3"
  ' _ "$REPO_ROOT" "$FIXTURE" "$1"
}

@test "a rule with no harness key reaches every harness" {
  printf -- '# Plain\n' > "$FIXTURE"
  _harness_ok claude
  [ "$status" -eq 0 ]
  _harness_ok pi
  [ "$status" -eq 0 ]
}

@test "a rule naming one harness reaches only that one" {
  printf -- '---\nharness: [claude]\n---\nbody\n' > "$FIXTURE"
  _harness_ok claude
  [ "$status" -eq 0 ]
  _harness_ok pi
  [ "$status" -ne 0 ]
}

@test "a rule naming both harnesses reaches both" {
  printf -- '---\nharness: [claude, pi]\n---\nbody\n' > "$FIXTURE"
  _harness_ok claude
  [ "$status" -eq 0 ]
  _harness_ok pi
  [ "$status" -eq 0 ]
}

@test "a harness list naming nothing reaches no harness" {
  # A typo, not a declaration. validate-rules fails on it; this asserts the
  # predicate does not quietly read it as the absent-key case.
  printf -- '---\nharness: []\n---\nbody\n' > "$FIXTURE"
  _harness_ok claude
  [ "$status" -ne 0 ]
  _harness_ok pi
  [ "$status" -ne 0 ]
}

@test "a harness name is matched whole, not as a prefix" {
  printf -- '---\nharness: [pilot]\n---\nbody\n' > "$FIXTURE"
  _harness_ok pi
  [ "$status" -ne 0 ]
}
