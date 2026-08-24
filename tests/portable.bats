#!/usr/bin/env bats
# Tests for lib/portable.sh — the GNU/BSD stat wrappers every other call site
# now goes through.

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/portable.sh
  source "$REPO_ROOT/lib/portable.sh"
  TMPDIR="$(mktemp -d)"
  FILE="$TMPDIR/sample"
  touch "$FILE"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

@test "file_mtime returns the mtime in epoch seconds" {
  touch -t 202001020304 "$FILE"
  run file_mtime "$FILE"
  [ "$status" -eq 0 ]
  # 2020-01-02 03:04 local time — exact epoch depends on the runner's zone, so
  # assert the shape and a decade-wide window rather than a literal.
  [[ "$output" =~ ^[0-9]+$ ]]
  [ "$output" -gt 1577000000 ]
  [ "$output" -lt 1580000000 ]
}

@test "file_mtime tracks a rewrite" {
  local before after
  before=$(file_mtime "$FILE")
  touch -t 200001010000 "$FILE"
  after=$(file_mtime "$FILE")
  [ "$after" -lt "$before" ]
}

@test "file_mode returns octal permission bits" {
  chmod 600 "$FILE"
  run file_mode "$FILE"
  [ "$status" -eq 0 ]
  [ "$output" = "600" ]

  chmod 755 "$FILE"
  run file_mode "$FILE"
  [ "$output" = "755" ]
}

@test "file_birth returns an epoch second count" {
  # APFS and ext4 record a birth time; older filesystems report 0. Both are
  # valid results — callers treat 0 as "unknown" — so assert the shape and
  # that a nonzero answer is not in the future.
  run file_birth "$FILE"
  [ "$status" -eq 0 ]
  [[ "$output" =~ ^[0-9]+$ ]]
  if [[ "$output" -gt 0 ]]; then
    [ "$output" -le "$(date +%s)" ]
  fi
}

@test "file_birth reads the creation time, not the mtime" {
  # Pushing the mtime into the future must not move the birth time. The reverse
  # direction is not a valid probe: APFS clamps birth time to the mtime when the
  # mtime is backdated past it. Skipped where there is no birth time to read.
  local birth
  birth=$(file_birth "$FILE")
  [[ "$birth" -gt 0 ]] || skip "filesystem does not record a birth time"
  touch -t 203001010000 "$FILE"
  [ "$(file_birth "$FILE")" = "$birth" ]
  [ "$(file_mtime "$FILE")" -gt "$birth" ]
}

@test "every helper returns 1 and prints nothing for a missing file" {
  local helper
  for helper in file_mtime file_birth file_mode; do
    run "$helper" "$TMPDIR/does-not-exist"
    [ "$status" -eq 1 ]
    [ -z "$output" ]
  done
}

@test "a path starting with a dash is read as a path, not a flag" {
  touch "$TMPDIR/-dashed"
  chmod 640 "$TMPDIR/-dashed"
  run file_mode "$TMPDIR/-dashed"
  [ "$status" -eq 0 ]
  [ "$output" = "640" ]
}

@test "no stray stdout leaks in from the form that fails" {
  # The bug this helper exists to prevent: GNU stat reads -f as --file-system
  # and prints a report before failing. Whichever form loses on this platform,
  # its output must not reach the caller.
  run file_mtime "$FILE"
  [ "${#lines[@]}" -eq 1 ]
  [[ "$output" =~ ^[0-9]+$ ]]
}

@test "load_average answers this machine with a bare decimal" {
  run load_average
  [ "$status" -eq 0 ]
  [[ "$output" =~ ^[0-9]+(\.[0-9]+)?$ ]]
}

@test "load_average reads the one-minute figure out of the BSD form" {
  # `{ 1.85 2.05 2.13 }` — the brace has to go before the first field is the
  # first field, and the two later averages are not what a caller asked for.
  sysctl() { echo "{ 1.85 2.05 2.13 }"; }
  run load_average
  [ "$status" -eq 0 ]
  [ "$output" = "1.85" ]
}

@test "load_average reads the one-minute figure out of the Linux form" {
  # `0.52 0.58 0.59 1/234 1234` — no brace, and two trailing fields that are
  # not averages at all.
  sysctl() { return 1; }
  cat() { echo "0.52 0.58 0.59 1/234 1234"; }
  run load_average
  unset -f cat
  [ "$status" -eq 0 ]
  [ "$output" = "0.52" ]
}

@test "load_average returns 1 and prints nothing when neither source reads" {
  # The caller decides what an unknown load means; inventing a number here
  # would have it size a test run from a reading nobody took.
  sysctl() { return 1; }
  cat() { return 1; }
  run load_average
  unset -f cat
  [ "$status" -eq 1 ]
  [ -z "$output" ]
}

@test "no stray stdout leaks in from the load source that fails" {
  # Same hazard as the stat helpers: a losing `sysctl` on Linux prints its own
  # complaint, and a `$(A || B)` would concatenate it onto B's answer.
  sysctl() { echo "sysctl: unknown oid 'vm.loadavg'"; return 1; }
  cat() { echo "0.52 0.58 0.59 1/234 1234"; }
  run load_average
  unset -f cat
  [ "$output" = "0.52" ]
}
