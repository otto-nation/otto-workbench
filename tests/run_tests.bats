#!/usr/bin/env bats
# Tests for bin/local/run-tests — the job sizing every suite run goes through.
#
# The runner is sourced rather than executed: its main() is behind a
# BASH_SOURCE guard, so sizing can be exercised without starting a suite.
# `getconf` and `load_average` are shadowed per test, which is the only way to
# ask what the machine's own core count and load would produce.

# `run --separate-stderr` is a 1.5.0 flag, and bats silently treats flags it
# does not know as the command to run.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
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
  common_teardown
}

# machine CORES LOAD — pin what test_jobs reads about the machine it is on.
machine() {
  MACHINE_CORES="$1"
  MACHINE_LOAD="$2"
}

@test "an idle machine gets one job per core" {
  machine 8 0.42
  test_jobs
  [ "$JOBS" -eq 8 ]
}

@test "cores already busy are not handed to the suite" {
  machine 8 3.70
  test_jobs
  [ "$JOBS" -eq 5 ]
}

@test "a fractional load is truncated, not rounded up" {
  # A one-minute average already lags the load it reports; rounding up would
  # count that lag twice and give away a core the machine may have back.
  machine 8 3.99
  test_jobs
  [ "$JOBS" -eq 5 ]
}

@test "a machine with more cores than the cap still stops at the cap" {
  machine 64 0.10
  test_jobs
  [ "$JOBS" -eq "$TEST_JOBS_CAP" ]
}

@test "a saturated machine falls back to the floor rather than to zero" {
  # Free capacity is negative here: the load exceeds the core count, which is
  # exactly the three-concurrent-suites case. The suite must still progress.
  machine 8 20.00
  test_jobs
  [ "$JOBS" -eq "$TEST_JOBS_FLOOR" ]
  [ "$JOBS" -gt 0 ]
}

@test "an oversubscribed single-core machine still gets the floor" {
  machine 1 4.00
  test_jobs
  [ "$JOBS" -eq "$TEST_JOBS_FLOOR" ]
}

@test "an unreadable load average reads as an idle machine" {
  # Guessing from a reading of unknown shape is worse than the plain core
  # count the sizing used before load entered it.
  machine 8 0
  load_average() { return 1; }
  test_jobs
  [ "$JOBS" -eq 8 ]
}

@test "a load average of an unexpected shape reads as an idle machine" {
  machine 8 "not-a-number"
  test_jobs
  [ "$JOBS" -eq 8 ]
}

@test "TEST_JOBS wins over the sizing, the cap and the floor" {
  machine 8 3.70
  TEST_JOBS=32 test_jobs
  [ "$JOBS" -eq 32 ]
}

@test "TEST_JOBS=1 restores the serial ordering" {
  # The bisect path: a test that only fails under concurrency needs one worker
  # even on a machine with capacity for twelve.
  machine 8 0.10
  TEST_JOBS=1 test_jobs
  [ "$JOBS" -eq 1 ]
}

@test "CI ignores the load average and sizes from the core count" {
  # A hosted runner is dedicated to the job, so its load average reports the
  # checkout and pipx installs that just finished rather than competing work.
  machine 4 3.90
  CI=true test_jobs
  [ "$JOBS" -eq 4 ]
}

@test "busy_cores reports nothing busy under CI" {
  machine 8 7.50
  CI=true run busy_cores
  [ "$output" -eq 0 ]
}

# report_jobs writes to stderr, so `run` needs both streams merged to see it.
# JOBS is what main() resolves before either suite starts; the tests set it the
# same way rather than calling the suites.
report_for() {
  machine "$1" "$2"
  test_jobs
  report_jobs 2>&1
}

@test "a run says how parallel it is before it starts" {
  run report_for 8 0.42
  [ "$status" -eq 0 ]
  [[ "$output" == *"8 job(s)"* ]]
}

@test "a floored run says the machine is busy and the run will be slow" {
  # The case worth reading: a suite sized down by another worktree's run is
  # otherwise indistinguishable from one that is simply slow.
  run report_for 18 17.0
  [[ "$output" == *"2 job(s)"* ]]
  [[ "$output" == *"floored"* ]]
  [[ "$output" == *"expect a slow run"* ]]
}

@test "a capped run says so rather than implying the machine was empty" {
  run report_for 32 0.10
  [[ "$output" == *"12 job(s)"* ]]
  [[ "$output" == *"capped at 12"* ]]
}

@test "an ordinary run reports the load it was sized from" {
  run report_for 18 9.0
  [[ "$output" == *"9 job(s)"* ]]
  [[ "$output" == *"18 cores less ~9 in use"* ]]
  [[ "$output" != *"floored"* ]]
  [[ "$output" != *"capped"* ]]
}

@test "the report describes the reading test_jobs used, not a fresh one" {
  # load_average reads live kernel state on every call, so a machine whose
  # load is fluctuating between the two calls main() makes — test_jobs() to
  # resolve JOBS, then report_jobs() to explain it — must not have the second
  # call silently re-derive a different "why" than the JOBS value it is
  # attached to.
  machine 18 17.0
  test_jobs
  machine 18 0.10
  run report_jobs 2>&1
  [[ "$output" == *"2 job(s)"* ]]
  [[ "$output" == *"18 cores less ~17 in use"* ]]
  [[ "$output" == *"floored"* ]]
}

@test "an overridden run credits TEST_JOBS rather than the load" {
  machine 18 9.0
  # shellcheck disable=SC2034  # read by test_jobs and report_jobs in bin/local/run-tests
  TEST_JOBS=4
  test_jobs
  run report_jobs
  [[ "$output" == *"4 job(s)"* ]]
  [[ "$output" == *"TEST_JOBS"* ]]
  [[ "$output" != *"cores less"* ]]
}

@test "the report stays off stdout, which the pre-push hook parses" {
  # The hook counts passes out of this script's stdout; a line there would be
  # read as a test result. `--separate-stderr` is what splits the two streams
  # — bats merges them into $output otherwise, which would pass either way.
  machine 8 0.42
  test_jobs
  run --separate-stderr report_jobs
  [ -z "$output" ]
  [[ "$stderr" == *"8 job(s)"* ]]
}

@test "main reports the parallelism it resolved" {
  # The helper above is called directly by every other test here, so none of
  # them would notice main() losing the call. Asserted against the source for
  # the same reason the lock-ordering tests are: running main() starts a suite.
  local jobs_line report_line
  jobs_line=$(grep -n '^  test_jobs$' "$REPO_ROOT/bin/local/run-tests" | cut -d: -f1)
  report_line=$(grep -n '^  report_jobs$' "$REPO_ROOT/bin/local/run-tests" | cut -d: -f1)
  [ -n "$jobs_line" ]
  [ -n "$report_line" ]
  # After the resolve: the report names the number JOBS ends up holding.
  [ "$report_line" -gt "$jobs_line" ]
}

@test "the report is written after the lock re-exec, so it prints once" {
  # The pre-lock process execs away. Anything it printed is printed again by
  # the process that replaces it, which is how this first shipped: the line
  # appeared twice on every real run.
  local exec_line report_line
  exec_line=$(grep -n 'exec "$WORKBENCH_DIR/bin/local/with-tree-lock"' "$REPO_ROOT/bin/local/run-tests" | cut -d: -f1)
  report_line=$(grep -n '^  report_jobs$' "$REPO_ROOT/bin/local/run-tests" | cut -d: -f1)
  [ -n "$exec_line" ]
  [ -n "$report_line" ]
  [ "$report_line" -gt "$exec_line" ]
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

# ── Sharding ─────────────────────────────────────────────────────────────────

@test "shard_files partitions every test file exactly once" {
  local total=3
  local -a all_files=()
  for shard in 1 2 3; do
    while IFS= read -r f; do
      all_files+=("$f")
    done < <(shard_files "$shard" "$total")
  done
  # Count actual .bats files
  local expected
  expected=$(find "$REPO_ROOT/tests" -maxdepth 1 -name '*.bats' | wc -l | tr -d ' ')
  [ "${#all_files[@]}" -eq "$expected" ]
  # No duplicates — sort and compare with unique
  local sorted
  sorted=$(printf '%s\n' "${all_files[@]}" | sort)
  local unique
  unique=$(printf '%s\n' "${all_files[@]}" | sort -u)
  [ "$sorted" = "$unique" ]
}

@test "no shard is empty" {
  for shard in 1 2 3; do
    local count
    count=$(shard_files "$shard" 3 | wc -l | tr -d ' ')
    [ "$count" -gt 0 ]
  done
}

@test "shard partition is deterministic" {
  local run1 run2
  run1=$(shard_files 1 3)
  run2=$(shard_files 1 3)
  [ "$run1" = "$run2" ]
}

@test "out-of-range shard exits non-zero" {
  run shard_files 0 3
  [ "$status" -ne 0 ]
  run shard_files 4 3
  [ "$status" -ne 0 ]
}

@test "shard 1/1 returns all files" {
  local shard_count
  shard_count=$(shard_files 1 1 | wc -l | tr -d ' ')
  local total
  total=$(find "$REPO_ROOT/tests" -maxdepth 1 -name '*.bats' | wc -l | tr -d ' ')
  [ "$shard_count" -eq "$total" ]
}

@test "--shard requires --bats" {
  run main --shard 1/3
  [ "$status" -ne 0 ]
  [[ "$output" == *"--shard only applies to the bats suite"* ]]
}

@test "--shard rejects invalid format" {
  run main --bats --shard abc
  [ "$status" -ne 0 ]
  [[ "$output" == *"expected N/M"* ]]
}

# ── Tree validation lock ─────────────────────────────────────────────────────

@test "run-tests declares the tree while a suite runs" {
  grep -q 'with-tree-lock' "$REPO_ROOT/bin/local/run-tests"
}

@test "run-tests acquires inside main, not at file scope" {
  # Sourcing the script must not lock: tests/run_tests.bats sources it to
  # reach the sizing helpers, and a file-scope acquire would lock the real
  # worktree for the length of this suite.
  local acquire_line main_line
  acquire_line=$(grep -n 'with-tree-lock' "$REPO_ROOT/bin/local/run-tests" | head -1 | cut -d: -f1)
  main_line=$(grep -n '^main() {' "$REPO_ROOT/bin/local/run-tests" | cut -d: -f1)
  [ -n "$acquire_line" ]
  [ -n "$main_line" ]
  [ "$acquire_line" -gt "$main_line" ]
}

@test "run-tests re-execs itself under the lock only once" {
  # The guard variable stops the re-exec recursing forever.
  grep -q 'WORKBENCH_TREE_LOCK' "$REPO_ROOT/bin/local/run-tests"
}
