#!/usr/bin/env bats
# Tests for ensure_tool_path in lib/setup.sh — sync's PATH must reach the tools
# a user installed, or the zsh step removes the snippets that activate them.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  FAKE_HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$FAKE_HOME"
}

teardown() {
  common_teardown
}

# Sources the libs under FAKE_HOME (so the constants point into it), narrows
# PATH to the given value, runs ensure_tool_path, then evaluates the rest.
_with_tool_path() {
  local start_path="$1" then="$2"
  env -i HOME="$FAKE_HOME" PATH="$PATH" NO_COLOR=1 bash -c '
    . "$1/lib/ui.sh"
    PATH="$2"
    ensure_tool_path
    eval "$3"
  ' _ "$REPO_ROOT" "$start_path" "$then"
}

@test "appends ~/.local/bin, Pi's bin and mise shims that exist" {
  mkdir -p "$FAKE_HOME/.local/bin" "$FAKE_HOME/.pi/agent/bin" "$FAKE_HOME/.local/share/mise/shims"

  run _with_tool_path /usr/bin:/bin 'echo "$PATH"'

  [ "$status" -eq 0 ]
  [[ "$output" == "/usr/bin:/bin:$FAKE_HOME/.local/bin:$FAKE_HOME/.pi/agent/bin:$FAKE_HOME/.local/share/mise/shims"* ]]
}

@test "skips directories that do not exist" {
  mkdir -p "$FAKE_HOME/.local/bin"

  run _with_tool_path /usr/bin:/bin 'echo "$PATH"'

  [ "$status" -eq 0 ]
  [[ "$output" == "/usr/bin:/bin:$FAKE_HOME/.local/bin"* ]]
  [[ "$output" != *"$FAKE_HOME/.pi/agent/bin"* ]]
  [[ "$output" != *"mise/shims"* ]]
}

# passes-at-base: guards that an already-ordered PATH keeps its order
@test "keeps the order of a PATH that already holds the directory" {
  mkdir -p "$FAKE_HOME/.local/bin"

  run _with_tool_path "$FAKE_HOME/.local/bin:/usr/bin:/bin" 'echo "$PATH"'

  [ "$status" -eq 0 ]
  [[ "$output" == "$FAKE_HOME/.local/bin:/usr/bin:/bin"* ]]
  [[ "$output" != *":$FAKE_HOME/.local/bin:"* && "$output" != *":$FAKE_HOME/.local/bin" ]]
}

@test "zsh layer deploy keeps a snippet whose tool lives only in ~/.local/bin" {
  mkdir -p "$FAKE_HOME/.local/bin" "$BATS_TEST_TMPDIR/src" "$BATS_TEST_TMPDIR/dst"
  printf '#!/bin/sh\n' > "$FAKE_HOME/.local/bin/fake-mise"
  chmod +x "$FAKE_HOME/.local/bin/fake-mise"
  printf '# requires-cmd: fake-mise\n' > "$BATS_TEST_TMPDIR/src/mise.zsh"
  cp "$BATS_TEST_TMPDIR/src/mise.zsh" "$BATS_TEST_TMPDIR/dst/mise.zsh"

  run _with_tool_path /usr/bin:/bin \
    ". \"$REPO_ROOT/zsh/steps.sh\"; _deploy_zsh_layer \"$BATS_TEST_TMPDIR/src\" \"$BATS_TEST_TMPDIR/dst\" '*.zsh'"

  [ "$status" -eq 0 ]
  [ -f "$BATS_TEST_TMPDIR/dst/mise.zsh" ]
}

# bin/otto-workbench dispatches on load, so the command functions cannot be
# sourced; these check each sync entry point calls the function as a live line.
_fn_body() {
  awk -v fn="$1" '$0 == fn "() {" {f=1} f&&/^\}/{exit} f' "$REPO_ROOT/bin/otto-workbench"
}

@test "cmd_sync calls ensure_tool_path" {
  run _fn_body cmd_sync
  [ "$status" -eq 0 ]
  grep -qx '  ensure_tool_path' <<<"$output"
}

@test "cmd_ai_sync calls ensure_tool_path" {
  run _fn_body cmd_ai_sync
  [ "$status" -eq 0 ]
  grep -qx '  ensure_tool_path' <<<"$output"
}
