#!/usr/bin/env bats
# Tests for bin/local/validate-doc-budget — the gate that keeps a hand-written
# guide from regrowing the module behaviour that was moved out of it.
#
# Every failing case runs against a copy of the real docs/ in TMPDIR, reached
# through DOCS_DIR, so the budgets under test are the ones the repo ships.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATOR="$REPO_ROOT/bin/local/validate-doc-budget"
  FIXTURE="$TMPDIR/docs"
  mkdir -p "$FIXTURE"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _copy_docs — the real source docs, which are within their budgets by construction.
_copy_docs() {
  cp "$REPO_ROOT"/docs/*.src.md "$FIXTURE/"
}

# _pad DOC N — append N filler lines to a fixture doc.
_pad() {
  local i
  for ((i = 0; i < $2; i++)); do echo "filler" >> "$FIXTURE/$1"; done
}

@test "the workbench docs are within their budgets" {
  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "success names how many docs were checked" {
  _copy_docs
  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qE "within its rules \([0-9]+ checked\)"
}

@test "fails when a budgeted doc grows past its budget" {
  _copy_docs
  _pad ai-automation.src.md 500

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "ai-automation.src.md: [0-9]* lines, over its budget of"
  echo "$output" | grep -q "belongs in that module's"
}

@test "a doc sitting exactly on its budget passes" {
  printf -- '<!-- doc-budget: 3 -->\none\ntwo\n' > "$FIXTURE/exact.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "one line over the budget fails" {
  printf -- '<!-- doc-budget: 3 -->\none\ntwo\nthree\n' > "$FIXTURE/over.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "4 lines, over its budget of 3"
}

@test "counts a final line that carries no newline" {
  # `wc -l` counts newlines, so a file ending mid-line reads one line short and
  # slips past a budget it has already exceeded.
  printf -- '<!-- doc-budget: 3 -->\none\ntwo\nthree' > "$FIXTURE/nonewline.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "4 lines, over its budget of 3"
}

@test "checks every budgeted doc rather than stopping at the first failure" {
  printf -- '<!-- doc-budget: 1 -->\nover\n' > "$FIXTURE/a.src.md"
  printf -- '<!-- doc-budget: 1 -->\n' > "$FIXTURE/b.src.md"
  printf -- '<!-- doc-budget: 1 -->\nover\n' > "$FIXTURE/c.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "a.src.md: 2 lines"
  echo "$output" | grep -q "c.src.md: 2 lines"
}

@test "fails on a '####' heading in a budgeted doc" {
  _copy_docs
  echo '#### A skill manifest'"'"'s fields' >> "$FIXTURE/ai-automation.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "'####' heading — #### A skill manifest's fields"
  echo "$output" | grep -q "Move the section to the docstring"
}

@test "reports the line a '####' heading is on" {
  _copy_docs
  echo '#### Stubbing the CLIs' >> "$FIXTURE/ai-automation.src.md"
  # Not `lines`: bats overwrites that with the output array when `run` returns.
  heading_line="$(wc -l < "$FIXTURE/ai-automation.src.md" | tr -d ' ')"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "ai-automation.src.md:$heading_line:"
}

@test "ignores a '####' inside a fenced code block" {
  _copy_docs
  # A shell comment in a bash fence, not a heading — the guide already carries
  # one of these, and a naive scan would fail the file it is meant to protect.
  printf '\n```bash\n#### not a heading\n```\n' >> "$FIXTURE/ai-automation.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "counts every '####' rather than stopping at the first" {
  _copy_docs
  echo '#### One' >> "$FIXTURE/ai-automation.src.md"
  echo '#### Two' >> "$FIXTURE/ai-automation.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "#### One"
  echo "$output" | grep -q "#### Two"
}

@test "skips a doc that declares no budget" {
  _copy_docs
  # tools.src.md is a generated reference with '####' headings of its own and no
  # marker — the rules apply to the docs that opt in, not to every source doc.
  _pad tools.src.md 500

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "fails when no doc declares a budget at all" {
  # Deleting the marker is the one way to make this validator vacuous, so it is
  # the case that has to fail rather than pass silently.
  cp "$REPO_ROOT/docs/tools.src.md" "$FIXTURE/"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "declares a budget"
}

@test "--quiet prints nothing on success" {
  _copy_docs
  DOCS_DIR="$FIXTURE" run "$VALIDATOR" --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "--quiet still reports a failure" {
  _copy_docs
  _pad ai-automation.src.md 500

  # validate-all invokes every validator with --quiet, so a failure the flag
  # swallowed would reach the summary as a bare name and no reason.
  DOCS_DIR="$FIXTURE" run "$VALIDATOR" --quiet
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "over its budget of"
}

@test "--help prints usage and exits 0" {
  run "$VALIDATOR" --help
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "Usage: validate-doc-budget"
}

@test "rejects an unknown flag with exit 2" {
  run "$VALIDATOR" --nope
  [ "$status" -eq 2 ]
  echo "$output" | grep -q "Unknown argument: --nope"
}
