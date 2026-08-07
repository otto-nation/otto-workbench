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

# _fixture_validator DIR NAME EXIT_CODE — write an executable stub into the
# fixture tree that validate-all will discover via VALIDATOR_ROOT. Fixtures
# rather than the real validators: running those here would double every
# pre-push validation, and the hook already runs them for real.
_fixture_validator() {
  mkdir -p "$TMPDIR/$1"
  printf '#!/usr/bin/env bash\nexit %s\n' "$3" > "$TMPDIR/$1/$2"
  chmod +x "$TMPDIR/$1/$2"
}

@test "validate-all discovers validators in both bin and bin/local" {
  _fixture_validator "bin" "validate-top" 0
  _fixture_validator "bin/local" "validate-nested" 0

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "validate-top"
  echo "$output" | grep -q "validate-nested"
  echo "$output" | grep -q "2 validators passed"
}

@test "every real validator is discovered by validate-all" {
  local expected=0
  for v in "$REPO_ROOT"/bin/validate-* "$REPO_ROOT"/bin/local/validate-*; do
    if [[ -x "$v" && "$(basename "$v")" != "validate-all" ]]; then
      expected=$(( expected + 1 ))
    fi
  done
  [ "$expected" -gt 0 ]

  # --list resolves discovery without executing anything, so the real tree is
  # covered without re-running validators the hook is about to run anyway.
  run "$VALIDATE_ALL" --list
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | wc -l | tr -d ' ')" -eq "$expected" ]
}

@test "validate-all excludes itself from discovery" {
  _fixture_validator "bin/local" "validate-only-one" 0
  cp "$VALIDATE_ALL" "$TMPDIR/bin/local/validate-all"

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "validate-only-one"
  echo "$output" | grep -q "1 validators passed"
}

@test "validate-all fails and names the failing validator" {
  _fixture_validator "bin/local" "validate-good" 0
  _fixture_validator "bin/local" "validate-bad" 1

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

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL" --list
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "validate-all skips non-executable files" {
  mkdir -p "$TMPDIR/bin/local"
  printf 'not a script\n' > "$TMPDIR/bin/local/validate-backup.orig"
  _fixture_validator "bin/local" "validate-real" 0

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
