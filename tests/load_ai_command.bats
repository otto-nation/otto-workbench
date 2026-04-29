#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  source_lib
  ORIG_HOME="$HOME"
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR"
  # Re-derive TASKFILE_ENV for the test HOME (constants.sh resolves at source time)
  TASKFILE_ENV="$HOME/.config/task/taskfile.env"
  # Start from a clean directory with no local .taskfile/
  cd "$TMPDIR"
}

teardown() {
  export HOME="$ORIG_HOME"
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  common_teardown
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

# ── ANTHROPIC_API_KEY export ──────────────────────────────────────────────────

@test "exports ANTHROPIC_API_KEY when set in env file" {
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  mkdir -p "$TMPDIR/.config/task"
  printf 'AI_COMMAND=fake-ai\nANTHROPIC_API_KEY=sk-ant-test-key\n' \
    > "$TMPDIR/.config/task/taskfile.env"
  unset ANTHROPIC_API_KEY
  PATH="$TMPDIR/bin:$PATH" load_ai_command
  [ "$ANTHROPIC_API_KEY" = "sk-ant-test-key" ]
}

@test "does not fail when ANTHROPIC_API_KEY is absent from env file" {
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  make_ai_config "$TMPDIR" "fake-ai"
  PATH="$TMPDIR/bin:$PATH" run load_ai_command
  [ "$status" -eq 0 ]
}

@test "ANTHROPIC_API_KEY in env file overrides existing environment value" {
  make_fake_binary "$TMPDIR/bin" "fake-ai"
  mkdir -p "$TMPDIR/.config/task"
  printf 'AI_COMMAND=fake-ai\nANTHROPIC_API_KEY=sk-ant-from-file\n' \
    > "$TMPDIR/.config/task/taskfile.env"
  export ANTHROPIC_API_KEY="sk-ant-from-env"
  PATH="$TMPDIR/bin:$PATH" load_ai_command
  [ "$ANTHROPIC_API_KEY" = "sk-ant-from-file" ]
}
