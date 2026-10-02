#!/usr/bin/env bats
# The pre-push hook must let run-tests stderr stream, or the suite heartbeat
# is buffered until the run ends and slow looks like stuck again.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
}

@test "pre-push captures run-tests stdout without swallowing stderr" {
  # Combined 2>&1 on these assignments is the capture the heartbeat exists to
  # undo. Other 2>&1 in the hook (gitleaks, shellcheck) are unrelated.
  run grep -E 'bin/local/run-tests.*2>&1' "$REPO_ROOT/git/hooks/pre-push-workbench"
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

# passes-at-base: stdout capture predates streaming stderr; this change only drops 2>&1
@test "pre-push still captures run-tests stdout for TAP and pytest counts" {
  run grep -F '_bats_output=$("$REPO_ROOT/bin/local/run-tests"' \
    "$REPO_ROOT/git/hooks/pre-push-workbench"
  [ "$status" -eq 0 ]
  run grep -F '_pytest_output=$("$REPO_ROOT/bin/local/run-tests"' \
    "$REPO_ROOT/git/hooks/pre-push-workbench"
  [ "$status" -eq 0 ]
}
