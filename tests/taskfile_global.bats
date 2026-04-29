#!/usr/bin/env bats
# Tests for Taskfile.global.yml structure and env configuration.
# Validates that go-task integration works correctly.

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# ─── YAML syntax ────────────────────────────────────────────────────────────

@test "Taskfile.global.yml is valid YAML" {
  run yq '.' "$REPO_ROOT/Taskfile.global.yml"
  [ "$status" -eq 0 ]
}

# ─── env block — TASKFILE_DIR export ─────────────────────────────────────────

@test "Taskfile.global.yml exports TASKFILE_DIR as env var" {
  local val
  val=$(yq '.env.TASKFILE_DIR' "$REPO_ROOT/Taskfile.global.yml")
  [[ "$val" == '{{.TASKFILE_DIR}}' ]]
}

# ─── core.sh sourcing — TASKFILE_DIR context (simulates go-task sh -c) ──────

@test "core.sh resolves conventions.sh via TASKFILE_DIR" {
  # Simulates the go-task execution path: sh -c with TASKFILE_DIR set
  run sh -c "TASKFILE_DIR='$REPO_ROOT' . '$REPO_ROOT/lib/ai/core.sh' && echo \"\$COMMIT_TYPES\""
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat"* ]]
}

@test "core.sh sets COMMIT_HEADER_MAX_LEN via conventions.sh" {
  # Validates the full sourcing chain: core.sh → conventions.sh → constants
  run bash -c ". '$REPO_ROOT/lib/ai/core.sh' && [[ \$COMMIT_HEADER_MAX_LEN -gt 0 ]] && echo ok"
  [ "$status" -eq 0 ]
  [[ "$output" == "ok" ]]
}

# ─── core.sh sourcing — BASH_SOURCE context (bin scripts) ───────────────────

@test "core.sh resolves conventions.sh via BASH_SOURCE" {
  run bash -c ". '$REPO_ROOT/lib/ai/core.sh' && echo \"\$COMMIT_TYPES\""
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat"* ]]
}

# ─── Symlinked TASKFILE_DIR (global task install path) ───────────────────────

@test "core.sh works when sourced through symlinked TASKFILE_DIR" {
  # Simulates ~/.config/task/ setup: Taskfile.yml and lib/ are symlinks
  local fake_task_dir="$BATS_TEST_TMPDIR/fake-task-config"
  mkdir -p "$fake_task_dir"
  ln -s "$REPO_ROOT/lib" "$fake_task_dir/lib"

  run sh -c "TASKFILE_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$COMMIT_TYPES\""
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat"* ]]
}

# ─── All tasks source core.sh with TASKFILE_DIR template ────────────────────

@test "all task cmds that source core.sh use TASKFILE_DIR template variable" {
  # Every `. "{{.TASKFILE_DIR}}/lib/ai/core.sh"` must use the template var,
  # not a hardcoded path — otherwise go-task won't resolve it.
  local core_sources
  core_sources=$(grep -c '{{\.TASKFILE_DIR}}/lib/ai/core\.sh' "$REPO_ROOT/Taskfile.global.yml")
  local raw_sources
  raw_sources=$(grep -c 'lib/ai/core\.sh' "$REPO_ROOT/Taskfile.global.yml")
  # Every reference to core.sh should go through the template variable
  [ "$core_sources" -eq "$raw_sources" ]
}
