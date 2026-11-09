#!/usr/bin/env bats
# Tests for herdr-app hc terminal liveness helpers.
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

# Evaluate EXPR against busy.js as m; print JSON.stringify(EXPR).
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/busy.js');
    const list = [
      {address:'t7',state:'live',labels:{'otto.pr':'o/n#7','otto.action':'review'}},
      {id:'homelab/t8',state:'live',labels:{'otto.pr':'o/n#8','otto.action':'rerun'}},
      {id:'t9',state:'finished',exit_code:1,finished_ms:100,labels:{'otto.pr':'o/n#9','otto.action':'review'}},
      {id:'t10',state:'live',labels:{'otto.pr':'p/n#10'}},
      {id:'t11',state:'live'},
    ];
    const toArr = (lab) => lab ? Object.entries(lab).map(([k,v]) => k+'='+v) : lab;
    const arrList = list.map((t) => ({...t, labels: toArr(t.labels)}));
    process.stdout.write(JSON.stringify($1));
  "
}

@test "lsArgv is hc ls --all --json" {
  _js 'm.lsArgv()'
  [ "$status" -eq 0 ]
  [ "$output" = '["ls","--all","--json"]' ]
}

@test "terminalsOf empty on null, object, and string; skips malformed" {
  _js '[m.terminalsOf(null),m.terminalsOf({}),m.terminalsOf("x"),m.terminalsOf([null,"x",1,{id:"t7",state:"live"}]).map(e=>e.id)]'
  [ "$status" -eq 0 ]
  [ "$output" = '[[],[],[],["t7"]]' ]
}

@test "terminalsOf wrapper matches array ids" {
  _js '[m.terminalsOf(list).map(e=>e.id),m.terminalsOf({terminals:list}).map(e=>e.id)]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["t7","homelab/t8","t9","t10","t11"],["t7","homelab/t8","t9","t10","t11"]]' ]
}

@test "busyMap keys live o/n only and keeps homelab/t8 verbatim" {
  _js '(()=>{const b=m.busyMap(m.terminalsOf(list),"o/n");return [[...b.keys()],b.get(8)];})()'
  [ "$status" -eq 0 ]
  [ "$output" = '[[7,8],{"terminal":"homelab/t8","action":"rerun"}]' ]
}

@test "array-form labels yield the same busyMap" {
  _js '(()=>{const b=m.busyMap(m.terminalsOf(arrList),"o/n");return [[...b.keys()],b.get(8)];})()'
  [ "$status" -eq 0 ]
  [ "$output" = '[[7,8],{"terminal":"homelab/t8","action":"rerun"}]' ]
}

@test "latestFinished picks newest finishedMs" {
  _js '[m.latestFinished(m.terminalsOf(list),"o/n",9),m.latestFinished(m.terminalsOf(list),"o/n",7),m.latestFinished([...m.terminalsOf(list),{id:"t9b",machine:null,state:"finished",exitCode:0,finishedMs:50,labels:{"otto.pr":"o/n#9","otto.action":"rerun"}}],"o/n",9)]'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"terminal":"t9","exitCode":1,"action":"review"},null,{"terminal":"t9","exitCode":1,"action":"review"}]' ]
}

@test "mergePending keeps pending unless finished and does not mutate" {
  _js '(()=>{const entries=m.terminalsOf(list);const snap=JSON.stringify(entries);const busy=m.busyMap(entries,"o/n");const pending={11:"t-new",9:"t9"};const pSnap=JSON.stringify(pending);const size=busy.size;const merged=m.mergePending(busy,pending,entries);return {keys:[...merged.keys()],t11:merged.get(11),has9:merged.has(9),busySize:busy.size===size,pendingSame:JSON.stringify(pending)===pSnap,entriesSame:JSON.stringify(entries)===snap};})()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"keys":[7,8,11],"t11":{"terminal":"t-new","action":null},"has9":false,"busySize":true,"pendingSame":true,"entriesSame":true}' ]
}
