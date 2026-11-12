#!/usr/bin/env bats
# Tests for herdr-app owedDue cadence on the board.
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

_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js');
    $1
  "
}


@test "owedDue is true when owedAt is missing or the ttl has elapsed" {
  _js '
    const t = 1e12;
    const ttl = m.OWED_TTL_MS;
    process.stdout.write(JSON.stringify([
      m.owedDue(undefined, t),
      m.owedDue(null, t),
      m.owedDue(NaN, t),
      m.owedDue(t - ttl, t),
      m.owedDue(t - ttl - 1, t),
      m.owedDue(t - ttl + 1, t),
      m.owedDue(t - 10, t, 10),
      m.owedDue(t - 9, t, 10),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,true,true,true,false,true,false]' ]
}
