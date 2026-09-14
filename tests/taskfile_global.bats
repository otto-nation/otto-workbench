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
  local fake_task_dir
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run sh -c "TASKFILE_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$COMMIT_TYPES\""
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat"* ]]
}

@test "WORKBENCH_ROOT reaches ai/ through a symlinked TASKFILE_DIR" {
  # The install links lib/ into the checkout and nothing else, so a root taken
  # from TASKFILE_DIR itself finds conventions.sh and no ai/ script at all —
  # which is how pr:create came to invoke an ai/lib/git/push.py that wasn't there.
  #
  # dash, not sh: on macOS /bin/sh is bash, which sets BASH_SOURCE even under
  # `sh -c`, so `sh -c` here would exercise the BASH_SOURCE branch instead of
  # the TASKFILE_DIR branch this test exists to cover.
  local fake_task_dir
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run dash -c "TASKFILE_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$WORKBENCH_ROOT\""
  [ "$status" -eq 0 ]
  [ -f "$output/ai/lib/git/push.py" ]
}

# ─── All lib/ai sourcing goes through the pin ────────────────────────────────

@test "every lib/ai source line in a task body goes through \$WORKBENCH_LIB_DIR" {
  # Supersedes a count of '{{.TASKFILE_DIR}}/lib/ai/core.sh' lines against a
  # count of 'lib/ai/core.sh' lines. The property is the same one — no source
  # line escapes the indirection — but asserted over every lib/ai module
  # rather than core.sh alone, because a hardcoded path on any one of them
  # pins that module to the installed checkout while its siblings follow
  # WORKBENCH_LIB_DIR, and a branch's core.sh over main's compact_diff.sh is a
  # build neither tree has.
  #
  # yq over the task bodies, not grep over the raw YAML: the env entry that
  # validates the pin names lib/ai/core.sh twice and is not a source line, so
  # any count taken over the whole file now measures the guard as well.
  local bad
  bad=$(yq -r '.tasks[].cmds[]' "$REPO_ROOT/Taskfile.global.yml" \
    | grep -E '^[[:space:]]*\.[[:space:]]' \
    | grep '/lib/ai/' \
    | grep -v '^[[:space:]]*\. "\$WORKBENCH_LIB_DIR/lib/ai/') || true
  [ -z "$bad" ]
}

@test "the global Taskfile still sources lib/ai through the pin" {
  # Vacuity guard for the test above: a yq path that stopped matching, or a
  # grep that stopped selecting, would let it pass having examined nothing.
  local n
  n=$(yq -r '.tasks[].cmds[]' "$REPO_ROOT/Taskfile.global.yml" \
    | grep -c '^[[:space:]]*\. "\$WORKBENCH_LIB_DIR/lib/ai/') || true
  [ "$n" -gt 0 ]
}

# ─── WORKBENCH_LIB_DIR — opt-in library pin ──────────────────────────────────

@test "Taskfile.global.yml defaults WORKBENCH_LIB_DIR to TASKFILE_DIR" {
  local script
  script=$(yq '.env.WORKBENCH_LIB_DIR.sh' "$REPO_ROOT/Taskfile.global.yml")
  [[ "$script" == *'{{.TASKFILE_DIR}}'* ]]
  [[ "$script" == *'lib/ai/core.sh'* ]]
}

@test "core.sh prefers WORKBENCH_LIB_DIR over TASKFILE_DIR" {
  # dash for the same reason as the test above: /bin/sh is bash on macOS and
  # would take the BASH_SOURCE branch, which deliberately ignores the pin.
  # TASKFILE_DIR is pointed somewhere unusable, so a WORKBENCH_ROOT that still
  # reaches ai/ proves the pin was the value that resolved it.
  local fake_task_dir
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run dash -c "TASKFILE_DIR='/nonexistent' WORKBENCH_LIB_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$WORKBENCH_ROOT\""
  [ "$status" -eq 0 ]
  [ -f "$output/ai/lib/git/push.py" ]
}

@test "core.sh falls back to TASKFILE_DIR when WORKBENCH_LIB_DIR is unset" {
  # The default path, asserted directly: an unset pin must resolve exactly what
  # TASKFILE_DIR resolved before the variable existed.
  local fake_task_dir
  fake_task_dir=$(make_fake_task_dir "$REPO_ROOT")

  run dash -c "unset WORKBENCH_LIB_DIR; TASKFILE_DIR='$fake_task_dir' . '$fake_task_dir/lib/ai/core.sh' && echo \"\$WORKBENCH_ROOT\""
  [ "$status" -eq 0 ]
  [ -f "$output/ai/lib/git/push.py" ]
}

@test "core.sh ignores WORKBENCH_LIB_DIR when BASH_SOURCE resolves the path" {
  # A stale pin in an interactive environment must not redirect a bash caller
  # away from the file it just sourced.
  run bash -c "WORKBENCH_LIB_DIR='/nonexistent' . '$REPO_ROOT/lib/ai/core.sh' && echo \"\$WORKBENCH_ROOT\""
  [ "$status" -eq 0 ]
  [ "$output" = "$REPO_ROOT" ]
}

@test "commit.sh and review.sh honour the same pin as core.sh" {
  # Neither has a runnable test for its fallback branch: compact_diff.sh uses
  # `local chunks=()`, which dash cannot parse, and bash always sets
  # BASH_SOURCE — so that branch only ever executes under go-task's shell.
  # A textual assertion is the honest cover, and it catches the regression
  # that matters: a module left on TASKFILE_DIR while core.sh follows the pin.
  grep -q '${WORKBENCH_LIB_DIR:-${TASKFILE_DIR' "$REPO_ROOT/lib/ai/commit.sh"
  grep -q '${WORKBENCH_LIB_DIR:-${TASKFILE_DIR' "$REPO_ROOT/lib/ai/review.sh"
}

@test "a bogus WORKBENCH_LIB_DIR fails the task by name" {
  command -v task >/dev/null 2>&1 || skip "task not installed"
  run task --taskfile "$REPO_ROOT/Taskfile.global.yml" \
    "WORKBENCH_LIB_DIR=$BATS_TEST_TMPDIR/nope" commit
  [ "$status" -ne 0 ]
  [[ "$output" == *"WORKBENCH_LIB_DIR"* ]]
}

@test "a relative WORKBENCH_LIB_DIR is refused" {
  command -v task >/dev/null 2>&1 || skip "task not installed"
  run task --taskfile "$REPO_ROOT/Taskfile.global.yml" \
    "WORKBENCH_LIB_DIR=relative/path" commit
  [ "$status" -ne 0 ]
  [[ "$output" == *"absolute path"* ]]
}
