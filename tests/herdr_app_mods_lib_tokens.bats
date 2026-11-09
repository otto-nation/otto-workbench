#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/tokens.js — densityOf breakpoints.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() { load 'test_helper'; common_setup; }
teardown() { common_teardown; }

# $1 = JS expression over tokens.js. Prints JSON.
_js() {
  run node --input-type=module -e "
    const { densityOf } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/tokens.js');
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "densityOf: rail/compact/board/wide boundaries; undefined is board" {
  _js '({a:densityOf(159),b:densityOf(160),c:densityOf(559),d:densityOf(560),e:densityOf(1099),f:densityOf(1100),g:densityOf(undefined)})'
  [ "$status" -eq 0 ]
  jq -e '.a=="rail" and .b=="compact" and .c=="compact" and .d=="board" and .e=="board" and .f=="wide" and .g=="board"' <<<"$output"
}
