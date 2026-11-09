#!/usr/bin/env bats
# Tests for herdr-app output helpers (tailLines).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# Evaluate EXPR against output.js as m; print JSON.stringify(EXPR).
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/output.js');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "null empty and number return empty array" {
  _js '[m.tailLines(null),m.tailLines(""),m.tailLines(1)]'
  [ "$status" -eq 0 ]
  [ "$output" = '[[],[],[]]' ]
}

@test "ansi escapes are stripped" {
  _js 'm.tailLines("\x1b[31mred\x1b[0m")'
  [ "$status" -eq 0 ]
  [ "$output" = '["red"]' ]
}

@test "trailing blank lines drop; interior blanks stay" {
  _js 'm.tailLines("a\n\nb\n\n")'
  [ "$status" -eq 0 ]
  [ "$output" = '["a","","b"]' ]
}

@test "cap keeps the last lines" {
  _js 'm.tailLines("1\n2\n3\n4\n5", 2)'
  [ "$status" -eq 0 ]
  [ "$output" = '["4","5"]' ]
}

@test "CRLF is handled as a line break" {
  _js 'm.tailLines("a\r\nb")'
  [ "$status" -eq 0 ]
  [ "$output" = '["a","b"]' ]
}
