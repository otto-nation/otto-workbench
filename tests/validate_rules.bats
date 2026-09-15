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

@test "resolves its repo root from its own path, not an inherited GIT_DIR" {
  run env GIT_DIR="$TMPDIR/nowhere" GIT_WORK_TREE="$TMPDIR/nowhere" "$VALIDATE" --help
  [ "$status" -eq 0 ]
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

@test "a claude tool name in a rule with paths: [] fails" {
  # An empty paths list scopes the rule to nothing, which is not a scope — the
  # rule still reaches Pi, the same normalization _pi_rule_reaches_pi applies.
  _rule odd.md "$(printf -- '---\npaths: []\n---\n- The TodoWrite tool')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"TodoWrite"* ]]
}

@test "permission-model reasoning in a rule that reaches Pi fails" {
  # The regression this check exists for: bash.md's Script Invocation told every
  # harness never to invoke a script by absolute path, for a reason that only
  # holds where a permission matcher reads the command.
  _rule bash.md "$(printf -- '# Bash\n\n- Never invoke scripts by absolute path. Permission rules match the first word of the command')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
  [[ "$output" == *"permission model"* ]]
}

@test "a grant shape in a rule that reaches Pi fails" {
  _rule bash.md "$(printf -- '# Bash\n\n- the grant is written `Bash(bin/*)` and only that form matches it')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
}

@test "permission-model reasoning in a claude-scoped rule passes" {
  _rule bash-tool.md "$(printf -- '---\nharness: [claude]\n---\n- An absolute path triggers a permission prompt; the grant is `Bash(bin/*)`')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 0 ]
}

@test "a line citing bash-tool.md may mention the permission model" {
  # The documented way to keep such a sentence in an unscoped rule: defer to the
  # scoped file rather than restating its reasoning. Exemption is per line, so
  # the second line here is still checked.
  _rule issue-tracker.md "$(printf -- '# Issue Tracker\n\n- Pass --repo after the subcommand; under Claude Code the other form costs a permission prompt (see bash-tool.md)')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 0 ]
}

@test "the bash-tool.md exemption does not license other lines in the file" {
  _rule issue-tracker.md "$(printf -- '# Issue Tracker\n\n- A line that cites bash-tool.md for its permission prompt\n- A later line that just says the permission matcher sees VAR=value as the command')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
}

@test "one line matching several phrases is reported once" {
  # "permission allow list" and "allow-list keys on" both match here; it is one
  # violation with one fix, so it must not be counted or printed twice.
  _rule bash.md "$(printf -- '# Bash\n\n- the permission allow list keys on per-subcommand prefixes')"

  WORKBENCH_DIR="$FAKE_WORKBENCH" run "$VALIDATE" --quiet
  [ "$status" -eq 1 ]
  [ "$(grep -c 'reasons from' <<< "$output")" -eq 1 ]
}
