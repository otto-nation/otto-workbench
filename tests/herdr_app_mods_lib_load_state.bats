#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/load_state.js — delayed loading line, progress, strip.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() { load 'test_helper'; common_setup; }
teardown() { common_teardown; }

# $1 = JS expression over every load_state.js export. Prints JSON.
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/load_state.js');
    const { LOAD_DELAY_MS, loadState } = m;
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "loadState: 999ms is not shown; 1000ms is shown" {
  _js '({a:loadState([{phase:"loading"}],{since:0,now:999}).shown,b:loadState([{phase:"loading"}],{since:0,now:1000}).shown,d:LOAD_DELAY_MS})'
  [ "$status" -eq 0 ]
  jq -e '.a==false and .b==true and .d==1000' <<<"$output"
}

@test "loadState: since null is not shown" {
  _js 'loadState([{phase:"loading"}],{since:null,now:5000}).shown'
  [ "$status" -eq 0 ]
  jq -e '.==false' <<<"$output"
}

@test "loadState: loaded+loading+queued line is Loading 1 of 3 repos" {
  _js 'loadState([{phase:"loaded"},{phase:"loading"},{phase:"queued"}],{since:0,now:1000})'
  [ "$status" -eq 0 ]
  jq -e '.busy==true and .shown==true and .line=="Loading 1 of 3 repos" and .progress==33' <<<"$output"
}

@test "loadState: hydrating line updates CI and review status" {
  _js 'loadState([{phase:"hydrating"},{phase:"loaded"}],{since:0,now:1000})'
  [ "$status" -eq 0 ]
  jq -e '.busy==true and .line=="Updating CI and review status · 1 of 2 repos" and .progress==50' <<<"$output"
}

@test "loadState: settled is not busy; strip is only stale and failed" {
  _js 'loadState([{phase:"loaded",slug:"acme/ok"},{phase:"stale",slug:"acme/old"},{phase:"failed",slug:"acme/bad"}],{since:0,now:1000})'
  [ "$status" -eq 0 ]
  jq -e '.busy==false and .shown==false and .line==null and .progress==null and (.strip|length)==2 and .strip[0].name=="old" and .strip[0].kind=="warn" and .strip[1].name=="bad" and .strip[1].kind=="fail"' <<<"$output"
}

@test "loadState: strip kinds and folder fallback" {
  _js 'loadState([{phase:"queued",slug:"acme/box"},{phase:"loading",slug:"acme/kit"},{phase:"hydrating",folder:"hydra"},{phase:"stale",slug:"acme/old"},{phase:"failed",slug:"acme/bad"},{phase:"loaded",slug:"acme/ok"}],{since:0,now:1000}).strip'
  [ "$status" -eq 0 ]
  jq -e 'length==5 and .[0]=={"name":"box","kind":"none"} and .[1]=={"name":"kit","kind":"running"} and .[2]=={"name":"hydra","kind":"running"} and .[3]=={"name":"old","kind":"warn"} and .[4]=={"name":"bad","kind":"fail"}' <<<"$output"
}

@test "loadState: undefined input is empty" {
  _js 'loadState(undefined)'
  [ "$status" -eq 0 ]
  jq -e '.busy==false and .shown==false and .line==null and .progress==null and (.strip|length)==0' <<<"$output"
}

@test "errorStatus and errorsStatus: stale with age, failed, one reads as errorStatus, several as N failed" {
  _js '[m.errorStatus({updatedAt:1000},121000), m.errorStatus({updatedAt:null},5), m.errorsStatus([],5), m.errorsStatus([{updatedAt:1000}],121000), m.errorsStatus([{updatedAt:1000},{updatedAt:null}],5)]'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"status","status":"warn","text":"stale · 2m ago"},{"kind":"status","status":"fail","text":"failed"},null,{"kind":"status","status":"warn","text":"stale · 2m ago"},{"kind":"status","status":"fail","text":"2 failed"}]' ]
}
