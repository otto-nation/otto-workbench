#!/usr/bin/env bats
# Tests for lib/output.sh — the sed helpers every formatting call site goes
# through. The logging helpers are covered by tests/ui.bats via the facade.

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  # shellcheck source=../lib/output.sh
  source "$REPO_ROOT/lib/output.sh"
}

teardown() {
  common_teardown
}

# ── indent ──────────────────────────────────────────────────────────────────

@test "indent prefixes every line, not just the first" {
  run indent '    ' <<<$'alpha\nbeta\ngamma'
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "    alpha" ]
  [ "${lines[1]}" = "    beta" ]
  [ "${lines[2]}" = "    gamma" ]
  [ "${#lines[@]}" -eq 3 ]
}

@test "indent takes a marker word as readily as whitespace" {
  # bin/local/check-surface-compat prints its machine-readable removals this
  # way, so a prefix is not always an indent.
  run indent 'REMOVED ' <<<$'command:pr\nconfig:review.model'
  [ "${lines[0]}" = "REMOVED command:pr" ]
  [ "${lines[1]}" = "REMOVED config:review.model" ]
}

@test "indent prefixes a blank line too" {
  # A blank line inside a block still belongs to the block; dropping its prefix
  # would break the indentation of anything that re-reads the output.
  run indent '..' <<<$'alpha\n\nbeta'
  [ "${lines[0]}" = "..alpha" ]
  [ "${lines[1]}" = ".." ]
  [ "${lines[2]}" = "..beta" ]
}

@test "indent leaves already-indented text alone beyond its own prefix" {
  run indent '  ' <<<$'top\n  nested'
  [ "${lines[0]}" = "  top" ]
  [ "${lines[1]}" = "    nested" ]
}

@test "indent passes empty input through as empty output" {
  run indent '  ' < /dev/null
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "indent reads a file on stdin, not a filename argument" {
  # The redirect form is what bin/local/check-surface-compat uses for its
  # merge-base error file; a second argument must not be read as a path.
  printf 'one\ntwo\n' > "$BATS_TEST_TMPDIR/block"
  run indent '> ' < "$BATS_TEST_TMPDIR/block"
  [ "${lines[0]}" = "> one" ]
  [ "${lines[1]}" = "> two" ]
}

@test "indent prefixes a final line that carries no trailing newline" {
  # Whether that line comes back terminated is BSD-vs-GNU and deliberately not
  # asserted — BSD sed preserves the missing newline, GNU sed supplies one. The
  # prefix is the contract; every call site either redirects to a terminal or
  # sits in a command substitution, both of which are indifferent to it.
  printf 'alpha\nbeta' | indent '# ' > "$BATS_TEST_TMPDIR/out"
  [ "$(cat "$BATS_TEST_TMPDIR/out")" = $'# alpha\n# beta' ]
}
