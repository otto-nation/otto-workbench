#!/usr/bin/env bats
# Tests for wiki-capture-list.sh — transcripts for one repo since last capture.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TEST_HOME="$(cd "$TMPDIR" && pwd -P)/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  gate_sandbox
  LIST="$REPO_ROOT/ai/skills/wiki-capture/wiki-capture-list.sh"
  REPO="$(gate_repo "repo")"
}

teardown() {
  common_teardown
}

_stamp_file() {
  local encoded
  encoded="$(printf '%s' "${1#/}" | tr -c 'A-Za-z0-9_' '-')"
  printf '%s/gates/--%s--.last-wiki-capture' "$WORKBENCH_STATE_DIR" "$encoded"
}

_write_stamp() {
  mkdir -p "$WORKBENCH_STATE_DIR/gates"
  echo "$1" > "$(_stamp_file "$REPO")"
}

_run_list() {
  run bash -c "'$LIST' '$REPO'"
}

@test "wiki-capture-list: no sessions prints nothing and exits 0" {
  _run_list
  [[ "$status" -eq 0 ]]
  [[ -z "$output" ]]
}

@test "wiki-capture-list: sessions newer than the stamp are listed" {
  _write_stamp "$(( $(date +%s) - 100 ))"
  gate_sessions "$(gate_claude_dir "$REPO")" 2

  _run_list
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"session-1.jsonl"* ]]
  [[ "$output" == *"session-2.jsonl"* ]]
}

@test "wiki-capture-list: sessions older than the stamp are omitted" {
  gate_sessions "$(gate_claude_dir "$REPO")" 1 202001010000
  _write_stamp "$(date +%s)"

  _run_list
  [[ "$status" -eq 0 ]]
  [[ -z "$output" ]]
}

@test "wiki-capture-list: the current Pi session is omitted" {
  _write_stamp "$(( $(date +%s) - 100 ))"
  local dir
  dir="$(gate_pi_dir "$REPO")"
  mkdir -p "$dir"
  printf '{"type":"message"}\n' > "$dir/20260101120000_keep.jsonl"
  printf '{"type":"message"}\n' > "$dir/20260101120000_auto-task.jsonl"
  export PI_SESSION_ID="auto-task"

  _run_list
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"20260101120000_keep.jsonl"* ]]
  [[ "$output" != *"20260101120000_auto-task.jsonl"* ]]
}

@test "wiki-capture-list: the current Claude session is omitted" {
  _write_stamp "$(( $(date +%s) - 100 ))"
  local dir
  dir="$(gate_claude_dir "$REPO")"
  mkdir -p "$dir"
  printf '{"type":"user"}\n' > "$dir/keep.jsonl"
  printf '{"type":"user"}\n' > "$dir/auto-task.jsonl"
  export CLAUDE_CODE_SESSION_ID="auto-task"

  _run_list
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"keep.jsonl"* ]]
  [[ "$output" != *"auto-task.jsonl"* ]]
}

@test "wiki-capture-list: Pi and Claude sessions for the repo are both listed" {
  _write_stamp "$(( $(date +%s) - 100 ))"
  gate_sessions "$(gate_claude_dir "$REPO")" 1
  gate_sessions "$(gate_pi_dir "$REPO")" 1

  _run_list
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"$(gate_claude_dir "$REPO")"* ]]
  [[ "$output" == *"$(gate_pi_dir "$REPO")"* ]]
}

@test "wiki-capture-list: another repo's sessions are omitted" {
  _write_stamp "$(( $(date +%s) - 100 ))"
  local other
  other="$(gate_repo "other")"
  gate_sessions "$(gate_claude_dir "$other")" 1
  gate_sessions "$(gate_claude_dir "$REPO")" 1

  _run_list
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"$(gate_claude_dir "$REPO")"* ]]
  [[ "$output" != *"$(gate_claude_dir "$other")"* ]]
}
