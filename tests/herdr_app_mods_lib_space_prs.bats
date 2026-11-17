#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/space_prs.js — OPEN PRs with a worktree, grouped by repo.
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

# Print JSON of a JS expression. `spaceWorktreePrs`, `WORKTREES_OUTDATED_TEXT`,
# `rows` (fixture via rowToPr), `loaded` (docToState shape), `friendlyError` are in scope.
_js() {
  run node --input-type=module -e "
    import fs from 'node:fs';
    const { spaceWorktreePrs, WORKTREES_OUTDATED_TEXT } = await import('$LIB/space_prs.js');
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


@test "two spaces on one repo yield one group named after the first" {
  _js '(() => { const prs=[{...rows[0],worktree:"/wt/a"}]; return spaceWorktreePrs({spaces:[{id:"one",name:"One",folder:"/one",branch:"me/fix/comments"},{id:"two",name:"Two",folder:"/two",branch:"you/feat/wip"}],panes:[],stateOf:()=>loaded(prs)}); })()'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].spaceId=="one" and .[0].spaceName=="One" and .[0].status=="ready" and .[0].rows[0].pr.number==7' <<<"$output"
}

@test "an OPEN PR with a worktree is a row; one without is none" {
  _js '(() => { const spaces=[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}]; const withWt=spaceWorktreePrs({spaces,panes:[],stateOf:()=>loaded([{...rows[0],worktree:"/wt/a"}])}); const without=spaceWorktreePrs({spaces,panes:[],stateOf:()=>loaded([rows[0]])}); return {withWt, without}; })()'
  [ "$status" -eq 0 ]
  jq -e '(.withWt|length)==1 and .withWt[0].rows[0].pr.number==7 and .withWt[0].rows[0].worktree=="/wt/a" and (.without|length)==0' <<<"$output"
}

@test "MERGED with a worktree yields no group" {
  _js 'spaceWorktreePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:"you/feat/landed"}],panes:[],stateOf:()=>loaded([{...rows[3],worktree:"/wt/m"}])})'
  [ "$status" -eq 0 ]
  jq -e 'length==0' <<<"$output"
}

@test "two OPEN PRs on one branch: lowest number, more 1" {
  _js '(() => { const branch="feat/two"; const prs=[{...rows[1],branch,worktree:"/wt/x"},{...rows[0],branch,worktree:"/wt/x"}]; return spaceWorktreePrs({spaces:[{id:"s",name:"S",folder:"/r",branch}],panes:[],stateOf:()=>loaded(prs)}); })()'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and (.[0].rows|length)==1 and .[0].rows[0].pr.number==7 and .[0].rows[0].more==1' <<<"$output"
}

@test "worktree PR rows are ordered by number" {
  _js '(() => { const prs=[{...rows[1],worktree:"/wt/b"},{...rows[0],worktree:"/wt/a"}]; return spaceWorktreePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}],panes:[],stateOf:()=>loaded(prs)}); })()'
  [ "$status" -eq 0 ]
  jq -e '(.[0].rows|map(.pr.number))==[7,8] and (.[0].rows|map(.worktree))==["/wt/a","/wt/b"]' <<<"$output"
}

@test "owner from a labelled pane uses tab_name; another space prefixes space_name" {
  _js '(() => { const prs=[{...rows[0],worktree:"/wt/x"}]; const spaces=[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}]; const panes=[{terminal:"t7",tab:"tb",cwd:"main",branch:"me/fix/comments",status:"idle",focused:false,pane:"p1"}]; const terminals=[{address:"t7",labels:{"otto.worktree":"/wt/x"}}]; const same=spaceWorktreePrs({spaces,panes,terminals,agents:[{address:"t7",pane:"p1",space:"s",space_name:"S",tab:"tb",tab_name:"Fix comments",app:"pi"}],stateOf:()=>loaded(prs)}); const other=spaceWorktreePrs({spaces,panes,terminals,agents:[{address:"t7",pane:"p1",space:"o",space_name:"Other",tab:"tb",tab_name:"Fix comments",app:"pi"}],stateOf:()=>loaded(prs)}); return {same:same[0].rows[0].owner, other:other[0].rows[0].owner}; })()'
  [ "$status" -eq 0 ]
  jq -e '.same.terminal=="t7" and .same.pane=="p1" and .same.name=="Fix comments" and .other.name=="Other / Fix comments"' <<<"$output"
}

@test "a shell pane in the worktree is not an owner" {
  _js '(() => { const prs=[{...rows[0],worktree:"/wt/x"}]; const panes=[{terminal:"t9",tab:"tb",cwd:"/wt/x",branch:"me/fix/comments",status:null,focused:false,pane:"zsh",app:"zsh"}]; return spaceWorktreePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}],panes,terminals:[],agents:[],stateOf:()=>loaded(prs)}); })()'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].rows[0].owner==null' <<<"$output"
}

@test "features without worktrees is outdated with WORKTREES_OUTDATED_TEXT" {
  _js '(() => { const st={...loaded([{...rows[0],worktree:"/wt/a"}]),features:["prs"]}; return {out:spaceWorktreePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}],panes:[],stateOf:()=>st}), text:WORKTREES_OUTDATED_TEXT}; })()'
  [ "$status" -eq 0 ]
  jq -e '(.out|length)==1 and .out[0].status=="outdated" and .out[0].message==.text and .out[0].rows==[] and (.text|length)>0' <<<"$output"
}

@test "features null is ready, not outdated" {
  _js '(() => { const st={...loaded([{...rows[0],worktree:"/wt/a"}]),features:null}; return spaceWorktreePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}],panes:[],stateOf:()=>st}); })()'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].status=="ready" and .[0].rows[0].pr.number==7' <<<"$output"
}

@test "a ready group with no rows is dropped" {
  _js 'spaceWorktreePrs({spaces:[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}],panes:[],stateOf:()=>loaded([])})'
  [ "$status" -eq 0 ]
  jq -e 'length==0' <<<"$output"
}

@test "loading and error keep the group" {
  _js '(() => { const spaces=[{id:"s",name:"S",folder:"/r",branch:"me/fix/comments"}]; const loading=spaceWorktreePrs({spaces,panes:[],stateOf:()=>({repo:{slug:"acme/box"},pulling:true,rows:undefined,error:null})}); const failed=spaceWorktreePrs({spaces,panes:[],stateOf:()=>({repo:{slug:"acme/box"},error:{kind:"auth"}})}); return {loading, failed, auth:friendlyError({kind:"auth"},null)}; })()'
  [ "$status" -eq 0 ]
  jq -e '(.loading|length)==1 and .loading[0].status=="loading" and .loading[0].rows==[] and (.failed|length)==1 and .failed[0].status=="error" and .failed[0].message==.auth' <<<"$output"
}
