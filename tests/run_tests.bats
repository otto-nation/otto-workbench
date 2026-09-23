#!/usr/bin/env bats
# Tests for bin/local/run-tests — the job sizing every suite run goes through.
#
# The runner is sourced rather than executed: its main() is behind a
# BASH_SOURCE guard, so sizing can be exercised without starting a suite.
# `cpu_count` is shadowed per test, which is the only way to ask what a given
# core count would produce — the real one answers for whatever box the suite
# happens to be running on.

# `run --separate-stderr` is a 1.5.0 flag, and bats silently treats flags it
# does not know as the command to run.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # All four are read by the code under test and all four are set on a real
  # run — CI sets CI, a caller or a parent suite may have exported TEST_JOBS,
  # and the slot wrapper exports its grant and marker into everything it runs,
  # this suite included. Left in place they would decide the answer instead of
  # the test: under the gate the outer run's grant of 12 reached report_jobs
  # and three tests asserting on a pinned core count failed.
  unset TEST_JOBS CI WORKBENCH_TEST_SLOTS_GRANTED WORKBENCH_TEST_SLOTS
  # shellcheck source=../bin/local/run-tests
  source "$REPO_ROOT/bin/local/run-tests"
  # Defined after the source, which brings lib/portable.sh's own cpu_count
  # with it. The pool is not exercised here: main() skips the claim when
  # TEST_JOBS or CI is set, and every test below calls test_jobs directly
  # rather than going through main.
  cpu_count() { echo "$MACHINE_CORES"; }
}

teardown() {
  common_teardown
}

# machine CORES — pin what test_jobs reads about the machine it is on.
#
# Only the core count now: the sizing no longer reads the load average, because
# a one-minute figure cannot see a suite that started thirty seconds ago. What
# another run is holding is asked of the slot pool instead, which is exercised
# in tests/job_slots_test.py.
machine() {
  MACHINE_CORES="$1"
}

@test "a machine gets one job per core" {
  machine 8
  test_jobs
  [ "$JOBS" -eq 8 ]
}

@test "a machine with more cores than the cap still stops at the cap" {
  machine 64
  test_jobs
  [ "$JOBS" -eq "$TEST_JOBS_CAP" ]
}

@test "a single-core machine asks for one job" {
  machine 1
  test_jobs
  [ "$JOBS" -eq 1 ]
}

# passes-at-base: the override predates the pool, and holds that it did not start clamping a named value
@test "TEST_JOBS wins over the sizing and the cap" {
  machine 8
  TEST_JOBS=32 test_jobs
  [ "$JOBS" -eq 32 ]
}

@test "TEST_JOBS=1 restores the serial ordering" {
  # The bisect path: a test that only fails under concurrency needs one worker
  # even on a machine with capacity for twelve.
  machine 8
  TEST_JOBS=1 test_jobs
  [ "$JOBS" -eq 1 ]
}

@test "the sizing no longer reads the load average" {
  # The bug this replaced: two suites launched within a minute of each other
  # both read an idle machine and both took the cap, so 24 heavy processes
  # landed on 18 cores. A reading cannot lag when there is no reading.
  run grep -n 'load_average' "$REPO_ROOT/bin/local/run-tests"
  [ "$status" -ne 0 ]
}

@test "granted_jobs prefers the pool's grant over what was asked for" {
  # The claim wrapper exports what it actually handed this run. Reading JOBS
  # instead would run twelve workers while the pool believed it had granted
  # five, which is the oversubscription the pool exists to prevent.
  machine 18
  test_jobs
  WORKBENCH_TEST_SLOTS_GRANTED=5 run granted_jobs
  [ "$output" -eq 5 ]
}

@test "granted_jobs falls back to the request when nothing granted" {
  # The CI and TEST_JOBS paths skip the pool entirely, so no grant is exported
  # and the request is the number to run at.
  machine 18
  test_jobs
  run granted_jobs
  [ "$output" -eq 12 ]
}

@test "both runners are sized from the grant, not the request" {
  # A grant read by only one of them is half a fix: the other still takes the
  # full cap, and two suites still oversubscribe on that half.
  run grep -cE '(--jobs|-n) "\$\(granted_jobs\)"' "$REPO_ROOT/bin/local/run-tests"
  [ "$output" -eq 2 ]
}

# report_jobs writes to stderr, so `run` needs both streams merged to see it.
# JOBS is what main() resolves before either suite starts; the tests set it the
# same way rather than calling the suites.
report_for() {
  machine "$1"
  test_jobs
  report_jobs 2>&1
}

@test "a run says how parallel it is before it starts" {
  run report_for 8
  [ "$status" -eq 0 ]
  [[ "$output" == *"8 job(s)"* ]]
}

@test "a run sized down by a sibling says so, and names the reason" {
  # The case worth reading: a suite that got 3 of the 12 it asked for is
  # otherwise indistinguishable from one that is simply slow.
  machine 18
  test_jobs
  WORKBENCH_TEST_SLOTS_GRANTED=3 run report_jobs 2>&1
  [[ "$output" == *"3 job(s)"* ]]
  [[ "$output" == *"another test run holds the rest"* ]]
}

@test "a run floored by a full pool warns that it will be slow" {
  machine 18
  test_jobs
  WORKBENCH_TEST_SLOTS_GRANTED=2 run report_jobs 2>&1
  [[ "$output" == *"2 job(s)"* ]]
  [[ "$output" == *"expect a slow run"* ]]
}

@test "a capped run says so rather than implying the machine was empty" {
  run report_for 32
  [[ "$output" == *"12 job(s)"* ]]
  [[ "$output" == *"capped at 12"* ]]
}

# passes-at-base: a negative case — the strings it refuses do not exist at base, and 18 cores gave 12 there too
@test "an uncontended run does not claim a sibling took anything" {
  run report_for 18
  [[ "$output" == *"12 job(s)"* ]]
  [[ "$output" != *"holds the rest"* ]]
  [[ "$output" != *"expect a slow run"* ]]
}

@test "a CI run says the pool was skipped" {
  # A hosted runner is dedicated to the job and the bats shards run on separate
  # runners, so a machine-wide pool there would only contend with itself.
  machine 4
  CI=true test_jobs
  CI=true run report_jobs 2>&1
  [[ "$output" == *"4 job(s)"* ]]
  [[ "$output" == *"pool skipped under CI"* ]]
}

# passes-at-base: the override's reporting predates the pool, and still names TEST_JOBS over the grant
@test "an overridden run credits TEST_JOBS rather than the machine" {
  machine 18
  # shellcheck disable=SC2034  # read by test_jobs and report_jobs in bin/local/run-tests
  TEST_JOBS=4
  test_jobs
  run report_jobs
  [[ "$output" == *"4 job(s)"* ]]
  [[ "$output" == *"TEST_JOBS"* ]]
  [[ "$output" != *"cores"* ]]
}

# passes-at-base: nothing exported the grant before this change; its subject is setup()'s unset, not the pool
@test "the suite does not inherit the grant of the run executing it" {
  # This suite runs *under* run-tests, so the slot wrapper has exported its own
  # grant into it. Read as the run under test's, it decides the answer: the
  # gate's grant of 12 reached report_jobs and failed three tests here that a
  # standalone bats invocation passed. setup() unsets it; this is what holds
  # that line in place.
  [ -z "${WORKBENCH_TEST_SLOTS_GRANTED:-}" ]
  [ -z "${WORKBENCH_TEST_SLOTS:-}" ]
}

@test "the report stays off stdout, which the pre-push hook parses" {
  # The hook counts passes out of this script's stdout; a line there would be
  # read as a test result. `--separate-stderr` is what splits the two streams
  # — bats merges them into $output otherwise, which would pass either way.
  machine 8
  test_jobs
  run --separate-stderr report_jobs
  [ -z "$output" ]
  [[ "$stderr" == *"8 job(s)"* ]]
}

@test "the slot claim wraps the suite rather than preceding it" {
  # A flock lives only as long as the process holding its descriptor, so a
  # claim taken and returned before the runner starts reserves nothing. The
  # exec is what makes the wrapper the parent of the suite.
  run grep -n 'exec "$WORKBENCH_DIR/bin/local/claim-job-slots"' "$REPO_ROOT/bin/local/run-tests"
  [ "$status" -eq 0 ]
}

@test "the slot claim comes after the tree lock, not before it" {
  # The tree lock can wait on a concurrent validator. Slots held across that
  # wait would be capacity reserved by a run that has not started, which is the
  # pool lying about what the machine is doing.
  local tree_line slots_line
  tree_line=$(grep -n 'exec "$WORKBENCH_DIR/bin/local/with-tree-lock"' "$REPO_ROOT/bin/local/run-tests" | cut -d: -f1)
  slots_line=$(grep -n 'exec "$WORKBENCH_DIR/bin/local/claim-job-slots"' "$REPO_ROOT/bin/local/run-tests" | cut -d: -f1)
  [ -n "$tree_line" ]
  [ -n "$slots_line" ]
  [ "$slots_line" -gt "$tree_line" ]
}

@test "CI and TEST_JOBS skip the claim rather than queueing behind it" {
  run grep -n 'z "${WORKBENCH_TEST_SLOTS:-}" && -z "${CI:-}" && -z "${TEST_JOBS:-}"' \
    "$REPO_ROOT/bin/local/run-tests"
  [ "$status" -eq 0 ]
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

# ── Serial-run diagnostics ───────────────────────────────────────────────────
#
# A suite that loses xdist runs several times slower and says nothing, so the
# only signal is a wall time nobody has a baseline for. These cover the message
# that replaces that silence — and specifically the shadowing case, which is a
# different remedy from a genuinely missing plugin.

@test "a serial run names the pytest it is using" {
  pytest() { [[ "$1" == "-VV" ]] && echo "no plugins here"; }
  export -f pytest 2>/dev/null || true
  run report_missing_xdist
  [[ "$output" == *"running serially"* ]]
  [[ "$output" == *"using:"* ]]
}

@test "a shadowed pytest is reported as shadowing, with its path" {
  # The real case on a machine where a version manager's shim directory sits
  # ahead of pipx's: `pytest` resolves to an interpreter that never had xdist
  # injected, while the one that does sits second and unused.
  local dir_a="$TMPDIR/bin-a" dir_b="$TMPDIR/bin-b"
  mkdir -p "$dir_a" "$dir_b"
  printf '#!/usr/bin/env bash\necho "pytest 9.0.0"\n' > "$dir_a/pytest"
  printf '#!/usr/bin/env bash\necho "pytest-xdist-3.8.0"\n' > "$dir_b/pytest"
  chmod +x "$dir_a/pytest" "$dir_b/pytest"

  PATH="$dir_a:$dir_b:$PATH" run report_missing_xdist
  [[ "$output" == *"shadowing: $dir_b/pytest"* ]]
  [[ "$output" == *"which does have xdist"* ]]
  [[ "$output" != *"pipx inject"* ]]
}

@test "a genuinely missing plugin is reported as an install, not a shadow" {
  # Nothing else on PATH has xdist, so there is nothing to un-shadow and the
  # remedy is the install the README documents.
  local dir_a="$TMPDIR/only-bin"
  mkdir -p "$dir_a"
  printf '#!/usr/bin/env bash\necho "pytest 9.0.0"\n' > "$dir_a/pytest"
  chmod +x "$dir_a/pytest"

  # /usr/bin kept on PATH: emptying it entirely takes mktemp and the rest of
  # the system utilities with it, and bats fails in its own helpers.
  PATH="$dir_a:/usr/bin:/bin" run report_missing_xdist
  [[ "$output" == *"pipx inject pytest pytest-xdist"* ]]
  [[ "$output" != *"shadowing:"* ]]
}

@test "pytest missing entirely is reported as missing, not as a blank path" {
  # No pytest anywhere on PATH: the old code printed "using: " with nothing
  # after it, which reads as a bug rather than the actual failure mode.
  PATH="/usr/bin:/bin" run report_missing_xdist
  [[ "$output" == *"pytest not found on PATH"* ]]
  [[ "$output" != *"using: "$'\n'* ]]
  [[ "$output" != *"using:  "* ]]
}

@test "the diagnostics stay off stdout, which the pre-push hook parses" {
  local dir_a="$TMPDIR/quiet-bin"
  mkdir -p "$dir_a"
  printf '#!/usr/bin/env bash\necho "pytest 9.0.0"\n' > "$dir_a/pytest"
  chmod +x "$dir_a/pytest"

  PATH="$dir_a:/usr/bin:/bin" run --separate-stderr report_missing_xdist
  [ -z "$output" ]
  [[ "$stderr" == *"running serially"* ]]
}

@test "_pytest_candidates lists every pytest on PATH, in resolution order" {
  local dir_a="$TMPDIR/cand-a" dir_b="$TMPDIR/cand-b"
  mkdir -p "$dir_a" "$dir_b"
  printf '#!/usr/bin/env bash\n' > "$dir_a/pytest"
  printf '#!/usr/bin/env bash\n' > "$dir_b/pytest"
  chmod +x "$dir_a/pytest" "$dir_b/pytest"

  PATH="$dir_a:$dir_b:/usr/bin:/bin" run _pytest_candidates
  [ "${lines[0]}" = "$dir_a/pytest" ]
  [ "${lines[1]}" = "$dir_b/pytest" ]
}

@test "_pytest_candidates skips a PATH entry with no pytest" {
  # A non-existent directory on PATH is normal and must not abort the walk
  # under set -e, which would leave the shadowing check silently unrun.
  local dir_a="$TMPDIR/cand-real"
  mkdir -p "$dir_a"
  printf '#!/usr/bin/env bash\n' > "$dir_a/pytest"
  chmod +x "$dir_a/pytest"

  PATH="$TMPDIR/does-not-exist:$dir_a:/usr/bin:/bin" run _pytest_candidates
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "$dir_a/pytest" ]
}
