#!/usr/bin/env bats
# Tests for lib/ai/session-count.sh — the mtime-based session counter behind
# the dream, retro, and promote cooldown checks.

setup() {
  load 'test_helper'
  common_setup
  source_lib
  SESSIONS="$(mktemp -d)/"
  mkdir -p "$SESSIONS"
}

teardown() {
  rm -rf "$SESSIONS"
  common_teardown
}

# Creates a .jsonl session file whose mtime is AGE_SECONDS in the past.
_session() {
  local name="$1" age_seconds="$2" file="$SESSIONS$1.jsonl"
  touch "$file"
  touch -t "$(_stamp "$age_seconds")" "$file"
}

# touch -t timestamp ([[CC]YY]MMDDhhmm) for AGE_SECONDS ago, GNU then BSD.
_stamp() {
  local out
  out=$(date -d "-$1 seconds" +%Y%m%d%H%M 2>/dev/null) \
    || out=$(date -v "-$1S" +%Y%m%d%H%M 2>/dev/null)
  printf '%s' "$out"
}

@test "counts a session newer than the cutoff" {
  _session recent 60
  run _has_enough_sessions "$SESSIONS" "$(_epoch_ago 3600)" 1
  [ "$status" -eq 0 ]
}

@test "ignores a session older than the cutoff" {
  _session stale 7200
  run _has_enough_sessions "$SESSIONS" "$(_epoch_ago 3600)" 1
  [ "$status" -eq 1 ]
}

@test "requires min_count sessions, not just one" {
  _session a 60
  _session b 120
  run _has_enough_sessions "$SESSIONS" "$(_epoch_ago 3600)" 3
  [ "$status" -eq 1 ]

  _session c 180
  run _has_enough_sessions "$SESSIONS" "$(_epoch_ago 3600)" 3
  [ "$status" -eq 0 ]
}

@test "counts only the sessions newer than the cutoff" {
  _session fresh 60
  _session stale 7200
  run _has_enough_sessions "$SESSIONS" "$(_epoch_ago 3600)" 2
  [ "$status" -eq 1 ]
}

@test "returns 1 when the directory holds no sessions" {
  run _has_enough_sessions "$SESSIONS" "$(_epoch_ago 3600)" 1
  [ "$status" -eq 1 ]
}

@test "ignores non-jsonl files" {
  touch "${SESSIONS}notes.txt"
  run _has_enough_sessions "$SESSIONS" "$(_epoch_ago 3600)" 1
  [ "$status" -eq 1 ]
}

@test "resolves an mtime rather than falling back to 0" {
  # Regression guard: the stat chain previously ran both the GNU and BSD forms
  # inside one substitution, so a form that printed to stdout before failing
  # would corrupt the timestamp. A file touched now must read as recent.
  _session now 0
  run _has_enough_sessions "$SESSIONS" "$(_epoch_ago 120)" 1
  [ "$status" -eq 0 ]
}

# Epoch seconds for SECONDS_AGO in the past.
_epoch_ago() {
  printf '%s' "$(($(date +%s) - $1))"
}
