#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/tokens.js — breakpoints and layout tokens.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() { load 'test_helper'; common_setup; }
teardown() { common_teardown; }

@test "tokens: breakpoints and layout tokens exported" {
  run node --input-type=module -e "
    const t = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/tokens.js');
    process.stdout.write(JSON.stringify({b:t.BREAKPOINT,l:t.LAYOUT,d:typeof t.densityOf}));
  "
  [ "$status" -eq 0 ]
  jq -e '.b.rail==160 and .b.compact==560 and .l.colMin==260 and .l.headerH==52 and .d=="undefined"' <<<"$output"
}
