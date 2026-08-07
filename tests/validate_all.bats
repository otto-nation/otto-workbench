#!/usr/bin/env bats
# Tests for bin/local/validate-all — the single validator entry point.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATE_ALL="$REPO_ROOT/bin/local/validate-all"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _fixture_validator NAME EXIT_CODE — write an executable stub into the fixture
# tree that validate-all will discover via VALIDATOR_ROOT.
_fixture_validator() {
  mkdir -p "$TMPDIR/bin/local"
  printf '#!/usr/bin/env bash\nexit %s\n' "$2" > "$TMPDIR/bin/local/$1"
  chmod +x "$TMPDIR/bin/local/$1"
}

@test "validate-all discovers every validator in bin and bin/local" {
  run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]

  local expected=0
  for v in "$REPO_ROOT"/bin/validate-* "$REPO_ROOT"/bin/local/validate-*; do
    if [[ -x "$v" && "$(basename "$v")" != "validate-all" ]]; then
      expected=$(( expected + 1 ))
      echo "$output" | grep -q "$(basename "$v")"
    fi
  done
  [ "$expected" -gt 0 ]
  echo "$output" | grep -q "$expected validators passed"
}

@test "validate-all excludes itself from discovery" {
  _fixture_validator "validate-only-one" 0
  cp "$VALIDATE_ALL" "$TMPDIR/bin/local/validate-all"

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "validate-only-one"
  echo "$output" | grep -q "1 validators passed"
}

@test "validate-all fails and names the failing validator" {
  _fixture_validator "validate-good" 0
  _fixture_validator "validate-bad" 1

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL" --quiet
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "validate-bad"
  echo "$output" | grep -q "1 of 2 validators failed"
}

@test "validate-all succeeds when a tree holds no validators" {
  mkdir -p "$TMPDIR/bin/local"

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "no validators found"
}

@test "validate-all skips non-executable files" {
  mkdir -p "$TMPDIR/bin/local"
  printf 'not a script\n' > "$TMPDIR/bin/local/validate-backup.orig"
  _fixture_validator "validate-real" 0

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "1 validators passed"
}

# ── Gate wiring ──────────────────────────────────────────────────────────────
#
# Both gates must call validate-all rather than list validators themselves —
# a hardcoded list is how validate-cli-flags ran in neither for months.

@test "pre-push hook runs validators through validate-all" {
  grep -q "bin/local/validate-all" "$REPO_ROOT/git/hooks/pre-push-workbench"
  run grep -cE 'bin/(local/)?validate-(registries|components|migrations|skills|cli-flags|errexit|nesting|worktree-guards|eval-baselines)' \
    "$REPO_ROOT/git/hooks/pre-push-workbench"
  [ "$output" -eq 0 ]
}

@test "CI runs validators through validate-all" {
  grep -q "bin/local/validate-all" "$REPO_ROOT/.github/workflows/ci.yml"
  run grep -cE 'bin/(local/)?validate-(registries|components|migrations|skills|cli-flags|errexit|nesting|worktree-guards|eval-baselines)' \
    "$REPO_ROOT/.github/workflows/ci.yml"
  [ "$output" -eq 0 ]
}
