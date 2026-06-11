#!/usr/bin/env bats
# Tests for lint-sweep — multi-repo lint violation sweep.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(mktemp -d)"
  LINT_SWEEP="$REPO_ROOT/bin/lint-sweep"

  # Configurable fake golangci-lint — reads from GOLANGCI_RESPONSE_FILE if set
  mkdir -p "$TEST_HOME/bin"
  cat > "$TEST_HOME/bin/golangci-lint" << 'STUB'
#!/bin/bash
if [[ -n "${GOLANGCI_RESPONSE_FILE:-}" && -f "$GOLANGCI_RESPONSE_FILE" ]]; then
  cat "$GOLANGCI_RESPONSE_FILE"
else
  echo '{"Issues":[]}'
fi
STUB
  chmod +x "$TEST_HOME/bin/golangci-lint"
  export PATH="$TEST_HOME/bin:$PATH"

  export GIT_CONFIG_GLOBAL="$TEST_HOME/.gitconfig"
  git config --global user.name "testuser"
}

teardown() {
  rm -rf "$TEST_HOME"
  common_teardown
}

_make_go_repo() {
  local name="$1"
  mkdir -p "$TEST_HOME/repos/$name"
  echo "module example.com/$name" > "$TEST_HOME/repos/$name/go.mod"
}

# ── Argument parsing ─────────────────────────────────────────────────────────

@test "lint-sweep: no args shows usage and exits 1" {
  run "$LINT_SWEEP"
  [[ "$status" -eq 1 ]]
  [[ "$output" == *"Usage:"* ]]
}

@test "lint-sweep: --help shows usage and exits 0" {
  run "$LINT_SWEEP" --help
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Usage:"* ]]
}

@test "lint-sweep: missing --rule exits 1" {
  run "$LINT_SWEEP" --repos /tmp
  [[ "$status" -eq 1 ]]
}

@test "lint-sweep: missing --repos exits 1" {
  run "$LINT_SWEEP" --rule test-rule
  [[ "$status" -eq 1 ]]
}

@test "lint-sweep: unknown flag exits 1" {
  run "$LINT_SWEEP" --rule test-rule --repos /tmp --bogus
  [[ "$status" -eq 1 ]]
}

# ── Repo resolution ──────────────────────────────────────────────────────────

@test "lint-sweep: no Go repos found exits 1" {
  mkdir -p "$TEST_HOME/repos/not-go"

  run "$LINT_SWEEP" --rule test-rule --repos "$TEST_HOME/repos/not-go"
  [[ "$status" -eq 1 ]]
}

@test "lint-sweep: skips directories without go.mod" {
  _make_go_repo "has-go"
  mkdir -p "$TEST_HOME/repos/no-go"

  run "$LINT_SWEEP" --rule test-rule --repos "$TEST_HOME/repos/has-go,$TEST_HOME/repos/no-go" --dry-run
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"has-go"* ]]
  [[ "$output" == *"1 repos"* ]]
}

# ── Dry-run ───────────────────────────────────────────────────────────────────

@test "lint-sweep: dry-run lists repos without scanning" {
  _make_go_repo "svc-a"
  _make_go_repo "svc-b"

  run "$LINT_SWEEP" --rule test-rule --repos "$TEST_HOME/repos/svc-a,$TEST_HOME/repos/svc-b" --dry-run
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"svc-a"* ]]
  [[ "$output" == *"svc-b"* ]]
  [[ "$output" == *"(dry-run)"* ]]
}

# ── Scanning ──────────────────────────────────────────────────────────────────

@test "lint-sweep: zero violations with clean repo" {
  _make_go_repo "clean-repo"

  run "$LINT_SWEEP" --rule test-rule --repos "$TEST_HOME/repos/clean-repo"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Total: 0 violations"* ]]
}

@test "lint-sweep: counts violations matching rule only" {
  _make_go_repo "dirty-repo"

  cat > "$TEST_HOME/golangci-response.json" << 'JSON'
{"Issues":[{"FromLinter":"test-rule","Text":"bad1"},{"FromLinter":"test-rule","Text":"bad2"},{"FromLinter":"other-rule","Text":"ok"}]}
JSON
  export GOLANGCI_RESPONSE_FILE="$TEST_HOME/golangci-response.json"

  run "$LINT_SWEEP" --rule test-rule --repos "$TEST_HOME/repos/dirty-repo"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"Total: 2 violations"* ]]
}

@test "lint-sweep: JSON output includes violation data" {
  _make_go_repo "json-repo"

  cat > "$TEST_HOME/golangci-response.json" << 'JSON'
{"Issues":[{"FromLinter":"test-rule","Text":"bad"}]}
JSON
  export GOLANGCI_RESPONSE_FILE="$TEST_HOME/golangci-response.json"

  run "$LINT_SWEEP" --rule test-rule --repos "$TEST_HOME/repos/json-repo" --json
  [[ "$status" -eq 0 ]]
  [[ "$output" == *'"repo"'* ]]
  [[ "$output" == *'"violations"'* ]]
}

@test "lint-sweep: multiple repos scanned independently" {
  _make_go_repo "repo-a"
  _make_go_repo "repo-b"

  run "$LINT_SWEEP" --rule test-rule --repos "$TEST_HOME/repos/repo-a,$TEST_HOME/repos/repo-b"
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"2 repos"* ]]
  [[ "$output" == *"repo-a"* ]]
  [[ "$output" == *"repo-b"* ]]
}
