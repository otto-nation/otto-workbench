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

@test "planSummary one group" {
  _js 'm.planSummary([{repo:"otto-nation/otto-workbench",keys:["a#1"]}], ["rebase"])'
  [ "$status" -eq 0 ]
  [ "$output" = '"Starts 1 run: otto-workbench (1 PR) — rebase"' ]
}

@test "planSummary two groups" {
  _js 'm.planSummary([{repo:"otto-nation/otto-workbench",keys:["a#1","a#2","a#3"]},{repo:"otto-nation/homelab",keys:["b#1"]}], ["rebase","ci"])'
  [ "$status" -eq 0 ]
  [ "$output" = '"Starts 2 runs: otto-workbench (3 PRs) and homelab (1 PR) — rebase, ci"' ]
}

@test "planSummary three groups" {
  _js 'm.planSummary([{repo:"a/one",keys:["a#1"]},{repo:"a/two",keys:["b#1"]},{repo:"a/three",keys:["c#1"]}], ["rebase"])'
  [ "$status" -eq 0 ]
  [ "$output" = '"Starts 3 runs: one (1 PR), two (1 PR) and three (1 PR) — rebase"' ]
}

@test "planSummary empty groups or steps is empty" {
  _js '[m.planSummary([], ["rebase"]),m.planSummary([{repo:"a/one",keys:["a#1"]}], []),m.planSummary(null, ["rebase"])]'
  [ "$status" -eq 0 ]
  [ "$output" = '["","",""]' ]
}

@test "batchButtonText none, running, and zero total" {
  _js '[m.batchButtonText([]),m.batchButtonText([{progress:{done:1,total:3}},{progress:{done:2,total:2}}]),m.batchButtonText([{progress:{done:0,total:0}}]),m.batchButtonText(null)]'
  [ "$status" -eq 0 ]
  [ "$output" = '["Batch","Batch · running 3 of 5","Batch · running","Batch"]' ]
}
