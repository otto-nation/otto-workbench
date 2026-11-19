#!/usr/bin/env bats
# Tests for herdr-app tab-bar panel toggle helpers.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/panel_toggle.js ai/herdr/app/mods/_lib/fullscreen.js

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
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/panel_toggle.js');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "panelOn is true only when the panel is showing that child" {
  _js '[m.panelOn({id:"r",children:[{id:"panel",children:[{id:"panel-body",active:"queue-panel"}]}]}, "queue-panel"), m.panelOn({id:"r",children:[{id:"panel",hidden:true,children:[{id:"panel-body",active:"queue-panel"}]}]}, "queue-panel"), m.panelOn({id:"r",children:[{id:"panel",children:[{id:"panel-body",active:"prs-panel"}]}]}, "queue-panel"), m.panelOn(null, "queue-panel")]'
  [ "$output" = '[true,false,false,false]' ]
}

@test "panelActive is the shown child or null" {
  _js '[m.panelActive({id:"r",children:[{id:"panel",children:[{id:"panel-body",active:"queue-panel"}]}]}), m.panelActive({id:"r",children:[{id:"panel",hidden:true,children:[{id:"panel-body",active:"queue-panel"}]}]}), m.panelActive(null)]'
  [ "$output" = '["queue-panel",null,null]' ]
}

@test "groupOn is true for any listed child while the panel shows" {
  _js '[m.groupOn({id:"r",children:[{id:"panel",children:[{id:"panel-body",active:"sessions-panel"}]}]}, ["workbench-panel","sessions-panel"]), m.groupOn({id:"r",children:[{id:"panel",children:[{id:"panel-body",active:"files"}]}]}, ["workbench-panel","sessions-panel"]), m.groupOn({id:"r",children:[{id:"panel",hidden:true,children:[{id:"panel-body",active:"sessions-panel"}]}]}, ["workbench-panel","sessions-panel"])]'
  [ "$output" = '[true,false,false]' ]
}

@test "groupClickSteps: hidden shows hub, hub hides, destination returns to hub" {
  _js '[m.groupClickSteps(null, "workbench-panel", ["workbench-panel","queue-panel"]), m.groupClickSteps("workbench-panel", "workbench-panel", ["workbench-panel","queue-panel"]), m.groupClickSteps("queue-panel", "workbench-panel", ["workbench-panel","queue-panel"])]'
  [ "$output" = '[[{"command":"layout.select","args":{"stack":"panel-body","child":"workbench-panel"}},{"command":"layout.show","args":{"id":"panel"}}],[{"command":"layout.hide","args":{"id":"panel"}}],[{"command":"layout.select","args":{"stack":"panel-body","child":"workbench-panel"}}]]' ]
}

@test "enableWorkspacePanels drops files and changes and adds every missing child once" {
  _js '[m.enableWorkspacePanels(undefined, ["workbench-panel","prs-panel"]), m.enableWorkspacePanels(["files","changes","workbench-panel","prs-panel"], ["workbench-panel","prs-panel"]), m.enableWorkspacePanels(["files","changes","workbench-panel"], ["workbench-panel","prs-panel"]), m.enableWorkspacePanels(["workbench-panel","prs-panel"], ["workbench-panel","prs-panel"])]'
  [ "$output" = '[["workbench-panel","prs-panel"],["workbench-panel","prs-panel"],["workbench-panel","prs-panel"],null]' ]
}
