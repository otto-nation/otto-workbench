#!/usr/bin/env bats
# Tests for session-lock's batch run-session exemption: the pi session that
# `pr batch session` opens (WORKBENCH_RUN_DIR set *and* session id
# batch-<run-id>) records no claim on its checkout. detect.ts and index.ts
# import no SDK value, so node loads both directly.
# select-on: ai/pi/extensions/session-lock/* ai/pi/extensions/_shared/run-dir.ts bin/local/with-session-lock

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # The suite may itself run inside a run session, which exports this.
  unset WORKBENCH_RUN_DIR
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  SL="$REPO_ROOT/ai/pi/extensions/session-lock"
}

teardown() {
  common_teardown
}

# _claims ENV_JSON SESSION_ID — prints claimsWorktree(ENV, SESSION_ID).
_claims() {
  run node --input-type=module -e "
    const { claimsWorktree } = await import('$SL/detect.ts');
    process.stdout.write(String(claimsWorktree(JSON.parse(process.argv[1]), process.argv[2])));
  " -- "$1" "$2"
}

# _start_session REPO SESSION_ID — loads index.ts with a stand-in pi, fires
# session_start in REPO, and prints with-session-lock --check's exit status
# while this node process (the pid the claim names) is still alive.
_start_session() {
  run node --input-type=module -e "
    const { execFileSync } = await import('node:child_process');
    const handlers = {};
    (await import('$SL/index.ts')).default({ on: (name, fn) => { handlers[name] = fn; } });
    const repo = process.argv[1];
    await handlers.session_start({ reason: 'startup' },
      { cwd: repo, sessionManager: { getSessionId: () => process.argv[2] } });
    let code = 0;
    try {
      execFileSync('$REPO_ROOT/bin/local/with-session-lock', [repo, '--check'], { stdio: 'ignore' });
    } catch (err) {
      code = err.status;
    }
    await handlers.session_shutdown({ reason: 'quit' }, {});
    process.stdout.write(String(code));
  " -- "$1" "$2"
}

@test "session-lock: an ordinary session claims its worktree" {
  _claims '{}' 'sess-1'
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "session-lock: a blank WORKBENCH_RUN_DIR still claims" {
  _claims '{"WORKBENCH_RUN_DIR":""}' 'batch-'
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "session-lock: a pi the run session started still claims" {
  _claims '{"WORKBENCH_RUN_DIR":"/state/batch/r1"}' 'child-1'
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

@test "session-lock: the run's own session claims nothing" {
  _claims '{"WORKBENCH_RUN_DIR":"/state/batch/r1/"}' 'batch-r1'
  [ "$status" -eq 0 ]
  [ "$output" = "false" ]
}

@test "session-lock: an ordinary session's claim holds the worktree" {
  local repo="$TMPDIR/sl-plain"
  git init -q "$repo"
  _start_session "$repo" "sess-1"
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
}

@test "session-lock: a batch run session leaves the worktree unclaimed" {
  local repo="$TMPDIR/sl-run"
  git init -q "$repo"
  export WORKBENCH_RUN_DIR="$TMPDIR/batch/r1"
  _start_session "$repo" "batch-r1"
  [ "$status" -eq 0 ]
  [ "$output" = "1" ]
}
