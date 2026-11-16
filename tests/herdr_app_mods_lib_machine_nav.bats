#!/usr/bin/env bats
# Tests for herdr-app machine navigation (pure: key, select, clear, page).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/machine_nav.js

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
    import * as n from '$REPO_ROOT/ai/herdr/app/mods/_lib/machine_nav.js';
    $1
  "
}

@test "machineKey prefers key, then on, else empty" {
  _js '
    process.stdout.write(JSON.stringify([
      n.machineKey({key:"a"}),
      n.machineKey({on:null}),
      n.machineKey({on:"homelab"}),
      n.machineKey({key:"a", on:"homelab"}),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["a","","homelab","a"]' ]
}

@test "selectMachine returns a new object; null key is null" {
  _js '
    const orig = {x:1};
    const selected = n.selectMachine(orig, "a");
    process.stdout.write(JSON.stringify({
      selected,
      same: orig === selected,
      orig,
      nullKey: n.selectMachine(null, null).machine,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"selected":{"x":1,"machine":"a"},"same":false,"orig":{"x":1},"nullKey":null}' ]
}

@test "clearMachine sets machine to null and keeps other fields" {
  _js '
    process.stdout.write(JSON.stringify(n.clearMachine({machine:"a",x:1})));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"machine":null,"x":1}' ]
}

@test "navStateOf maps raw shared values to nav state" {
  _js 'process.stdout.write(JSON.stringify([n.navStateOf(undefined), n.navStateOf(null), n.navStateOf(""), n.navStateOf("h")]))'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"machine":null},{"machine":null},{"machine":""},{"machine":"h"}]' ]
}

@test "pageOf matches by machineKey and misses home or unknown" {
  _js '
    const machines = [{on:null,key:""},{on:"h",key:"h"}];
    process.stdout.write(JSON.stringify({
      home: n.pageOf({machine:null}, machines),
      local: n.pageOf({machine:""}, machines)?.key ?? null,
      hit: n.pageOf({machine:"h"}, machines)?.key ?? null,
      miss: n.pageOf({machine:"zzz"}, machines),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"home":null,"local":"","hit":"h","miss":null}' ]
}
