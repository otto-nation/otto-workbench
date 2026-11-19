#!/usr/bin/env bats
# Tests for the Workbench hub ids and tiles (pure: workbench.js).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/workbench.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# _js BODY — runs BODY with s (workbench.js) and out(v) in scope.
# BODY must not contain dollar-brace or backticks: bash substitutes it once.
_js() {
  run node --input-type=module -e "
    import * as s from '$REPO_ROOT/ai/herdr/app/mods/_lib/workbench.js';
    const out = (v) => process.stdout.write(JSON.stringify(v));
    $1
  "
}

@test "HUB_TILES order and hub id" {
  _js 'out([s.WORKBENCH_HUB, s.WORKBENCH_CHILDREN, s.HUB_TILES.map((t) => [t.label, t.child])])'
  [ "$output" = '["workbench-panel",["workbench-panel","prs-panel","queue-panel","hosts-panel","sessions-panel","settings-panel"],[["PRs","prs-panel"],["Queue","queue-panel"],["Hosts","hosts-panel"],["Sessions","sessions-panel"],["Settings","settings-panel"]]]' ]
}
