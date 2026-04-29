#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  cd "$TMPDIR"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  common_teardown
}

# ── Conventions sourcing (lib/conventions.sh via lib/ai/core.sh) ──────────────

@test "conventions.sh is sourced by core.sh and defines COMMIT_TYPES" {
  [[ -n "$COMMIT_TYPES" ]]
}

@test "conventions.sh is sourced by core.sh and defines COMMIT_HEADER_MAX_LEN" {
  [[ -n "$COMMIT_HEADER_MAX_LEN" ]]
  [[ "$COMMIT_HEADER_MAX_LEN" -gt 0 ]]
}

@test "core.sh can be sourced with TASKFILE_DIR (Taskfile context)" {
  # Simulates how go-task sources core.sh: TASKFILE_DIR is set, sh -c is used
  run sh -c "TASKFILE_DIR='$REPO_ROOT' . '$REPO_ROOT/lib/ai/core.sh' && echo \"\$COMMIT_TYPES\""
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat"* ]]
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
