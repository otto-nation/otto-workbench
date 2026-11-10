#!/usr/bin/env bats
# Tests for herdr-app boardTabId (reuse the open board tab).
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
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/tabs.js');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "an existing named PRs tab is found, active space first" {
  _js 'm.boardTabId([{active:false,tabs:[{id:"o",name:"PRs",named:true}]},{active:true,tabs:[{id:"a",name:"PRs",named:true}]}],"PRs")'
  [ "$output" = '"a"' ]
}

@test "only the panel copy offers Open board, whatever its width" {
  _js '[m.offersOpenBoard({placement:"panel"}), m.offersOpenBoard({}), m.offersOpenBoard(undefined), m.offersOpenBoard({placement:"tab"})]'
  [ "$output" = '[true,false,false,false]' ]
}

@test "falls back to another space; none, unnamed or other names give null" {
  _js '[m.boardTabId([{active:true,tabs:[]},{active:false,tabs:[{id:"o",name:"PRs",named:true}]}],"PRs"), m.boardTabId([{active:true,tabs:[{id:"x",name:"PRs",named:false}]}],"PRs"), m.boardTabId([],"PRs"), m.boardTabId(null,"PRs")]'
  [ "$output" = '["o",null,null,null]' ]
}
