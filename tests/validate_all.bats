#!/usr/bin/env bats
# Tests for bin/local/validate-all — the single validator entry point.

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "validate-all discovers every validator in bin and bin/local" {
  run "$REPO_ROOT/bin/local/validate-all"
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

@test "validate-all does not recurse into itself" {
  run "$REPO_ROOT/bin/local/validate-all" --quiet
  [ "$status" -eq 0 ]
  run grep -c "validate-all" <<< "$output"
  [ "$output" -eq 0 ]
}

@test "validate-all fails when a validator fails" {
  local fake="$REPO_ROOT/bin/local/validate-zz-bats-fixture"
  printf '#!/usr/bin/env bash\nexit 1\n' > "$fake"
  chmod +x "$fake"

  run "$REPO_ROOT/bin/local/validate-all" --quiet
  rm -f "$fake"

  [ "$status" -eq 1 ]
  echo "$output" | grep -q "validate-zz-bats-fixture"
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
