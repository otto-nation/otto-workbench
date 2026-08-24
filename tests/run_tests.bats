#!/usr/bin/env bats
# Tests for bin/local/run-tests — the job sizing every suite run goes through.
#
# The runner is sourced rather than executed: its main() is behind a
# BASH_SOURCE guard, so sizing can be exercised without starting a suite.
# `getconf` and `load_average` are shadowed per test, which is the only way to
# ask what the machine's own core count and load would produce.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  # Both are read by the code under test and both are set on a real run — CI
  # sets CI, and a caller or a parent suite may have exported TEST_JOBS. Left
  # in place they would decide the answer instead of the test.
  unset TEST_JOBS CI
  # shellcheck source=../bin/local/run-tests
  source "$REPO_ROOT/bin/local/run-tests"
  # Defined after the source, which brings its own load_average with it.
  # Shadowing both readers is the only way to ask what a given machine would
  # produce; the real ones answer for whatever box the suite happens to be on.
  getconf() { echo "$MACHINE_CORES"; }
  load_average() { echo "$MACHINE_LOAD"; }
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# machine CORES LOAD — pin what test_jobs reads about the machine it is on.
machine() {
  MACHINE_CORES="$1"
  MACHINE_LOAD="$2"
}

@test "an idle machine gets one job per core" {
  machine 8 0.42
  run test_jobs
  [ "$status" -eq 0 ]
  [ "$output" -eq 8 ]
}

@test "cores already busy are not handed to the suite" {
  machine 8 3.70
  run test_jobs
  [ "$output" -eq 5 ]
}

@test "a fractional load is truncated, not rounded up" {
  # A one-minute average already lags the load it reports; rounding up would
  # count that lag twice and give away a core the machine may have back.
  machine 8 3.99
  run test_jobs
  [ "$output" -eq 5 ]
}

@test "a machine with more cores than the cap still stops at the cap" {
  machine 64 0.10
  run test_jobs
  [ "$output" -eq "$TEST_JOBS_CAP" ]
}

@test "a saturated machine falls back to the floor rather than to zero" {
  # Free capacity is negative here: the load exceeds the core count, which is
  # exactly the three-concurrent-suites case. The suite must still progress.
  machine 8 20.00
  run test_jobs
  [ "$output" -eq "$TEST_JOBS_FLOOR" ]
  [ "$output" -gt 0 ]
}

@test "an oversubscribed single-core machine still gets the floor" {
  machine 1 4.00
  run test_jobs
  [ "$output" -eq "$TEST_JOBS_FLOOR" ]
}

@test "an unreadable load average reads as an idle machine" {
  # Guessing from a reading of unknown shape is worse than the plain core
  # count the sizing used before load entered it.
  machine 8 0
  load_average() { return 1; }
  run test_jobs
  [ "$output" -eq 8 ]
}

@test "a load average of an unexpected shape reads as an idle machine" {
  machine 8 "not-a-number"
  run test_jobs
  [ "$output" -eq 8 ]
}

@test "TEST_JOBS wins over the sizing, the cap and the floor" {
  machine 8 3.70
  TEST_JOBS=32 run test_jobs
  [ "$output" -eq 32 ]
}

@test "TEST_JOBS=1 restores the serial ordering" {
  # The bisect path: a test that only fails under concurrency needs one worker
  # even on a machine with capacity for twelve.
  machine 8 0.10
  TEST_JOBS=1 run test_jobs
  [ "$output" -eq 1 ]
}

@test "CI ignores the load average and sizes from the core count" {
  # A hosted runner is dedicated to the job, so its load average reports the
  # checkout and pipx installs that just finished rather than competing work.
  machine 4 3.90
  CI=true run test_jobs
  [ "$output" -eq 4 ]
}

@test "busy_cores reports nothing busy under CI" {
  machine 8 7.50
  CI=true run busy_cores
  [ "$output" -eq 0 ]
}

@test "the help text names the floor and the cap it will apply" {
  run main --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"TEST_JOBS"* ]]
  [[ "$output" == *"at least $TEST_JOBS_FLOOR"* ]]
  [[ "$output" == *"at most $TEST_JOBS_CAP"* ]]
}

@test "sourcing the runner does not start a suite" {
  # The guard around main() is what makes every test above possible. Without
  # it, setup() would have run both suites before the first assertion.
  [ -z "$JOBS" ]
}
