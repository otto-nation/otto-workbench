#!/usr/bin/env bats

setup() {
  load 'test_helper'
  source_lib
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  cd "$TMPDIR"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
}

# ── Fallback rules (no commitlint config) ─────────────────────────────────────

@test "uses fallback rules when COMMITLINT_CONFIG is empty" {
  COMMITLINT_CONFIG=""
  build_commit_rules
  [[ "$COMMIT_RULES" == *"conventional commit"* ]]
}

@test "fallback rules include every type from COMMIT_TYPES" {
  COMMITLINT_CONFIG=""
  build_commit_rules
  for type in $COMMIT_TYPES; do
    [[ "$COMMIT_RULES" == *"$type"* ]]
  done
}

# ── Config-based rules ────────────────────────────────────────────────────────

@test "uses config file content when COMMITLINT_CONFIG is set" {
  echo '{"rules":{"type-enum":[2,"always",["feat","fix"]]}}' > commitlint.config.json
  COMMITLINT_CONFIG="commitlint.config.json"
  build_commit_rules
  [[ "$COMMIT_RULES" == *"type-enum"* ]]
}
