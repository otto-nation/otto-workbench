#!/usr/bin/env bats
# Style guards for the herdr-app mods: the brand DESIGN.md rules a grep can see.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_ui/*.js ai/herdr/app/mods/prs/*.js

setup() {
  load 'test_helper'
  common_setup
  MODS="$REPO_ROOT/ai/herdr/app/mods"
}

teardown() {
  common_teardown
}

@test "no hover callback changes a border colour (single-line match only)" {
  run grep -nE '\.hover\([^)]*\)[^;]*border_color|\.hover\(\(s\) => s[^;]*border_color' \
    "$MODS"/_ui/*.js "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
}

@test "the brand forbids pills: no pill( call or export remains" {
  run grep -nE '\bpill\(' "$MODS"/_ui/*.js "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
}

@test "the fail status glyph is ×, never ✗, which no brand font draws" {
  run grep -n '✗' "$MODS"/_ui/*.js "$MODS"/prs/*.js
  [ "$status" -eq 1 ]
  grep -q 'fail: { glyph: "×"' "$MODS/_ui/ui.js"
}

@test "load_status indicators are static: no spin, frames, loop, or transition" {
  run grep -nE '\.(spin|frames|loop|transition)\(' "$MODS/prs/load_status.js"
  [ "$status" -eq 1 ]
}

@test "load_status uses SP.sm gap on status text" {
  grep -q 'gap: SP.sm' "$MODS/prs/load_status.js"
}
