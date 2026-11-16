#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/space_prs.js — space checkout rows joined to open PRs.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js tests/fixtures/board_document.json

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  LIB="$REPO_ROOT/ai/herdr/app/mods/_lib"
  FIX="$REPO_ROOT/tests/fixtures/board_document.json"
}

teardown() {
  common_teardown
}

# Print JSON of a JS expression. `spacePrs`, `rows` (fixture via rowToPr), `loaded` (docToState
# shape), `friendlyError` are in scope.
_js() {
  run node --input-type=module -e "
    import fs from 'node:fs';
    const { spacePrs } = await import('$LIB/space_prs.js');
    const d = await import('$LIB/board_doc.js');
    const { friendlyError } = await import('$LIB/scope.js');
    const doc = JSON.parse(fs.readFileSync('$FIX', 'utf8'));
    const rows = doc.rows.map(d.rowToPr);
    const loaded = (prs) => ({
      repo: { slug: doc.repos[0].slug, host: doc.repos[0].host },
      phase: 'loaded', rows: prs, error: null,
    });
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}


@test "one OPEN PR on the pane branch is that number, more 0, ready" {
  _js 'spacePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:"other",tabs:[{id:"t1"}]}],panes:[{tab:"t1",cwd:"/r",branch:"me/fix/comments"}],stateOf:()=>loaded(rows)})'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].pr.number==7 and .[0].more==0 and .[0].status=="ready" and .[0].spaceId=="s" and .[0].spaceName=="S" and .[0].branch=="me/fix/comments" and .[0].cwd=="/r" and .[0].on==null and .[0].message==null' <<<"$output"
}

@test "two OPEN PRs on the branch, higher number first in fixture order: lowest number, more 1" {
  _js '(() => { const branch = "feat/two"; const prs = [{...rows[1], branch}, {...rows[0], branch}]; return spacePrs({spaces:[{id:"s",name:"S",folder:"/r",branch,tabs:[{id:"t1"}]}],panes:[{tab:"t1",cwd:"/r",branch}],stateOf:()=>loaded(prs)}); })()'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].pr.number==7 and .[0].more==1 and .[0].status=="ready"' <<<"$output"
}

@test "MERGED and CLOSED PRs on the branch yield no row" {
  _js '(() => { const branch = rows[3].branch; const prs = [rows[3], {...rows[0], state:"CLOSED", branch}]; return spacePrs({spaces:[{id:"s",name:"S",folder:"/r",branch}],panes:[],stateOf:()=>loaded(prs)}); })()'
  [ "$status" -eq 0 ]
  jq -e 'length==0' <<<"$output"
}

@test "draft OPEN PR is kept with presentation.tags including Draft" {
  _js 'spacePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:"pat/feat/draft"}],panes:[],stateOf:()=>loaded(rows)})'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].pr.number==10 and .[0].pr.isDraft==true and (.[0].pr.presentation.tags | index("Draft")) != null' <<<"$output"
}

@test "a PR whose only CI is passing has empty presentation.row_badges" {
  _js '(() => { const pr = {...rows[0], presentation:{...rows[0].presentation, issues:[], row_badges:[]}}; return {ci: pr.presentation.ci, out: spacePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:pr.branch}],panes:[],stateOf:()=>loaded([pr])})}; })()'
  [ "$status" -eq 0 ]
  jq -e '.ci.status=="pass" and .ci.text=="CI passing" and (.out|length)==1 and .out[0].pr.presentation.row_badges==[]' <<<"$output"
}

@test "two spaces in the same repo both resolve through stateOf" {
  _js '(() => { const seen=[]; const st=loaded(rows); const out=spacePrs({spaces:[{id:"one",name:"One",folder:"/one",branch:"me/fix/comments"},{id:"two",name:"Two",folder:"/two",branch:"you/feat/wip"}],panes:[],stateOf:(t)=>{seen.push(t.cwd); return st;}}); return {ids:out.map((r)=>r.spaceId), nums:out.map((r)=>r.pr.number), seen}; })()'
  [ "$status" -eq 0 ]
  jq -e '.ids==["one","two"] and .nums==[7,8] and .seen==["/one","/two"]' <<<"$output"
}

@test "pane cwd branch wins over space.branch; another space pane with the same cwd does not" {
  _js 'spacePrs({spaces:[{id:"a",name:"A",folder:"/r",branch:"pat/feat/draft",tabs:[{id:"t1"}]},{id:"b",name:"B",folder:"/r",branch:"you/feat/wip",tabs:[{id:"t2"}]}],panes:[{tab:"t2",cwd:"/r",branch:"you/feat/wip"},{tab:"t1",cwd:"/r",branch:"me/fix/comments"}],stateOf:()=>loaded(rows)})'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].spaceId=="a" and .[0].branch=="me/fix/comments" and .[0].pr.number==7' <<<"$output"
}

@test "detached pane branch is no row; a space with no panes uses space.folder and space.branch" {
  _js '(() => { const st=loaded(rows); const detached=spacePrs({spaces:[{id:"d",name:"D",folder:"/r",branch:"me/fix/comments",tabs:[{id:"t1"}]}],panes:[{tab:"t1",cwd:"/r",branch:null}],stateOf:()=>st}); const fallback=spacePrs({spaces:[{id:"f",name:"F",folder:"/r",branch:"me/fix/comments"}],panes:[],stateOf:()=>st}); return {detached:detached.length, fallback:fallback.map((r)=>({id:r.spaceId,cwd:r.cwd,branch:r.branch,n:r.pr.number}))}; })()'
  [ "$status" -eq 0 ]
  jq -e '.detached==0 and (.fallback|length)==1 and .fallback[0].id=="f" and .fallback[0].cwd=="/r" and .fallback[0].branch=="me/fix/comments" and .fallback[0].n==7' <<<"$output"
}

@test "loading pulling with no rows; auth error message; skippable not-git drops the row" {
  _js '(() => { const spaces=[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}]; const loading=spacePrs({spaces,panes:[],stateOf:()=>({repo:{slug:"acme/box"},pulling:true,rows:undefined,error:null})}); const err={kind:"auth"}; const failed=spacePrs({spaces,panes:[],stateOf:()=>({repo:{slug:"acme/box"},error:err})}); const skip=spacePrs({spaces,panes:[],stateOf:()=>({repo:{slug:"acme/box"},error:{kind:"not-git"}})}); return {loading:loading.map((r)=>({status:r.status,pr:r.pr,more:r.more})), failed:failed.map((r)=>({status:r.status,pr:r.pr,message:r.message})), skip:skip.length, auth:friendlyError(err,null)}; })()'
  [ "$status" -eq 0 ]
  jq -e '.loading==[{"status":"loading","pr":null,"more":0}] and (.failed|length)==1 and .failed[0].status=="error" and .failed[0].pr==null and .failed[0].message==.auth and .auth=="run gh auth login on this Mac" and .skip==0' <<<"$output"
}

@test "spaces() order is preserved" {
  _js 'spacePrs({spaces:[{id:"later",name:"Later",folder:"/b",branch:"me/fix/comments"},{id:"first",name:"First",folder:"/a",branch:"you/feat/wip"}],panes:[],stateOf:()=>loaded(rows)})'
  [ "$status" -eq 0 ]
  jq -e 'length==2 and .[0].spaceId=="later" and .[0].pr.number==7 and .[1].spaceId=="first" and .[1].pr.number==8' <<<"$output"
}
