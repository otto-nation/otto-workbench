#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  source_lib
  COMMITLINT_CONFIG=""
  # Production always calls this before generate_commit_msg (Taskfile.global.yml)
  # — call it here too so COMMIT_RULES reflects the real prompt shape the
  # wiring tests below inspect.
  build_commit_rules
}

@test "conventions.sh defines BREAKING_CHANGE_FOOTER" {
  [[ "$BREAKING_CHANGE_FOOTER" == "BREAKING CHANGE" ]]
}

@test "conventions.sh defines NOT_BREAKING_FOOTER" {
  [[ "$NOT_BREAKING_FOOTER" == "Not-Breaking" ]]
}

@test "validate_commit_msg accepts a bang marker in the header" {
  run validate_commit_msg "feat!: drop the legacy flag"
  [ "$status" -eq 0 ]
}

@test "validate_commit_msg accepts a bang marker with a scope" {
  run validate_commit_msg "feat(pr)!: rename --post to --publish"
  [ "$status" -eq 0 ]
}

@test "validate_commit_msg still rejects a non-conventional header" {
  run validate_commit_msg "just some words"
  [ "$status" -eq 1 ]
}

@test "validate_commit_msg still rejects an unknown type with a bang" {
  run validate_commit_msg "wibble!: not a real type"
  [ "$status" -eq 1 ]
}

# ── _surface_removals ──────────────────────────────────────────────────────

@test "_surface_removals is empty when the gate reports nothing" {
  local fake="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\nexit 0\n' > "$fake"
  chmod +x "$fake"
  WORKBENCH_SURFACE_GATE="$fake"
  run --separate-stderr _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "_surface_removals parses REMOVED lines from the gate" {
  local fake="$BATS_TEST_TMPDIR/check-surface-compat"
  mkdir -p "$(dirname "$fake")"
  printf '#!/bin/bash\necho "REMOVED command:beta"\necho "REMOVED config:old.key"\nexit 1\n' > "$fake"
  chmod +x "$fake"
  WORKBENCH_SURFACE_GATE="$fake"
  run --separate-stderr _surface_removals "$REPO_ROOT"
  [[ "$output" == *"command:beta"* ]]
  [[ "$output" == *"config:old.key"* ]]
  [[ "$output" != *"REMOVED"* ]]
}

@test "_surface_removals discards a partial REMOVED list when the gate aborts mid-run" {
  local fake="$BATS_TEST_TMPDIR/check-surface-compat"
  # exit 128 is a git failure (e.g. no merge base) — the gate aborted before it
  # could tell us whether anything was removed. Any REMOVED lines printed
  # before the abort are a partial result and must not be trusted, but the
  # abort itself must not vanish without a trace either.
  printf '#!/bin/bash\necho "REMOVED command:beta"\nexit 128\n' > "$fake"
  chmod +x "$fake"
  WORKBENCH_SURFACE_GATE="$fake"
  run --separate-stderr _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [[ "$stderr" == *"exited 128"* ]]
}

@test "_surface_removals is silent when the gate path is not executable" {
  WORKBENCH_SURFACE_GATE="$BATS_TEST_TMPDIR/does-not-exist"
  run --separate-stderr _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

# ── _build_commit_prompt wiring ────────────────────────────────────────────

@test "_build_commit_prompt sends the removed-surface note to the AI when the gate flags removals" {
  local fake_gate="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\necho "REMOVED command:beta"\nexit 1\n' > "$fake_gate"
  chmod +x "$fake_gate"
  WORKBENCH_SURFACE_GATE="$fake_gate"

  mkdir -p "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/bash\ncat\n' > "$BATS_TEST_TMPDIR/bin/fake-ai"
  chmod +x "$BATS_TEST_TMPDIR/bin/fake-ai"
  AI_COMMAND="fake-ai"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  _build_commit_prompt "some diff" ""

  [[ "$AI_MSG" == *"command:beta"* ]]
  # $BREAKING_CHANGE_FOOTER alone is a weak assertion here: it's already in
  # COMMIT_RULES's fallback bullet regardless of the note. $NOT_BREAKING_FOOTER
  # only ever appears via surface_note, so it actually proves the note landed.
  [[ "$AI_MSG" == *"$NOT_BREAKING_FOOTER"* ]]

  # The note must sit next to the rules, before the diff — not orphaned after
  # it, which is the placement the brief specifically warned against.
  local before_diff="${AI_MSG%%Diff:*}"
  [[ "$before_diff" == *"command:beta"* ]]
}

@test "_build_commit_prompt sends no surface note when the gate is clean" {
  local fake_gate="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\nexit 0\n' > "$fake_gate"
  chmod +x "$fake_gate"
  WORKBENCH_SURFACE_GATE="$fake_gate"

  mkdir -p "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/bash\ncat\n' > "$BATS_TEST_TMPDIR/bin/fake-ai"
  chmod +x "$BATS_TEST_TMPDIR/bin/fake-ai"
  AI_COMMAND="fake-ai"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  _build_commit_prompt "some diff" ""

  # "public surface" alone is a weak sentinel: COMMIT_RULES's fallback bullet
  # ("...anything on the public surface...") contains it independent of
  # surface_note. "removes the following entries" only ever comes from the note.
  [[ "$AI_MSG" != *"removes the following entries"* ]]
}
