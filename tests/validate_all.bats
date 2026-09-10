#!/usr/bin/env bats
# Tests for bin/local/validate-all — the single validator entry point.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATE_ALL="$REPO_ROOT/bin/local/validate-all"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _fixture_validator DIR NAME EXIT_CODE — write an executable stub into the
# fixture tree that validate-all will discover via VALIDATOR_ROOT. Fixtures
# rather than the real validators: running those here would double every
# pre-push validation, and the hook already runs them for real.
_fixture_validator() {
  mkdir -p "$TMPDIR/$1"
  printf '#!/usr/bin/env bash\nexit %s\n' "$3" > "$TMPDIR/$1/$2"
  chmod +x "$TMPDIR/$1/$2"
}

# _sleeping_validator DIR NAME SECONDS — a validator that does nothing but take
# time. Used to observe concurrency: wall-clock is the only evidence available
# that the pool ran jobs side by side rather than one after another.
_sleeping_validator() {
  mkdir -p "$TMPDIR/$1"
  printf '#!/usr/bin/env bash\nsleep %s\n' "$3" > "$TMPDIR/$1/$2"
  chmod +x "$TMPDIR/$1/$2"
}

# _slow_validators_running — how many _sleeping_validator processes are alive.
_slow_validators_running() {
  pgrep -f 'validate-slow-' | wc -l | tr -d ' '
}

_slow_validators_started() { [[ "$(_slow_validators_running)" -gt 0 ]]; }
_slow_validators_stopped() { [[ "$(_slow_validators_running)" -eq 0 ]]; }

# _tracked_validator DIR NAME — a validator that records when it entered and
# left its own body, so a test can count how many overlapped.
#
# The two marks bracket a short sleep, which is only there to widen the window
# enough for an overlap to be observable at all; nothing asserts its length.
_tracked_validator() {
  mkdir -p "$TMPDIR/$1"
  cat > "$TMPDIR/$1/$2" <<EOF
#!/usr/bin/env bash
echo "+" >> "$TMPDIR/marks"
sleep 0.5
echo "-" >> "$TMPDIR/marks"
EOF
  chmod +x "$TMPDIR/$1/$2"
}

# _peak_concurrency — the most validators that were running at any one moment.
#
# Read off the marks as a running total: every `+` is one entering its body and
# every `-` one leaving, so the high-water mark of the sum is the peak. Appends
# of a single short line under O_APPEND do not interleave, so the file is a
# faithful ordering of the events even with several writers.
_peak_concurrency() {
  [[ -f "$TMPDIR/marks" ]] || { echo 0; return; }
  awk '/\+/ { n++; if (n > peak) peak = n } /-/ { n-- } END { print peak + 0 }' "$TMPDIR/marks"
}

# _wait_until SECONDS COMMAND... — poll until COMMAND succeeds, or give up.
#
# A fixed `sleep` has to be either long enough for the slowest runner or short
# enough not to pad the suite, and cannot be both. Polling takes the time it
# actually needs and only spends the whole budget when the assertion is
# genuinely going to fail.
_wait_until() {
  local deadline=$(( SECONDS + $1 )); shift
  while (( SECONDS < deadline )); do
    "$@" && return 0
    sleep 0.1
  done
  return 1
}

@test "validate-all discovers validators in both bin and bin/local" {
  _fixture_validator "bin" "validate-top" 0
  _fixture_validator "bin/local" "validate-nested" 0

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "validate-top"
  echo "$output" | grep -q "validate-nested"
  echo "$output" | grep -q "2 validators passed"
}

@test "every real validator is discovered by validate-all" {
  local expected=0
  for v in "$REPO_ROOT"/bin/validate-* "$REPO_ROOT"/bin/local/validate-*; do
    if [[ -x "$v" && "$(basename "$v")" != "validate-all" ]]; then
      expected=$(( expected + 1 ))
    fi
  done
  [ "$expected" -gt 0 ]

  # --list resolves discovery without executing anything, so the real tree is
  # covered without re-running validators the hook is about to run anyway.
  run "$VALIDATE_ALL" --list
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | wc -l | tr -d ' ')" -eq "$expected" ]
}

@test "validate-all excludes itself from discovery" {
  _fixture_validator "bin/local" "validate-only-one" 0
  cp "$VALIDATE_ALL" "$TMPDIR/bin/local/validate-all"

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "validate-only-one"
  echo "$output" | grep -q "1 validators passed"
}

@test "validate-all fails and names the failing validator" {
  _fixture_validator "bin/local" "validate-good" 0
  _fixture_validator "bin/local" "validate-bad" 1

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL" --quiet
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "validate-bad"
  echo "$output" | grep -q "1 of 2 validators failed"
}

@test "validate-all succeeds when a tree holds no validators" {
  mkdir -p "$TMPDIR/bin/local"

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "no validators found"

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL" --list
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "validators run concurrently" {
  # Peak overlap, not elapsed time. A wall-clock bound has to assume how long
  # the work takes on the slowest runner the suite will ever meet; counting how
  # many validators were inside their own body at once is the same claim made
  # directly, and a loaded machine only makes the overlap longer.
  local i
  for i in 1 2 3 4; do
    _tracked_validator "bin/local" "validate-sleep-$i"
  done

  VALIDATOR_ROOT="$TMPDIR" VALIDATOR_JOBS=4 run "$VALIDATE_ALL" --quiet
  [ "$status" -eq 0 ]
  [ "$(_peak_concurrency)" -gt 1 ]
}

@test "VALIDATOR_JOBS caps how many run at once" {
  # The cap's only observable. With a pool of one no two validators may ever be
  # inside their body together, however the scheduler orders them — an
  # assertion that cannot be weakened by the machine being fast or slow.
  local i
  for i in 1 2 3 4; do
    _tracked_validator "bin/local" "validate-sleep-$i"
  done

  VALIDATOR_ROOT="$TMPDIR" VALIDATOR_JOBS=1 run "$VALIDATE_ALL" --quiet
  [ "$status" -eq 0 ]
  [ "$(_peak_concurrency)" -eq 1 ]
}

@test "the pool fills to the cap and no further" {
  local i
  for i in 1 2 3 4 5 6; do
    _tracked_validator "bin/local" "validate-sleep-$i"
  done

  VALIDATOR_ROOT="$TMPDIR" VALIDATOR_JOBS=2 run "$VALIDATE_ALL" --quiet
  [ "$status" -eq 0 ]
  [ "$(_peak_concurrency)" -eq 2 ]
}

@test "results are reported in discovery order, not completion order" {
  # Buffered output exists so a run reads the same as it did serially. The slow
  # validator sorts first and finishes last, so completion order would invert
  # these two lines.
  _sleeping_validator "bin/local" "validate-aaa-slow" 1
  _fixture_validator "bin/local" "validate-zzz-fast" 0

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  local order
  order=$(echo "$output" | grep -n 'validate-aaa-slow\|validate-zzz-fast' | cut -d: -f1 | tr '\n' ' ')
  [ "$(echo "$order" | awk '{print ($1 < $2)}')" -eq 1 ]
}

@test "a failing validator's output is not interleaved with another's" {
  # Two validators failing at once must produce two readable blocks, not one
  # shuffled block. Each writes several lines with a slice of sleep between
  # them, which is exactly what unbuffered concurrent writes would interleave.
  mkdir -p "$TMPDIR/bin/local"
  local name
  for name in one two; do
    cat > "$TMPDIR/bin/local/validate-$name" <<EOF
#!/usr/bin/env bash
for i in 1 2 3; do echo "$name-line-\$i"; sleep 0.1; done
exit 1
EOF
    chmod +x "$TMPDIR/bin/local/validate-$name"
  done

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL" --quiet
  [ "$status" -eq 1 ]
  # Every line of a validator's output lands before the next validator's first.
  local one_last two_first
  one_last=$(echo "$output" | grep -n 'one-line-3' | cut -d: -f1)
  two_first=$(echo "$output" | grep -n 'two-line-1' | cut -d: -f1)
  [ "$one_last" -lt "$two_first" ]
}

@test "a validator killed before it reports is counted as failed" {
  # The status file is the only record of a result. A validator that takes its
  # own process group down leaves none, and the run must not read that silence
  # as a pass.
  mkdir -p "$TMPDIR/bin/local"
  printf '#!/usr/bin/env bash\nkill -9 $PPID\n' > "$TMPDIR/bin/local/validate-suicidal"
  chmod +x "$TMPDIR/bin/local/validate-suicidal"

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL" --quiet
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "validate-suicidal"
}

@test "an interrupt kills the validators rather than orphaning them" {
  # A validator that forks is the case a bare `kill $pid` misses: bash reports
  # the subshell's pid, and the process it forked survives it. Each job runs in
  # its own process group so the whole tree is signalled.
  local i
  for i in 1 2 3; do
    _sleeping_validator "bin/local" "validate-slow-$i" 30
  done
  local buffers="$TMPDIR/buffers" root="$TMPDIR"
  mkdir -p "$buffers"

  # The root is bound to its own name because the same prefix list reassigns
  # TMPDIR. Bash expands the prefix against the pre-assignment value, so
  # VALIDATOR_ROOT="$TMPDIR" would in fact read the outer one — but nothing on
  # the line says so, and shellcheck reads it as the bug it looks like. Spelling
  # the two apart makes which directory is which answerable by reading it.
  VALIDATOR_ROOT="$root" TMPDIR="$buffers" "$VALIDATE_ALL" --quiet >/dev/null 2>&1 &
  local runner=$!
  # Polled rather than slept: on a loaded runner the validators may take longer
  # than a fixed window to spawn, and waiting a fixed window for them to die
  # would report a slow signal as an orphan.
  _wait_until 15 _slow_validators_started
  [ "$(_slow_validators_running)" -gt 0 ]

  kill -TERM "$runner"
  _wait_until 15 _slow_validators_stopped
  [ "$(_slow_validators_running)" -eq 0 ]
  # And the buffers go with them — the signal path cleans up too, not just the
  # normal exit.
  [ -z "$(ls -A "$buffers")" ]
}

@test "the run leaves no buffer directory behind" {
  # A dedicated TMPDIR rather than the one setup() made: the buffers land
  # wherever TMPDIR points, and looking anywhere else would pass without
  # observing anything. A failing validator too — the trap has to fire on the
  # exit path that reports failures, not only the clean one.
  local buffers="$TMPDIR/buffers" root="$TMPDIR"
  mkdir -p "$buffers"
  _fixture_validator "bin/local" "validate-good" 0
  _fixture_validator "bin/local" "validate-bad" 1

  # Bound to its own name for the reason the interrupt test above gives.
  VALIDATOR_ROOT="$root" TMPDIR="$buffers" run "$VALIDATE_ALL" --quiet
  [ "$status" -eq 1 ]
  [ -z "$(ls -A "$buffers")" ]
}

@test "validate-all skips non-executable files" {
  mkdir -p "$TMPDIR/bin/local"
  printf 'not a script\n' > "$TMPDIR/bin/local/validate-backup.orig"
  _fixture_validator "bin/local" "validate-real" 0

  VALIDATOR_ROOT="$TMPDIR" run "$VALIDATE_ALL"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "1 validators passed"
}

# ── Gate wiring ──────────────────────────────────────────────────────────────
#
# Both gates must call validate-all rather than list validators themselves —
# a hardcoded list is how validate-cli-flags ran in neither for months.

@test "pre-push hook runs validators through validate-all" {
  grep -q "bin/local/validate-all" "$REPO_ROOT/git/hooks/pre-push-workbench"
  run grep -cE 'bin/(local/)?validate-(registries|components|migrations|skills|cli-flags|errexit|nesting|worktree-guards|eval-baselines)' \
    "$REPO_ROOT/git/hooks/pre-push-workbench"
  [ "$output" -eq 0 ]
}

@test "CI runs validators through validate-all" {
  grep -q "bin/local/validate-all" "$REPO_ROOT/.github/workflows/ci.yml"
  run grep -cE 'bin/(local/)?validate-(registries|components|migrations|skills|cli-flags|errexit|nesting|worktree-guards|eval-baselines)' \
    "$REPO_ROOT/.github/workflows/ci.yml"
  [ "$output" -eq 0 ]
}
