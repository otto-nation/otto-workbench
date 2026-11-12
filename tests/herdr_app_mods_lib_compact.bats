#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/compact.js — view mode, rail, compact sections.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() { load 'test_helper'; common_setup; }
teardown() { common_teardown; }

# $1 = JS expression over compact.js and groupColumns. Prints JSON.
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/compact.js');
    const { groupColumns } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/columns.js');
    const { viewOf, compactSections } = m;
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "viewOf: narrow is compact, wide is board" {
  _js '({a:viewOf(400),b:viewOf(900),c:viewOf(undefined)})'
  [ "$status" -eq 0 ]
  jq -e '.a=="compact" and .b=="board" and .c=="board"' <<<"$output"
}

@test "compactSections: drops empty groups" {
  _js 'compactSections([{key:"open",label:"Open",items:[{n:1}]},{key:"landed",label:"Landed",items:[]}])'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].key=="open" and .[0].count==1 and (.[0].items|length)==1 and .[0].status=="ready" and .[0].batch==null' <<<"$output"
}

@test "compactSections: keeps an empty group that is loading or failed, with its status and message" {
  _js 'compactSections([{key:"has-comments",label:"Has Comments",items:[],status:"loading",message:"Loading comments…",batch:null},{key:"landed",label:"Landed",items:[]}])'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"key":"has-comments","label":"Has Comments","count":0,"items":[],"batch":null,"status":"loading","message":"Loading comments…"}]' ]
}

@test "groupColumns: buckets by colOf and ignores unknown keys" {
  _js 'groupColumns([{n:1},{n:2},{n:3}], (p)=>p.n===1?"open":p.n===2?"landed":"nope", [{key:"open",label:"Open"},{key:"landed",label:"Landed"}])'
  [ "$status" -eq 0 ]
  jq -e 'length==2 and .[0].items[0].n==1 and .[1].items[0].n==2 and ([.[].items[]]|length)==2' <<<"$output"
}
