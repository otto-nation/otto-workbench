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

@test "syncWorkspacePanels drops files and changes and adds the child once" {
  _js '[m.syncWorkspacePanels(undefined, "queue-panel"), m.syncWorkspacePanels(["files","changes","queue-panel"], "queue-panel"), m.syncWorkspacePanels(["files","changes","prs-panel"], "queue-panel"), m.syncWorkspacePanels(["queue-panel"], "queue-panel")]'
  [ "$output" = '[["queue-panel"],["queue-panel"],["prs-panel","queue-panel"],null]' ]
}

@test "clickSteps hide the panel when on and select the child when off" {
  _js '[m.clickSteps(true, "queue-panel"), m.clickSteps(false, "queue-panel")]'
  [ "$output" = '[[{"command":"layout.hide","args":{"id":"panel"}}],[{"command":"layout.select","args":{"stack":"panel-body","child":"queue-panel"}},{"command":"layout.show","args":{"id":"panel"}}]]' ]
}
