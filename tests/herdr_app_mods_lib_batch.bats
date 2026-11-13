#!/usr/bin/env bats
# Tests for herdr-app batch status parsing, argv, and card state.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js ai/lib/batch/model.py

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "BUSY_STATES members are ItemStatus enum values" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch.js');
    process.stdout.write(JSON.stringify(m.BUSY_STATES));
  "
  [ "$status" -eq 0 ]
  enum_vals=$(sed -n '/^class ItemStatus(StrEnum):/,/^class /p' "$REPO_ROOT/ai/lib/batch/model.py" | sed -n 's/^.*= "\([^"]*\)"$/\1/p')
  [ -n "$enum_vals" ]
  busy_vals=$(jq -r '.[]' <<<"$output")
  [ -n "$busy_vals" ]
  while IFS= read -r s || [[ -n "$s" ]]; do
    echo "$enum_vals" | grep -Fxq "$s"
  done <<<"$busy_vals"
}

_js() {
  run node --input-type=module -e "const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch.js'); process.stdout.write(JSON.stringify($1))"
}

_parse() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch.js');
    process.stdout.write(JSON.stringify(m.$1(JSON.parse(process.argv[1]))));
  " -- "$2"
}

@test "listArgv statusArgv logTailArgv exact argv" {
  _js 'm.listArgv()'
  [ "$status" -eq 0 ]
  [ "$output" = '["--schema-version","2","batch","status","--list"]' ]
  _js 'm.statusArgv("run-1")'
  [ "$output" = '["--schema-version","2","batch","status","run-1","--full","--offline"]' ]
  _js 'm.statusArgv()'
  [ "$output" = '["--schema-version","2","batch","status","--full","--offline"]' ]
  _js 'm.logTailArgv("/tmp/x.log")'
  [ "$output" = '["tail","-n","200","/tmp/x.log"]' ]
  _js 'm.logTailArgv("/tmp/x.log", 50)'
  [ "$output" = '["tail","-n","50","/tmp/x.log"]' ]
  _js 'm.reportArgv("run-1")'
  [ "$output" = '["--schema-version","2","batch","report","--format","json","--offline","run-1"]' ]
}

@test "parseRunList ok plus error entry plus bad doc" {
  _parse parseRunList '{"schema_version":2,"runs":[{"id":"new","run_dir":"/r/new","started_at":"t2","status":"running","active":true,"items":3,"repos":["o/a"]},{"id":"bad","run_dir":"/r/bad","started_at":"t1","status":"interrupted","active":false,"error":"corrupt"},{"run_dir":"/r/noid","status":"done"},null]}'
  [ "$status" -eq 0 ]
  jq -e '.ok == true and (.runs | length) == 2 and .runs[0].id == "new" and .runs[0].runDir == "/r/new" and .runs[0].startedAt == "t2" and .runs[0].active == true and .runs[0].items == 3 and .runs[0].repos == ["o/a"] and .runs[0].error == null and .runs[1].id == "bad" and .runs[1].error == "corrupt" and .runs[1].active == false and .runs[1].items == 0' <<<"$output"
  _parse parseRunList 'null'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
  _parse parseRunList '[]'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
  _parse parseRunList '{"runs":null}'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
}

@test "parseRunDoc v1 decisions ignored and missing steps are null" {
  _parse parseRunDoc '{"schema_version":1,"active":false,"run_dir":"/r/1","run":{"id":"r1","started_at":"t","steps":["rebase","ci","comments","review"],"status":"waiting","trail_root":"/tr","decisions":[{"id":"d1"}],"items":[{"key":"o/r#1","repo":"o/r","pr":1,"branch":"feat","status":"awaiting_decision","worktree":"/wt","wait_reason":"conflict","head_moved":false,"steps":[{"step":"rebase","status":"failed","exit_code":1,"started_at":"a","ended_at":"b","log_path":"/l"}],"findings":[]}]}}'
  [ "$status" -eq 0 ]
  jq -e '.ok == true and .run.id == "r1" and .run.status == "waiting" and .run.active == false and .run.runDir == "/r/1" and .run.trailRoot == "/tr" and .run.startedAt == "t" and .run.schemaVersion == 1 and (.run | has("decisions") | not) and .items[0].key == "o/r#1" and .items[0].pr == 1 and .items[0].waitReason == "conflict" and .items[0].headMoved == false and .items[0].steps.rebase.status == "failed" and .items[0].steps.rebase.exitCode == 1 and .items[0].steps.rebase.logPath == "/l" and .items[0].steps.ci == null and .items[0].steps.comments == null and .items[0].steps.review == null and (.items[0].steps | keys) == ["ci","comments","rebase","review"]' <<<"$output"
}

@test "parseRunDoc unknown item status passes through muted" {
  _parse parseRunDoc '{"run":{"id":"r","items":[{"key":"o/r#1","status":"mystery"}]}}'
  [ "$status" -eq 0 ]
  jq -e '.ok == true and .items[0].status == "mystery"' <<<"$output"
}

@test "findingRec copies jobs from data and defaults to empty" {
  _parse parseRunDoc '{"run":{"id":"r","items":[{"key":"o/r#1","status":"needs_session","findings":[{"reason":"ci_failing","data":{"jobs":["a","b"]}},{"reason":"x"},{"reason":"y","data":{"jobs":"no"}}]}]}}'
  [ "$status" -eq 0 ]
  jq -e '.ok == true and .items[0].findings[0].jobs == ["a","b"] and .items[0].findings[1].jobs == [] and .items[0].findings[2].jobs == []' <<<"$output"
}

@test "parseRunDoc missing steps and findings become empty" {
  _parse parseRunDoc '{"schema_version":2,"run":{"id":"r"}}'
  [ "$status" -eq 0 ]
  jq -e '.ok == true and .items == [] and .run.steps == ["rebase","ci","comments","review"]' <<<"$output"
  _parse parseRunDoc '{"run":{"id":"r","items":[{"key":"o/r#1","status":"done"}]}}'
  jq -e '.ok == true and .items[0].findings == [] and .items[0].steps.rebase == null and .items[0].steps.ci == null and .items[0].steps.comments == null and .items[0].steps.review == null' <<<"$output"
}

@test "parseRunDoc bad doc is bad-json" {
  _parse parseRunDoc 'null'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
  _parse parseRunDoc '[]'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
  _parse parseRunDoc '{"active":false}'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
  _parse parseRunDoc '{"run":null}'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
}

@test "runStatus and stepStatus: kinds, labels, unknown is none with words" {
  _js '[["running","waiting","done","cancelled","interrupted","mystery_x",null].map((s) => m.runStatus(s).status),["running","waiting","done","cancelled","interrupted","mystery_x",null].map((s) => m.runStatus(s).text),["pending","running","done","skipped","needs_decision","failed","interrupted"].map((s) => m.stepStatus(s).status),["pending","running","done","skipped","needs_decision","failed","interrupted"].map((s) => m.stepStatus(s).text),Object.keys(m.STEP_LABEL),m.AI_STEPS]'
  [ "$status" -eq 0 ]
  [ "$output" = '[["running","running","pass","fail","fail","none","none"],["running","waiting","finished","cancelled","interrupted","mystery x",""],["none","running","pass","none","warn","fail","warn"],["pending","running","done","skipped","needs decision","failed","interrupted"],["rebase","ci","comments","review"],["rebase","ci","comments"]]' ]
}

@test "stateChanged true for state.json and runDir, false for report and log" {
  _js 'm.stateChanged(["/r/state.json"], "/r")'
  [ "$output" = 'true' ]
  _js 'm.stateChanged(["/r"], "/r")'
  [ "$output" = 'true' ]
  _js 'm.stateChanged(["/r/"], "/r/")'
  [ "$output" = 'true' ]
  _js 'm.stateChanged(["/r/state.json"], "/r/")'
  [ "$output" = 'true' ]
  _js 'm.stateChanged(["/r/report.json"], "/r")'
  [ "$output" = 'false' ]
  _js 'm.stateChanged(["/r/items/x.log"], "/r")'
  [ "$output" = 'false' ]
}

@test "laneOf table covering each status" {
  _js '({running:m.laneOf({status:"running"}),queued:m.laneOf({status:"queued"}),waiting_admission:m.laneOf({status:"waiting_admission"}),awaiting_decision:m.laneOf({status:"awaiting_decision"}),needs_session:m.laneOf({status:"needs_session"}),ready_to_publish:m.laneOf({status:"ready_to_publish"}),done:m.laneOf({status:"done",findings:[]}),doneFindings:m.laneOf({status:"done",findings:[{reason:"open"}]}),dropped:m.laneOf({status:"dropped"}),skipped_closed:m.laneOf({status:"skipped_closed"}),unknown:m.laneOf({status:"mystery"}),nil:m.laneOf({status:null})})'
  [ "$status" -eq 0 ]
  jq -e '.running=="running" and .queued=="waiting" and .waiting_admission=="waiting" and .awaiting_decision=="attention" and .needs_session=="attention" and .ready_to_publish=="attention" and .done=="done" and .doneFindings=="attention" and .dropped=="closed" and .skipped_closed=="closed" and .unknown=="closed" and .nil=="closed"' <<<"$output"
}

@test "cardState null, closed, failed, attention, running, queued, done" {
  _js 'm.cardState(null)'
  [ "$status" -eq 0 ]
  [ "$output" = 'null' ]
  _js 'm.cardState({status:"dropped"})'
  [ "$output" = 'null' ]
  _js 'm.cardState({status:"skipped_closed"})'
  [ "$output" = 'null' ]
  _js 'm.cardState({status:"running",steps:{rebase:{status:"failed"},ci:{status:"failed"}}})'
  jq -e '.text=="rebase failed" and .tone=="failed"' <<<"$output"
  _js 'm.cardState({status:"awaiting_decision",steps:{}})'
  jq -e '.text=="needs you" and .tone=="attention"' <<<"$output"
  _js 'm.cardState({status:"done",findings:[{reason:"open"}],steps:{}})'
  jq -e '.text=="needs you" and .tone=="attention"' <<<"$output"
  _js 'm.cardState({status:"running",steps:{rebase:{status:"done"},ci:{status:"running"}}})'
  jq -e '.text=="running ci" and .tone=="busy"' <<<"$output"
  _js 'm.cardState({status:"running",steps:{}})'
  jq -e '.text=="running" and .tone=="busy"' <<<"$output"
  _js 'm.cardState({status:"queued",steps:{}})'
  jq -e '.text=="queued" and .tone=="busy"' <<<"$output"
  _js 'm.cardState({status:"waiting_admission",steps:{}})'
  jq -e '.text=="queued" and .tone=="busy"' <<<"$output"
  _js 'm.cardState({status:"done",findings:[],steps:{}})'
  jq -e '.text=="done" and .tone=="done"' <<<"$output"
}

@test "shortKey single repo hashes, otherwise full or empty" {
  _js 'm.shortKey("o/r#1905", ["o/r"])'
  [ "$status" -eq 0 ]
  [ "$output" = '"#1905"' ]
  _js 'm.shortKey("o/r#1905", ["o/r","o/s"])'
  [ "$output" = '"o/r#1905"' ]
  _js 'm.shortKey("o/r#1905", ["o/other"])'
  [ "$output" = '"o/r#1905"' ]
  _js 'm.shortKey(null, ["o/r"])'
  [ "$output" = '""' ]
}

_sessions() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch.js');
    const r = m.parseSessions(JSON.parse(process.argv[1]));
    const out = r.ok ? {ok:true, sessions: Object.fromEntries(r.sessions)} : r;
    process.stdout.write(JSON.stringify(out));
  " -- "$1"
}

@test "parseSessions maps lowercased pr keys and command fields" {
  _sessions '{"sessions":[{"pr":"Owner/Name#1","worktree":"/wt","notes":["push first"],"commands":[{"argv":["pr","ci","--fix"],"why":"fix CI","action":"ci"},{"argv":["git","push"],"why":"publish","action":"push","push":true},{"argv":["pr","comments","--finish","--post"],"why":"close","action":"closeout","files_issue":true}]},{"pr":"owner/name#1","worktree":"/later","notes":[],"commands":[]},null,{"worktree":"/x"}]}'
  [ "$status" -eq 0 ]
  jq -e '.ok == true and (.sessions | keys) == ["owner/name#1"] and .sessions["owner/name#1"].worktree == "/later" and .sessions["owner/name#1"].notes == [] and .sessions["owner/name#1"].commands == []' <<<"$output"
  _sessions '{"sessions":[{"pr":"o/r#2","worktree":"/wt","notes":["n"],"commands":[{"argv":["pr","ci","--fix"],"why":"fix CI","action":"ci"},{"argv":["git","push"],"why":"publish","action":"push","push":true},{"argv":["pr","comments","--finish","--post"],"why":"close","action":"closeout","files_issue":true}]}]}'
  jq -e '.ok == true and .sessions["o/r#2"].worktree == "/wt" and .sessions["o/r#2"].notes == ["n"] and .sessions["o/r#2"].commands[0] == {"argv":["pr","ci","--fix"],"why":"fix CI","action":"ci","push":false,"filesIssue":false} and .sessions["o/r#2"].commands[1].push == true and .sessions["o/r#2"].commands[1].filesIssue == false and .sessions["o/r#2"].commands[2].push == false and .sessions["o/r#2"].commands[2].filesIssue == true' <<<"$output"
}

@test "parseSessions bad doc is bad-json" {
  _sessions 'null'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
  _sessions '[]'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
  _sessions '{"sessions":null}'
  jq -e '.ok == false and .error.kind == "bad-json"' <<<"$output"
}

@test "actionLabel maps ids, reuses STEP_LABEL, falls back to Run" {
  _js '[m.actionLabel({action:"ci"}),m.actionLabel({action:"comments"}),m.actionLabel({action:"rebase"}),m.actionLabel({action:"rebase_generated"}),m.actionLabel({action:"review"}),m.actionLabel({action:"closeout"}),m.actionLabel({action:"push"}),m.actionLabel({action:"switch_branch"}),m.actionLabel({action:"undo_rebase"}),m.actionLabel({action:"file_threads_issue"}),m.actionLabel({action:"file_review_issue"}),m.actionLabel({action:"mystery"}),m.actionLabel({}),m.ACTION_LABEL.ci===m.STEP_LABEL.ci,m.ACTION_LABEL.comments===m.STEP_LABEL.comments,Object.isFrozen(m.ACTION_LABEL)]'
  [ "$status" -eq 0 ]
  [ "$output" = '["Fix CI","Answer comments","Resolve conflicts","Rebuild generated files","Fix review findings","Close out comments","Push","Switch branch","Undo rebase","File issue for threads","File issues for findings","Run","Run",true,true,true]' ]
}

@test "writesGitHub is true for push or --post" {
  _js '[m.writesGitHub({push:true,argv:["git","push"]}),m.writesGitHub({push:false,argv:["pr","comments","--finish","--post"]}),m.writesGitHub({argv:["pr","ci","--fix"]}),m.writesGitHub({push:false,argv:["pr","review"]})]'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,false,false]' ]
}

@test "durationText formats elapsed and empty cases" {
  _js 'm.durationText("1970-01-01T00:00:00.000Z", "1970-01-01T00:01:18.000Z", 0)'
  [ "$status" -eq 0 ]
  [ "$output" = '"1m 18s"' ]
  _js 'm.durationText("1970-01-01T00:00:00.000Z", "1970-01-01T00:00:05.000Z", 0)'
  [ "$output" = '"5s"' ]
  _js 'm.durationText("1970-01-01T00:00:00.000Z", "1970-01-01T01:02:00.000Z", 0)'
  [ "$output" = '"1h 2m"' ]
  _js 'm.durationText("1970-01-01T00:00:00.000Z", "", 5000)'
  [ "$output" = '"5s"' ]
  _js 'm.durationText("", "1970-01-01T00:00:05.000Z", 0)'
  [ "$output" = '""' ]
  _js 'm.durationText("1970-01-01T00:00:00.000Z", "not-a-date", 0)'
  [ "$output" = '""' ]
}

_trail() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch.js');
    const { REMOTE_PREAMBLE } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js');
    process.stdout.write(JSON.stringify($1).replaceAll(JSON.stringify(REMOTE_PREAMBLE), '\"<PREAMBLE>\"'));
  "
}

@test "trailArgv id to argv, null empty whitespace to null, machine via launch convention" {
  _trail 'm.trailArgv({trailRoot:"03e519c6e72a",cwd:"/w",repo:"o/n"})'
  [ "$status" -eq 0 ]
  [ "$output" = '["run","--bg","--json","--cwd","/w","--label","otto.repo=o/n","--label","otto.action=batch","--label","otto.app=pr","--","otto-log","show","03e519c6e72a"]' ]
  _trail 'm.trailArgv({trailRoot:"03e519c6e72a",cwd:"/w",on:"homelab",repo:"o/n"})'
  [ "$output" = '["run","--bg","--json","--cwd","/w","--on","homelab","--label","otto.repo=o/n","--label","otto.action=batch","--label","otto.app=pr","--","sh","-c","<PREAMBLE>","sh","otto-log","show","03e519c6e72a"]' ]
  _trail 'm.trailArgv({trailRoot:null,cwd:"/w",on:"homelab",repo:"o/n"})'
  [ "$output" = 'null' ]
  _trail 'm.trailArgv({trailRoot:"",cwd:"/w",on:"homelab",repo:"o/n"})'
  [ "$output" = 'null' ]
  _trail 'm.trailArgv({trailRoot:"   ",cwd:"/w",on:"homelab",repo:"o/n"})'
  [ "$output" = 'null' ]
}
