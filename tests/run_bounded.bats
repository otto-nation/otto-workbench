#!/usr/bin/env bats
# run_bounded: one way to run a captured command with a heartbeat and a hard bound.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/run_bounded.sh
  . "$REPO_ROOT/lib/run_bounded.sh"
  OUT="$BATS_TEST_TMPDIR/out"
}

@test "returns the command's status and keeps its output" {
  run run_bounded 10 0 step "$OUT" "$OUT" sh -c 'echo hi; echo err >&2; exit 3'
  [ "$status" -eq 3 ]
  grep -qx hi "$OUT"
  grep -qx err "$OUT"
}

@test "a heartbeat names the step while it runs" {
  run --separate-stderr run_bounded 10 1 "Validating conventions" "$OUT" "$OUT" sleep 3
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"⏱ Validating conventions still running"* ]]
}

@test "run_bounded refuses a non-integer bound rather than dying in arithmetic" {
  run --separate-stderr run_bounded 1.5 0 step "$OUT" "$OUT" true
  [ "$status" -eq 2 ]
  [[ "$stderr" == *"whole seconds"* ]]
}

@test "bounded_seconds: unset and empty are the default" {
  unset STEP_T
  run bounded_seconds STEP_T 1800
  [ "$status" -eq 0 ]
  [ "$output" = 1800 ]
  STEP_T="" run bounded_seconds STEP_T 1800
  [ "$output" = 1800 ]
}

@test "bounded_seconds: a decimal truncates, and a positive fraction is at least 1" {
  STEP_T=90.7 run bounded_seconds STEP_T 1800
  [ "$output" = 90 ]
  STEP_T=0.5 run bounded_seconds STEP_T 60
  [ "$output" = 1 ]
  STEP_T=0 run bounded_seconds STEP_T 60
  [ "$output" = 0 ]
}

@test "bounded_seconds: junk warns and keeps the default, never disables the bound" {
  STEP_T=abc run --separate-stderr bounded_seconds STEP_T 1800
  [ "$status" -eq 0 ]
  [ "$output" = 1800 ]
  [[ "$stderr" == *"STEP_T must be a number of seconds"* ]]
}

@test "SIGTERM to the caller kills the step's process group" {
  marker="$BATS_TEST_TMPDIR/survived"
  bash -c ". '$REPO_ROOT/lib/run_bounded.sh'; run_bounded 0 0 s '$OUT' '$OUT' sh -c \"sleep 3; touch $marker\"" &
  p=$!
  sleep 1
  kill -TERM "$p"
  wait "$p" || true
  sleep 4
  [ ! -e "$marker" ]
}

@test "heartbeat 0 prints nothing" {
  run --separate-stderr run_bounded 10 0 step "$OUT" "$OUT" sleep 2
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
}

@test "the deadline kills the whole process group and returns 124" {
  marker="$BATS_TEST_TMPDIR/grandchild-survived"
  run run_bounded 1 0 step "$OUT" "$OUT" sh -c "sh -c 'sleep 4; touch $marker' & wait"
  [ "$status" -eq 124 ]
  sleep 5
  [ ! -e "$marker" ]
}

# passes-at-base: a command's own 124 never wrote a marker; this change must keep that
@test "a command that itself exits 124 leaves no timeout marker" {
  run run_bounded 10 0 step "$OUT" "$OUT" sh -c 'exit 124'
  [ "$status" -eq 124 ]
  [ ! -e "$OUT.timeout" ]
}

@test "a timed-out step leaves a timeout marker beside OUT" {
  run run_bounded 1 0 step "$OUT" "$OUT" sleep 4
  [ "$status" -eq 124 ]
  [ -e "$OUT.timeout" ]
}

@test "the first heartbeat is preceded by a newline" {
  # File capture, not bats $stderr: bats strips a leading newline from captured stderr.
  errf="$BATS_TEST_TMPDIR/err"
  run_bounded 10 1 step "$OUT" "$OUT" sleep 3 2>"$errf"
  [ "$?" -eq 0 ]
  [[ "$(cat "$errf")" == $'\n⏱ '* ]]
  [[ "$(cat "$errf")" != *$'\n\n⏱ '* ]]
}
