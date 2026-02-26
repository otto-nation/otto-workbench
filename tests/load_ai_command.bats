#!/usr/bin/env bats

setup() {
  load 'test_helper'
  source_lib
  ORIG_HOME="$HOME"
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR"
  # Start from a clean directory with no local .taskfile/
  cd "$TMPDIR"
}

teardown() {
  export HOME="$ORIG_HOME"
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
}

@test "fails: no env file exists" {
  run load_ai_command
  [ "$status" -eq 1 ]
  [[ "$output" == *"AI not configured"* ]]
}

@test "fails: env file exists but AI_COMMAND is not set" {
  mkdir -p "$TMPDIR/.config/task"
  echo "# no command here" > "$TMPDIR/.config/task/taskfile.env"
  run load_ai_command
  [ "$status" -eq 1 ]
  [[ "$output" == *"AI_COMMAND not set"* ]]
}

@test "fails: AI_COMMAND is commented out" {
  mkdir -p "$TMPDIR/.config/task"
  echo "# AI_COMMAND=claude" > "$TMPDIR/.config/task/taskfile.env"
  run load_ai_command
  [ "$status" -eq 1 ]
  [[ "$output" == *"AI_COMMAND not set"* ]]
}

@test "fails: AI binary not found in PATH" {
  make_ai_config "$TMPDIR" "nonexistent-binary-xyz-abc"
  run load_ai_command
  [ "$status" -eq 1 ]
  [[ "$output" == *"AI command not found"* ]]
}

@test "succeeds: global env file with valid binary" {
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  PATH="$TMPDIR/bin:$PATH" run load_ai_command
  [ "$status" -eq 0 ]
}

@test "succeeds: reads AI_COMMAND with flags" {
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai --flag1 --flag2"
  PATH="$TMPDIR/bin:$PATH" load_ai_command
  [ "$AI_COMMAND" = "fake-ai --flag1 --flag2" ]
}

@test "prefers local .taskfile/taskfile.env over global" {
  # Global config points to global-ai
  make_fake_binary "$TMPDIR/bin" "global-ai"
  make_fake_binary "$TMPDIR/bin" "local-ai"
  make_ai_config "$TMPDIR" "global-ai"

  # Local config points to local-ai
  mkdir -p "$TMPDIR/project/.taskfile"
  echo "AI_COMMAND=local-ai" > "$TMPDIR/project/.taskfile/taskfile.env"
  cd "$TMPDIR/project"

  PATH="$TMPDIR/bin:$PATH" load_ai_command
  [ "$AI_COMMAND" = "local-ai" ]
}
