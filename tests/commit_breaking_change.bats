#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  source_lib
  COMMITLINT_CONFIG=""
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
  run _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "_surface_removals parses REMOVED lines from the gate" {
  local fake="$BATS_TEST_TMPDIR/check-surface-compat"
  mkdir -p "$(dirname "$fake")"
  printf '#!/bin/bash\necho "REMOVED command:beta"\necho "REMOVED config:old.key"\nexit 1\n' > "$fake"
  chmod +x "$fake"
  SURFACE_GATE="$fake"
  run _surface_removals "$REPO_ROOT"
  [[ "$output" == *"command:beta"* ]]
  [[ "$output" == *"config:old.key"* ]]
  [[ "$output" != *"REMOVED"* ]]
}

@test "_surface_removals stays silent when the gate never delivers a verdict" {
  local fake="$BATS_TEST_TMPDIR/check-surface-compat"
  # exit 128 is a git failure (e.g. no merge base) — the gate aborted before it
  # could tell us whether anything was removed. Any REMOVED lines printed
  # before the abort are a partial result and must not be trusted.
  printf '#!/bin/bash\necho "REMOVED command:beta"\nexit 128\n' > "$fake"
  chmod +x "$fake"
  SURFACE_GATE="$fake"
  run _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "_surface_removals is silent when the gate path is not executable" {
  SURFACE_GATE="$BATS_TEST_TMPDIR/does-not-exist"
  run _surface_removals "$REPO_ROOT"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# ── _build_commit_prompt wiring ────────────────────────────────────────────

@test "_build_commit_prompt sends the removed-surface note to the AI when the gate flags removals" {
  local fake_gate="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\necho "REMOVED command:beta"\nexit 1\n' > "$fake_gate"
  chmod +x "$fake_gate"
  SURFACE_GATE="$fake_gate"

  mkdir -p "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/bash\ncat\n' > "$BATS_TEST_TMPDIR/bin/fake-ai"
  chmod +x "$BATS_TEST_TMPDIR/bin/fake-ai"
  AI_COMMAND="fake-ai"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  _build_commit_prompt "some diff" ""

  [[ "$AI_MSG" == *"command:beta"* ]]
  [[ "$AI_MSG" == *"$BREAKING_CHANGE_FOOTER"* ]]
}

@test "_build_commit_prompt sends no surface note when the gate is clean" {
  local fake_gate="$BATS_TEST_TMPDIR/check-surface-compat"
  printf '#!/bin/bash\nexit 0\n' > "$fake_gate"
  chmod +x "$fake_gate"
  SURFACE_GATE="$fake_gate"

  mkdir -p "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/bash\ncat\n' > "$BATS_TEST_TMPDIR/bin/fake-ai"
  chmod +x "$BATS_TEST_TMPDIR/bin/fake-ai"
  AI_COMMAND="fake-ai"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  _build_commit_prompt "some diff" ""

  [[ "$AI_MSG" != *"public surface"* ]]
}
