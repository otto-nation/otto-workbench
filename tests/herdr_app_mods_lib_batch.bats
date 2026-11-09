#!/usr/bin/env bats
# Tests for herdr-app batch busy-PR helper against pr batch status --full JSON.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js ai/lib/batch/model.py

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# $1 = status JSON, $2 = repo. Prints JSON array of busy PR numbers.
_busy() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch.js');
    process.stdout.write(JSON.stringify([...m.busyPrs(JSON.parse(process.argv[1]), process.argv[2])]));
  " -- "$1" "$2"
}

@test "each busy state is busy" {
  _busy '{"run":{"items":[{"key":"o/r#1","status":"queued"},{"key":"o/r#2","status":"waiting_admission"},{"key":"o/r#3","status":"running"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[1,2,3]' ]
}

@test "awaiting_decision is not busy" {
  _busy '{"run":{"items":[{"key":"o/r#4","status":"awaiting_decision"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "a finished state is not busy" {
  _busy '{"run":{"items":[{"key":"o/r#5","status":"done"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "repo match is exact, not a prefix sibling" {
  _busy '{"run":{"items":[{"key":"o/r#1","status":"queued"},{"key":"o/r-extra#2","status":"queued"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[1]' ]
}

@test "same name under a different owner is not this repo" {
  _busy '{"run":{"items":[{"key":"other/r#1","status":"queued"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "identity splits on the last hash" {
  _busy '{"run":{"items":[{"key":"o/foo#bar#9","status":"queued"}]}}' 'o/foo#bar'
  [ "$status" -eq 0 ]
  [ "$output" = '[9]' ]
}

@test "results are numbers not strings" {
  _busy '{"run":{"items":[{"key":"o/r#12","status":"running"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  jq -e '.[0] == 12 and (.[0] | type) == "number"' <<<"$output"
}

@test "null status yields empty" {
  _busy 'null' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "no-run status yields empty" {
  _busy '{"active":false}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "empty items yields empty" {
  _busy '{"run":{"items":[]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "malformed items are skipped" {
  _busy '{"run":{"items":[null,"x",{"status":"queued"},{"key":1,"status":"queued"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "duplicate number is busy if any item is busy" {
  _busy '{"run":{"items":[{"key":"o/r#7","status":"done"},{"key":"o/r#7","status":"queued"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[7]' ]
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
  _js 'm.itemTone("mystery")'
  [ "$output" = '"muted"' ]
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

@test "itemTone and stepTone table" {
  _js '({queued:m.itemTone("queued"),waiting_admission:m.itemTone("waiting_admission"),running:m.itemTone("running"),awaiting_decision:m.itemTone("awaiting_decision"),needs_session:m.itemTone("needs_session"),ready_to_publish:m.itemTone("ready_to_publish"),done:m.itemTone("done"),dropped:m.itemTone("dropped"),skipped_closed:m.itemTone("skipped_closed"),unknown:m.itemTone("nope")})'
  [ "$status" -eq 0 ]
  jq -e '.queued=="busy" and .waiting_admission=="busy" and .running=="busy" and .awaiting_decision=="attention" and .needs_session=="attention" and .ready_to_publish=="attention" and .done=="done" and .dropped=="muted" and .skipped_closed=="muted" and .unknown=="muted"' <<<"$output"
  _js '({running:m.stepTone("running"),failed:m.stepTone("failed"),needs_decision:m.stepTone("needs_decision"),interrupted:m.stepTone("interrupted"),done:m.stepTone("done"),pending:m.stepTone("pending"),skipped:m.stepTone("skipped"),unknown:m.stepTone("nope"),nil:m.stepTone(null)})'
  jq -e '.running=="busy" and .failed=="attention" and .needs_decision=="attention" and .interrupted=="attention" and .done=="done" and .pending=="muted" and .skipped=="muted" and .unknown=="muted" and .nil=="muted"' <<<"$output"
}

@test "attentionCount counts a finding-bearing done item" {
  _js 'm.attentionCount({ok:true,items:[{status:"done",findings:[{reason:"open"}]},{status:"awaiting_decision",findings:[]},{status:"running",findings:[]}]})'
  [ "$status" -eq 0 ]
  [ "$output" = '2' ]
  _js 'm.attentionCount({ok:false,error:{kind:"bad-json",message:"x"}})'
  [ "$output" = '0' ]
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
