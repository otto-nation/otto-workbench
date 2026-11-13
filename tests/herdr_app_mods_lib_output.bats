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

@test "OSC sequences are stripped and lone CR keeps the last redraw" {
  _js 'm.tailLines("\x1b]0;title\x07a\x1b]8;;http://x\x1b\\link\nfoo\r bar\rdone")'
  [ "$status" -eq 0 ]
  [ "$output" = '["alink","done"]' ]
}

# Evaluate EXPR against run_output.js as m; print JSON.stringify(EXPR).
_ro() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/run_output.js');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "outputModel maps live to running, exit 0 to pass, non-zero and null to fail" {
  _ro '[
    m.outputModel({ terminal: { state: "live" } }).status,
    m.outputModel({ terminal: { state: "finished", exitCode: 0 } }).status,
    m.outputModel({ terminal: { state: "finished", exitCode: 2 } }).status,
    m.outputModel({ terminal: { state: "finished", exitCode: null } }).status,
    m.outputModel({}).status,
  ]'
  [ "$status" -eq 0 ]
  [ "$output" = '["running","pass","fail","fail","running"]' ]
}

@test "outputModel title comes from the action" {
  _ro '[m.outputModel({ action: "review" }).title, m.outputModel({ action: "post-comment" }).title, m.outputModel({ action: "nope" }).title]'
  [ "$status" -eq 0 ]
  [ "$output" = '["Review","Post comment","Action"]' ]
}

@test "outputModel elapsed reuses durationText" {
  _ro 'm.outputModel({ startedMs: 0, nowMs: 78000 }).elapsed'
  [ "$status" -eq 0 ]
  [ "$output" = '"1m 18s"' ]
  _ro 'm.outputModel({ startedMs: 0, endedMs: 5000, nowMs: 99999 }).elapsed'
  [ "$output" = '"5s"' ]
  _ro 'm.outputModel({ nowMs: 5000 }).elapsed'
  [ "$output" = '""' ]
}

@test "summary skips blank and progress-only lines" {
  _ro 'm.outputModel({ text: "reviewing files\n\n42%\n...\n" }).summary'
  [ "$status" -eq 0 ]
  [ "$output" = '"reviewing files"' ]
  _ro 'm.summaryLine("a\n\n⠋\n50%\n", 20)'
  [ "$output" = '"a"' ]
  _ro 'm.outputModel({ text: "\n...\n" }).summary'
  [ "$output" = '""' ]
}

@test "outputModel lines are the cleaned tail" {
  _ro 'm.outputModel({ text: "\x1b[31mred\x1b[0m\n" }).lines'
  [ "$status" -eq 0 ]
  [ "$output" = '["red"]' ]
}

@test "landed is the review run whose reviewed_at is at or after startedMs" {
  _ro 'm.outputModel({
    action: "review",
    startedMs: Date.parse("2024-01-01T00:01:00.000Z"),
    runs: [
      { stamp: "old", reviewed_at: "2024-01-01T00:00:00.000Z" },
      { stamp: "new", reviewed_at: "2024-01-01T00:02:00.000Z" },
    ],
  }).landed?.stamp'
  [ "$status" -eq 0 ]
  [ "$output" = '"new"' ]
}

@test "a run older than startedMs is not landed" {
  _ro 'm.outputModel({
    action: "review",
    startedMs: Date.parse("2024-01-01T00:01:00.000Z"),
    runs: [{ stamp: "old", reviewed_at: "2024-01-01T00:00:00.000Z" }],
  }).landed'
  [ "$status" -eq 0 ]
  [ "$output" = 'null' ]
}

@test "landed is null for post and dismiss even when a later run exists" {
  _ro '[
    m.outputModel({
      action: "post-comment",
      startedMs: Date.parse("2024-01-01T00:00:00.000Z"),
      runs: [{ stamp: "x", reviewed_at: "2024-01-01T00:02:00.000Z" }],
    }).landed,
    m.outputModel({
      action: "dismiss",
      startedMs: Date.parse("2024-01-01T00:00:00.000Z"),
      runs: [{ stamp: "x", reviewed_at: "2024-01-01T00:02:00.000Z" }],
    }).landed,
  ]'
  [ "$status" -eq 0 ]
  [ "$output" = '[null,null]' ]
}
