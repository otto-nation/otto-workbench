#!/usr/bin/env bats
# Tests for herdr-app batch launch grouping and argv helpers.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# Evaluate EXPR against batch_launch.js as m; print JSON.stringify(EXPR).
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch_launch.js');
    process.stdout.write(JSON.stringify($1));
  "
}

_throws() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch_launch.js');
    try { $1; process.stdout.write('no-throw'); }
    catch { process.stdout.write('throw'); }
  "
}

@test "groupByRepo splits two repos on two machines" {
  _js 'm.groupByRepo([{repoSlug:"a/one",number:1,on:"m1",cwd:"/c1"},{repoSlug:"b/two",number:2,on:"m2",cwd:"/c2"}])'
  [ "$status" -eq 0 ]
  [ "$output" = '{"groups":[{"repo":"a/one","on":"m1","cwd":"/c1","keys":["a/one#1"]},{"repo":"b/two","on":"m2","cwd":"/c2","keys":["b/two#2"]}],"unrunnable":[]}' ]
}

@test "groupByRepo same repo on two machines is two groups" {
  _js 'm.groupByRepo([{repoSlug:"a/one",number:1,on:"m1",cwd:"/c1"},{repoSlug:"a/one",number:2,on:"m2",cwd:"/c2"}])'
  [ "$status" -eq 0 ]
  [ "$output" = '{"groups":[{"repo":"a/one","on":"m1","cwd":"/c1","keys":["a/one#1"]},{"repo":"a/one","on":"m2","cwd":"/c2","keys":["a/one#2"]}],"unrunnable":[]}' ]
}

@test "groupByRepo missing cwd is unrunnable; bad records are skipped" {
  _js 'm.groupByRepo([{repoSlug:"a/one",number:3,on:"m1",cwd:null},{repoSlug:"a/one",number:4,on:"m1",cwd:""},{repoSlug:"",number:1,cwd:"/x"},{number:1,cwd:"/x"},{repoSlug:"a/one",number:"x",cwd:"/x"},{repoSlug:"a/one",number:1.5,cwd:"/x"}])'
  [ "$status" -eq 0 ]
  [ "$output" = '{"groups":[],"unrunnable":[{"key":"a/one#3","reason":"no checkout of this repo on its machine"},{"key":"a/one#4","reason":"no checkout of this repo on its machine"}]}' ]
}

@test "runArgv default three steps" {
  _js 'm.runArgv({keys:["o/r#1","o/r#2"],steps:["rebase","ci","comments"],checkout:"/co"})'
  [ "$status" -eq 0 ]
  [ "$output" = '["batch","run","--checkout","/co","--prs","o/r#1,o/r#2","--steps","rebase,ci,comments"]' ]
}

@test "runArgv with review drops review from --steps and adds --with-review" {
  _js 'm.runArgv({keys:["o/r#1"],steps:["rebase","ci","comments","review"],checkout:"/co"})'
  [ "$status" -eq 0 ]
  [ "$output" = '["batch","run","--checkout","/co","--prs","o/r#1","--steps","rebase,ci,comments","--with-review"]' ]
}

@test "runArgv withAi is filtered to rebase|ci|comments" {
  _js 'm.runArgv({keys:["o/r#1"],steps:["rebase","ci","comments"],withAi:["rebase","review","ci"],checkout:"/co"})'
  [ "$status" -eq 0 ]
  [ "$output" = '["batch","run","--checkout","/co","--prs","o/r#1","--steps","rebase,ci,comments","--with-ai","rebase,ci"]' ]
}

@test "runArgv autoPublish adds --auto-publish ff" {
  _js 'm.runArgv({keys:["o/r#1"],steps:["rebase","ci","comments"],autoPublish:true,checkout:"/co"})'
  [ "$status" -eq 0 ]
  [ "$output" = '["batch","run","--checkout","/co","--prs","o/r#1","--steps","rebase,ci,comments","--auto-publish","ff"]' ]
}

@test "runArgv sends --rebase behind only when asked and rebase is a step" {
  _js '[m.runArgv({keys:["o/r#1"],steps:["rebase","ci"],rebaseBehind:true,checkout:"/co"}), m.runArgv({keys:["o/r#1"],steps:["rebase"],checkout:"/co"}), m.runArgv({keys:["o/r#1"],steps:["ci"],rebaseBehind:true,checkout:"/co"})]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["batch","run","--checkout","/co","--prs","o/r#1","--steps","rebase,ci","--rebase","behind"],["batch","run","--checkout","/co","--prs","o/r#1","--steps","rebase"],["batch","run","--checkout","/co","--prs","o/r#1","--steps","ci"]]' ]
}

@test "launchOutcome: started on run_started; otherwise the last line is the error" {
  _js '(() => { const started = "plan…\n{\"schema_version\": 1, \"kind\": \"run_started\", \"run\": \"r1\"}\n{\"schema_version\": 1, \"kind\": \"item_queued\"}\n"; const failed = "⚠ Transient failure on graphql (attempt 1/3)\npr batch: GitHub query failed: gh: HTTP 504\n\n  \n"; return [m.launchOutcome(started).started, m.launchOutcome(failed), m.launchOutcome(""), m.launchOutcome(null)]; })()'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,{"started":false,"error":"pr batch: GitHub query failed: gh: HTTP 504"},{"started":false,"error":""},{"started":false,"error":""}]' ]
}

@test "runArgv selects follow keys order and imply --with-review" {
  _js 'm.runArgv({keys:["o/r#2","o/r#1","o/r#3"],steps:["rebase","ci"],selects:{"o/r#3":["ci"],"o/r#1":["review"],"o/r#2":["rebase"],"o/r#9":["ci"]},checkout:"/co"})'
  [ "$status" -eq 0 ]
  [ "$output" = '["batch","run","--checkout","/co","--prs","o/r#2,o/r#1,o/r#3","--steps","rebase,ci","--with-review","--select","o/r#2=rebase","--select","o/r#1=review","--select","o/r#3=ci"]' ]
}

@test "runArgv focus is trimmed and blanks dropped" {
  _js 'm.runArgv({keys:["o/r#1"],steps:["rebase"],focus:["  a  ","","","   ","b"],checkout:"/co"})'
  [ "$status" -eq 0 ]
  [ "$output" = '["batch","run","--checkout","/co","--prs","o/r#1","--steps","rebase","--focus","a","--focus","b"]' ]
}

@test "runArgv review-only omits --steps and selects review per key" {
  _js 'm.runArgv({keys:["o/r#1","o/r#2"],steps:["review"],checkout:"/co"})'
  [ "$status" -eq 0 ]
  [ "$output" = '["batch","run","--checkout","/co","--prs","o/r#1,o/r#2","--with-review","--select","o/r#1=review","--select","o/r#2=review"]' ]
}

@test "runArgv empty keys throws" {
  _throws 'm.runArgv({keys:[],steps:["rebase"],checkout:"/co"})'
  [ "$status" -eq 0 ]
  [ "$output" = 'throw' ]
}

@test "cancelArgv without kill" {
  _js 'm.cancelArgv("run-1")'
  [ "$status" -eq 0 ]
  [ "$output" = '["--schema-version","2","batch","cancel","run-1"]' ]
}

@test "cancelArgv with kill" {
  _js 'm.cancelArgv("run-1",{kill:true})'
  [ "$status" -eq 0 ]
  [ "$output" = '["--schema-version","2","batch","cancel","--kill","run-1"]' ]
}

@test "cancelArgv empty runId throws" {
  _throws 'm.cancelArgv("")'
  [ "$status" -eq 0 ]
  [ "$output" = 'throw' ]
}

@test "runProgress counts finished lanes; not-ok is zeros" {
  _js '[m.runProgress({ok:true,items:[{status:"done"},{status:"awaiting_decision"},{status:"failed"},{status:"running"},{status:"queued"},{status:"waiting_admission"},{status:"done",findings:[{}]}]}),m.runProgress({ok:false}),m.runProgress(null)]'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"done":4,"total":7},{"done":0,"total":0},{"done":0,"total":0}]' ]
}

@test "STEP_NAMES lists the four batch steps" {
  _js 'm.STEP_NAMES'
  [ "$status" -eq 0 ]
  [ "$output" = '["rebase","ci","comments","review"]' ]
}

@test "formFacets repos first-seen order, labels sorted case-insensitively, deduped" {
  _js 'm.formFacets([{repoSlug:"b/two",labels:["Zebra","alpha"]},{repoSlug:"a/one",labels:["ALPHA","bug"]},{repoSlug:"b/two",labels:["gamma"]},{repoSlug:"B/Two",labels:["bug"]}])'
  [ "$status" -eq 0 ]
  [ "$output" = '{"repos":["b/two","a/one"],"labels":["alpha","bug","gamma","Zebra"]}' ]
}

@test "formFacets empty list and missing labels" {
  _js '[m.formFacets([]),m.formFacets(null),m.formFacets([{repoSlug:"a/one"}])]' 
  [ "$status" -eq 0 ]
  [ "$output" = '[{"repos":[],"labels":[]},{"repos":[],"labels":[]},{"repos":["a/one"],"labels":[]}]' ]
}

@test "filterForm hides drafts by default; drafts:true shows them; repo chip still ANDs" {
  _js '(() => { const prs = [{n:1,repoSlug:"a/one",isDraft:true},{n:2,repoSlug:"a/one"},{n:3,repoSlug:"b/two",isDraft:true},{n:4,repoSlug:"b/two"}]; const ids = (f) => m.filterForm(prs, f).map((p) => p.n); return [ids({}),ids({drafts:true}),ids({drafts:false}),ids({drafts:false,repos:["b/two"]})]; })()'
  [ "$status" -eq 0 ]
  [ "$output" = '[[2,4],[1,2,3,4],[2,4],[4]]' ]
}

@test "filterForm hides drafts when drafts is omitted" {
  _js '(() => { const prs = [{n:1,repoSlug:"a/one",isDraft:true},{n:2,repoSlug:"a/one"}]; return m.filterForm(prs).map((p) => p.n); })()'
  [ "$status" -eq 0 ]
  [ "$output" = '[2]' ]
}

@test "filterForm empty, repo, labels OR, repo AND label, stale ignored, unlabeled hidden" {
  _js '(() => { const prs = [{n:1,repoSlug:"a/one",labels:["bug"]},{n:2,repoSlug:"b/two",labels:["docs"]},{n:3,repoSlug:"a/one",labels:[]},{n:4,repoSlug:"A/One",labels:["BUG"]},{n:5,repoSlug:"c/three",labels:["help"]},{n:6,repoSlug:"c/three"}]; const ids = (f) => m.filterForm(prs, f).map((p) => p.n); return [ids({}),ids({repos:[],labels:[]}),ids({repos:["A/ONE"]}),ids({labels:["BUG","docs"]}),ids({repos:["a/one"],labels:["bug"]}),ids({repos:["gone/x"]}),ids({repos:["gone/x","b/two"]}),ids({labels:["nope"]}),ids({labels:["nope","help"]})]; })()'
  [ "$status" -eq 0 ]
  [ "$output" = '[[1,2,3,4,5,6],[1,2,3,4,5,6],[1,3,4],[1,2,4],[1,4],[1,2,3,4,5,6],[2],[1,2,3,4,5,6],[5]]' ]
}

