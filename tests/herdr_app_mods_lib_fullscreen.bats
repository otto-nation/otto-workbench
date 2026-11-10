#!/usr/bin/env bats
# Tests for herdr-app PRs panel full-screen helpers.
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
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/fullscreen.js');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "fullscreenState is off when there is no root, main is missing, or main is shown" {
  _js '[m.fullscreenState(null), m.fullscreenState({id:"root",children:[]}), m.fullscreenState({id:"root",children:[{id:"main"}]})]'
  [ "$output" = '["off","off","off"]' ]
}

@test "fullscreenState is on when main is hidden and the PRs panel is showing" {
  _js 'm.fullscreenState({id:"root",children:[{id:"main",hidden:true},{id:"panel",children:[{id:"panel-body",active:"prs-panel"}]}]})'
  [ "$output" = '"on"' ]
}

@test "fullscreenState is stranded when the panel is hidden, missing, or not on PRs" {
  _js '[m.fullscreenState({id:"root",children:[{id:"main",hidden:true},{id:"panel",hidden:true,children:[{id:"panel-body",active:"prs-panel"}]}]}), m.fullscreenState({id:"root",children:[{id:"main",hidden:true}]}), m.fullscreenState({id:"root",children:[{id:"main",hidden:true},{id:"panel",children:[{id:"panel-body",active:"files"}]}]}), m.fullscreenState({id:"root",children:[{id:"main",hidden:true},{id:"panel"}]})]'
  [ "$output" = '["stranded","stranded","stranded","stranded"]' ]
}

@test "enterPlan saves a px size and always hides main then weights the panel" {
  _js '[m.enterPlan({children:[{id:"panel",size:{px:368}}]}), m.enterPlan({children:[{id:"panel",size:{weight:1}}]}), m.enterPlan({children:[]})]'
  [ "$output" = '[{"savedPx":368,"steps":[{"command":"layout.hide","args":{"id":"main"}},{"command":"layout.resize","args":{"id":"panel","weight":1}}]},{"savedPx":null,"steps":[{"command":"layout.hide","args":{"id":"main"}},{"command":"layout.resize","args":{"id":"panel","weight":1}}]},{"savedPx":null,"steps":[{"command":"layout.hide","args":{"id":"main"}},{"command":"layout.resize","args":{"id":"panel","weight":1}}]}]' ]
}

@test "exitSteps shows main and resizes only for a saved px" {
  _js '[m.exitSteps(368), m.exitSteps(null), m.exitSteps(0), m.exitSteps(NaN)]'
  [ "$output" = '[[{"command":"layout.show","args":{"id":"main"}},{"command":"layout.resize","args":{"id":"panel","px":368}}],[{"command":"layout.show","args":{"id":"main"}}],[{"command":"layout.show","args":{"id":"main"}}],[{"command":"layout.show","args":{"id":"main"}}]]' ]
}

@test "only the panel copy is a panel copy" {
  _js '[m.isPanelCopy({placement:"panel"}), m.isPanelCopy({}), m.isPanelCopy(undefined), m.isPanelCopy({placement:"tab"})]'
  [ "$output" = '[true,false,false,false]' ]
}
