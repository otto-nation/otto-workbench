#!/usr/bin/env bats
# Taskfile test targets must declare the tree via run-tests, not invoke
# bats or pytest directly.

setup() {
  load 'test_helper'
  common_setup
  TASKFILE="$REPO_ROOT/Taskfile.yml"
  [ -f "$TASKFILE" ]
}

@test "no task target invokes bats directly" {
  # Every suite must go through run-tests, which declares the tree. A direct
  # bats call is a validation nothing can see.
  run grep -nE '^[[:space:]]+- bats ' "$TASKFILE"
  [ "$status" -ne 0 ]
}

@test "no task target invokes pytest directly" {
  run grep -nE '^[[:space:]]+- pytest ' "$TASKFILE"
  [ "$status" -ne 0 ]
}

# ── gate ──────────────────────────────────────────────────────────────
#
# `task gate` exists so the whole sequence has a name to call it by. The point
# of the target is that the hook stays its single owner, so what is asserted is
# the delegation — a target that grew its own copy of the steps would satisfy
# "runs a gate" and be the exact regression this guards.

@test "gate delegates to the pre-push hook" {
  run yq -r '.tasks.gate.cmds[0]' "$TASKFILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"git/hooks/pre-push-workbench"* ]]
}

@test "gate does not re-list the hook's steps" {
  # The hook sequences validate-all, check-surface-compat, shellcheck and both
  # suites. Naming any of them here is a second definition of green.
  run yq -r '.tasks.gate.cmds[]' "$TASKFILE"
  [ "$status" -eq 0 ]
  run grep -cE 'validate-all|check-surface-compat|run-tests|gitleaks' <<< "$output"
  [ "$output" -eq 0 ]
}

@test "gate is a single command" {
  # One delegation. A second cmd is how the sequence starts being reassembled
  # here — the hook is the whole gate, not the first step of one.
  run yq -r '.tasks.gate.cmds | length' "$TASKFILE"
  [ "$status" -eq 0 ]
  [ "$output" -eq 1 ]
}

@test "the hook reads nothing from stdin that a task call cannot supply" {
  # Git hands a pre-push hook its ref lines on stdin; `task gate` has none to
  # hand it, which is why the target redirects from /dev/null. That is only
  # safe while the hook ignores stdin, so the condition is asserted rather than
  # assumed — a hook that started consuming it would read EOF under `task gate`
  # and behave as though nothing were being pushed.
  #
  # Scoped to reads of the script's own stdin. `$1` inside the hook's helper
  # functions is a function parameter and says nothing about the script's
  # arguments, so matching a bare `$1` here would assert something false.
  run grep -nE '(read[[:space:]][^|]*<&0|</dev/stdin|\$\(cat\)|read[[:space:]]+-r?[[:space:]]*(local|remote)_(ref|sha))' \
    "$REPO_ROOT/git/hooks/pre-push-workbench"
  [ "$status" -ne 0 ]
}

@test "gate offers no way to skip the slow suites" {
  # A gate with a documented skip flag is a gate people run with the flag. The
  # parts are individually reachable through `task test` and `task lint`;
  # `gate` is the whole thing or nothing.
  run yq -r '.tasks.gate.cmds[]' "$TASKFILE"
  [ "$status" -eq 0 ]
  [[ "$output" != *"CLI_ARGS"* ]]
  [[ "$output" != *"SKIP"* ]]
}

@test "test:* routes a single file through run-tests" {
  # Filename must be the VALUE of --files, not an argument after a bare --.
  # Passing it after -- would run the entire tests/ tree plus a stray
  # argument. Matching only the substrings run-tests and --files would still
  # pass that regression.
  run yq -r '.tasks."test:*".cmds[0]' "$TASKFILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"run-tests"* ]]
  [[ "$output" == *'--files "tests/{{.FILENAME}}"'* ]]
}
