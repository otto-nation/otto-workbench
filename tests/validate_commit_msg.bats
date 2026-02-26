#!/usr/bin/env bats

setup() {
  load 'test_helper'
  source_lib
  # Use fallback path (no commitlint config, no npx) for all tests
  COMMITLINT_CONFIG=""
  # Hide npx so the fallback branch is always exercised
  PATH_ORIG="$PATH"
  PATH="$(echo "$PATH" | tr ':' '\n' | grep -v 'npm\|node' | tr '\n' ':' | sed 's/:$//')"
}

teardown() {
  PATH="$PATH_ORIG"
}

# ── Valid messages ────────────────────────────────────────────────────────────

@test "passes: feat commit" {
  run validate_commit_msg "feat: add retry logic"
  [ "$status" -eq 0 ]
}

@test "passes: fix commit with scope" {
  run validate_commit_msg "fix(auth): resolve token expiry"
  [ "$status" -eq 0 ]
}

@test "passes: commit with multi-line body" {
  run validate_commit_msg "feat: add retry logic

- retries once on length violation
- includes specific error feedback"
  [ "$status" -eq 0 ]
}

@test "passes: all standard types" {
  local types=(feat fix perf deps revert docs style refactor test build ci chore)
  for type in "${types[@]}"; do
    run validate_commit_msg "$type: short subject"
    [ "$status" -eq 0 ]
  done
}

@test "passes: header exactly 72 characters" {
  # "feat: " = 6 chars; subject padded to fill to exactly 72
  local subject
  subject="$(printf '%-66s' 'x' | tr ' ' 'x')"
  run validate_commit_msg "feat: $subject"
  [ "$status" -eq 0 ]
}

# ── Header length violations ──────────────────────────────────────────────────

@test "fails: header is 73 characters" {
  local subject
  subject="$(printf '%-67s' 'x' | tr ' ' 'x')"
  run validate_commit_msg "feat: $subject"
  [ "$status" -eq 1 ]
  [[ "$output" == *"Header is"* ]]
  [[ "$output" == *"max 72"* ]]
}

@test "fails: long scope pushes header over 72 chars" {
  run validate_commit_msg "fix(very-long-scope-name-here): resolve something important that is too long"
  [ "$status" -eq 1 ]
}

# ── Format violations ─────────────────────────────────────────────────────────

@test "fails: no type prefix" {
  run validate_commit_msg "update stuff in the codebase"
  [ "$status" -eq 1 ]
  [[ "$output" == *"conventional commit format"* ]]
}

@test "fails: unknown type" {
  run validate_commit_msg "update: some change"
  [ "$status" -eq 1 ]
  [[ "$output" == *"conventional commit format"* ]]
}

@test "fails: missing colon" {
  run validate_commit_msg "feat add something"
  [ "$status" -eq 1 ]
}

@test "fails: missing subject after colon" {
  run validate_commit_msg "feat: "
  [ "$status" -eq 1 ]
}

@test "fails: empty message" {
  run validate_commit_msg ""
  [ "$status" -eq 1 ]
}
