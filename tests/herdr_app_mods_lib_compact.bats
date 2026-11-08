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
    const { findNode, isRail, viewOf, compactSections } = m;
    const tree268 = {id:'root', children:[{id:'sidebar', size:{px:268}}]};
    const tree120 = {id:'root', children:[{id:'sidebar', size:{px:120}}]};
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "viewOf: sidebar compact/rail; pane width compact/board" {
  _js '({a:viewOf({view:"sidebar"},268,tree268),b:viewOf({view:"sidebar"},268,tree120),c:viewOf({},400,null),d:viewOf({},900,null)})'
  [ "$status" -eq 0 ]
  jq -e '.a=="compact" and .b=="rail" and .c=="compact" and .d=="board"' <<<"$output"
}

@test "findNode: nested id, missing is null" {
  _js '({a:findNode({id:"root",children:[{id:"a",children:[{id:"nested"}]}]},"nested")?.id,b:findNode({id:"root"},"x")})'
  [ "$status" -eq 0 ]
  jq -e '.a=="nested" and .b==null' <<<"$output"
}

@test "compactSections: drops empty groups" {
  _js 'compactSections([{key:"open",label:"Open",items:[{n:1}]},{key:"landed",label:"Landed",items:[]}])'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].key=="open" and .[0].count==1 and (.[0].items|length)==1' <<<"$output"
}

@test "groupColumns: buckets by colOf and ignores unknown keys" {
  _js 'groupColumns([{n:1},{n:2},{n:3}], (p)=>p.n===1?"open":p.n===2?"landed":"nope", [{key:"open",label:"Open"},{key:"landed",label:"Landed"}])'
  [ "$status" -eq 0 ]
  jq -e 'length==2 and .[0].items[0].n==1 and .[1].items[0].n==2 and ([.[].items[]]|length)==2' <<<"$output"
}
