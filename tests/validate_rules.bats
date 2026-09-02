#!/usr/bin/env bats
# Tests for bin/local/validate-rules.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH/ai/guidelines/rules"
  VALIDATE="$REPO_ROOT/bin/local/validate-rules"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

_rule() {
  printf -- '%s\n' "$2" > "$FAKE_WORKBENCH/ai/guidelines/rules/$1"
}

@test "a plain rule passes" {
  _rule general.md "# General"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 0 ]
}

@test "a known harness value passes" {
  _rule bash-tool.md "$(printf -- '---\nharness: [claude]\n---\n# Bash Tool')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 0 ]
}

@test "an unknown harness value fails" {
  _rule odd.md "$(printf -- '---\nharness: [cursor]\n---\n# Odd')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"cursor"* ]]
}

@test "a harness list naming no harness fails" {
  # The typo that scopes a rule to nothing and is invisible everywhere else.
  _rule odd.md "$(printf -- '---\nharness: []\n---\n# Odd')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
}

@test "a block-form harness key with no entries fails" {
  # The same typo in the other YAML form, and the more dangerous one: a block
  # sequence with no entries reads back identically to an absent key, so
  # rule_harness_ok lets the rule reach every harness rather than none. Only a
  # check that reads the key line itself can tell the two apart.
  _rule odd.md "$(printf -- '---\nharness:\n---\n# Odd')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
}

@test "a claude tool name in an always-on rule fails" {
  _rule general.md "$(printf -- '# General\n\n- Use the TodoWrite tool to track work')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"TodoWrite"* ]]
}

@test "a claude tool name in a claude-scoped rule passes" {
  _rule bash-tool.md "$(printf -- '---\nharness: [claude]\n---\n- The TodoWrite tool')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 0 ]
}

@test "a claude tool name in a path-scoped rule passes" {
  # Path-scoped rules never reach Pi, so the vocabulary check does not apply.
  # Block-sequence form deliberately — it is what every path-scoped rule in
  # ai/guidelines/rules/ is written as.
  _rule go.md "$(printf -- '---\npaths:\n  - "**/*.go"\n---\n- The TodoWrite tool')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 0 ]
}
