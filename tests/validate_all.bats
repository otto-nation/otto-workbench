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
  # Four one-second validators in a pool of four finish in about a second. The
  # ceiling is set well under the serial four so a loaded runner cannot make a
  # genuinely parallel run look serial.
  local i
  for i in 1 2 3 4; do
    _sleeping_validator "bin/local" "validate-sleep-$i" 1
  done

  local start elapsed
  start=$SECONDS
  VALIDATOR_ROOT="$TMPDIR" VALIDATOR_JOBS=4 run "$VALIDATE_ALL" --quiet
  elapsed=$(( SECONDS - start ))
  [ "$status" -eq 0 ]
  [ "$elapsed" -lt 3 ]
}

@test "VALIDATOR_JOBS caps how many run at once" {
  # The same four seconds of work through a pool of one is a serial run, and
  # must take at least as long as the work does. This is the throttle's only
  # observable: without it the pool would launch all four immediately.
  local i
  for i in 1 2 3 4; do
    _sleeping_validator "bin/local" "validate-sleep-$i" 1
  done

  local start elapsed
  start=$SECONDS
  VALIDATOR_ROOT="$TMPDIR" VALIDATOR_JOBS=1 run "$VALIDATE_ALL" --quiet
  elapsed=$(( SECONDS - start ))
  [ "$status" -eq 0 ]
  [ "$elapsed" -ge 3 ]
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
  local buffers="$TMPDIR/buffers"
  mkdir -p "$buffers"

  VALIDATOR_ROOT="$TMPDIR" TMPDIR="$buffers" "$VALIDATE_ALL" --quiet >/dev/null 2>&1 &
  local runner=$!
  sleep 1
  [ "$(pgrep -f 'validate-slow-' | wc -l | tr -d ' ')" -gt 0 ]

  kill -TERM "$runner"
  sleep 1
  [ "$(pgrep -f 'validate-slow-' | wc -l | tr -d ' ')" -eq 0 ]
  # And the buffers go with them — the signal path cleans up too, not just the
  # normal exit.
  [ -z "$(ls -A "$buffers")" ]
}

@test "the run leaves no buffer directory behind" {
  # A dedicated TMPDIR rather than the one setup() made: the buffers land
  # wherever TMPDIR points, and looking anywhere else would pass without
  # observing anything. A failing validator too — the trap has to fire on the
  # exit path that reports failures, not only the clean one.
  local buffers="$TMPDIR/buffers"
  mkdir -p "$buffers"
  _fixture_validator "bin/local" "validate-good" 0
  _fixture_validator "bin/local" "validate-bad" 1

  VALIDATOR_ROOT="$TMPDIR" TMPDIR="$buffers" run "$VALIDATE_ALL" --quiet
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
