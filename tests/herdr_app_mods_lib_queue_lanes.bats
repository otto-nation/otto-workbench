#!/usr/bin/env bats
# Tests for herdr-app queue lane model (pure: lanes, position, timing, staleness).
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

_js() {
  run node --input-type=module -e "
    import * as q from '$REPO_ROOT/ai/herdr/app/mods/_lib/queue_lanes.js';
    import { LOAD_DELAY_MS } from '$REPO_ROOT/ai/herdr/app/mods/_lib/load_state.js';
    import { GLYPH } from '$REPO_ROOT/ai/herdr/app/mods/_lib/status.js';
    $1
  "
}

@test "firstWaitingId matches the Python _first_waiting_id cases" {
  _js '
    const now = 1_000_000;
    const base = [
      {id:1, kind:"job", entry_class:"heavy", state:"queued"},
      {id:2, kind:"ticket", entry_class:"heavy", state:"queued"},
      {id:3, kind:"ticket", entry_class:"heavy", state:"queued"},
      {id:4, kind:"ticket", entry_class:"light", state:"queued"},
    ];
    const afterFirstRunning = base.map((r) => r.id === 2 ? {...r, state:"running"} : r);
    const kinds = [
      {id:10, kind:"job", entry_class:"heavy", state:"queued"},
      {id:11, kind:"ticket", entry_class:"heavy", state:"queued"},
    ];
    const retry = [
      {id:20, kind:"job", entry_class:"heavy", state:"queued", not_before: 2000},
      {id:21, kind:"ticket", entry_class:"heavy", state:"queued"},
    ];
    process.stdout.write(JSON.stringify({
      oldestTicket: q.firstWaitingId(base, "heavy", {jobs:false, nowMs: now}),
      afterRunning: q.firstWaitingId(afterFirstRunning, "heavy", {jobs:false, nowMs: now}),
      light: q.firstWaitingId(base, "light", {jobs:false, nowMs: now}),
      jobsFalse: q.firstWaitingId(kinds, "heavy", {jobs:false, nowMs: now}),
      jobsTrue: q.firstWaitingId(kinds, "heavy", {jobs:true, nowMs: now}),
      retryEarly: q.firstWaitingId(retry, "heavy", {jobs:true, nowMs: 1_000_000}),
      retryDue: q.firstWaitingId(retry, "heavy", {jobs:true, nowMs: 2_000_000}),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"oldestTicket":2,"afterRunning":3,"light":4,"jobsFalse":11,"jobsTrue":10,"retryEarly":21,"retryDue":20}' ]
}

@test "lanesModel partitions Running / Waiting / Finished; Waiting is class then id" {
  _js '
    const NOW = 1_700_000_000_000;
    const rows = [
      {id:3, state:"queued", kind:"ticket", entry_class:"light", action:"c"},
      {id:1, state:"running", kind:"ticket", entry_class:"heavy", action:"run"},
      {id:9, state:"done", kind:"job", entry_class:"heavy", action:"d"},
      {id:4, state:"queued", kind:"ticket", entry_class:"heavy", action:"b"},
      {id:2, state:"queued", kind:"ticket", entry_class:"heavy", action:"a"},
      {id:8, state:"failed", kind:"job", entry_class:"light", action:"e"},
      {id:7, state:"cancelled", kind:"ticket", entry_class:"heavy", action:"f"},
      {id:6, state:"tempfail", kind:"job", entry_class:"light", action:"g"},
      {id:5, state:"mystery", kind:"job", entry_class:"heavy", action:"h"},
    ];
    const lanes = q.lanesModel(rows, NOW);
    process.stdout.write(JSON.stringify(lanes.map((l) => ({
      key: l.key, label: l.label, count: l.count, ids: l.rows.map((r) => r.id),
    }))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"key":"running","label":"Running","count":1,"ids":[1]},{"key":"waiting","label":"Waiting","count":3,"ids":[2,4,3]},{"key":"finished","label":"Finished","count":4,"ids":[9,8,7,6]}]' ]
}

@test "queued rows get 1st in line / behind #id by class, and retry in from not_before" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const rows = [
      {id:820, state:"queued", kind:"ticket", entry_class:"heavy", action:"a"},
      {id:821, state:"queued", kind:"ticket", entry_class:"heavy", action:"b"},
      {id:830, state:"queued", kind:"ticket", entry_class:"light", action:"c"},
      {id:808, state:"queued", kind:"job", entry_class:"light", action:"d", not_before: SEC + 240},
      {id:1, state:"running", kind:"ticket", entry_class:"heavy", action:"e"},
    ];
    const waiting = q.lanesModel(rows, NOW).find((l) => l.key === "waiting").rows;
    process.stdout.write(JSON.stringify(waiting.map((r) => ({id:r.id, position:r.position, retry:r.retry}))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"id":820,"position":"1st in line","retry":""},{"id":821,"position":"behind #820","retry":""},{"id":808,"position":"","retry":"retry in 4m"},{"id":830,"position":"1st in line","retry":""}]' ]
}

@test "row timing: queued waited, running ran, finished ran plus ago" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const rows = [
      {id:1, state:"queued", kind:"ticket", entry_class:"heavy", created_at: SEC - 240},
      {id:2, state:"running", kind:"ticket", entry_class:"heavy", started_at: SEC - 720},
      {id:3, state:"done", kind:"job", entry_class:"heavy",
        started_at: SEC - 1080 - 41, finished_at: SEC - 1080},
    ];
    const lanes = q.lanesModel(rows, NOW);
    const pick = (key, id) => lanes.find((l) => l.key === key).rows.find((r) => r.id === id).timing;
    process.stdout.write(JSON.stringify([pick("waiting",1), pick("running",2), pick("finished",3)]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["waited 4m","ran 12m","ran 41s · 18m ago"]' ]
}

@test "failure text: exit, holder died, tempfail never got a slot, cancelled, exited 75" {
  _js '
    const NOW = 1_700_000_000_000;
    const rows = [
      {id:1, state:"failed", kind:"job", entry_class:"heavy", action:"a", exit_status: 1},
      {id:2, state:"failed", kind:"ticket", entry_class:"heavy", action:"b", exit_status: null},
      {id:3, state:"tempfail", kind:"job", entry_class:"light", action:"c"},
      {id:4, state:"cancelled", kind:"ticket", entry_class:"heavy", action:"d"},
      {id:5, state:"tempfail", kind:"job", entry_class:"light", action:"e", started_at: 1, exit_status: 75},
      {id:6, state:"done", kind:"job", entry_class:"heavy", action:"f", exit_status: 0},
    ];
    const finished = q.lanesModel(rows, NOW).find((l) => l.key === "finished").rows;
    process.stdout.write(JSON.stringify(finished.map((r) => ({id:r.id, failure:r.failure}))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"id":1,"failure":"exit 1"},{"id":2,"failure":"holder died"},{"id":3,"failure":"tempfail · never got a slot"},{"id":4,"failure":"cancelled"},{"id":5,"failure":"exited 75 (busy)"},{"id":6,"failure":""}]' ]
}

@test "repeated failed rows with the same subject+action collapse; else label; members kept" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const rows = [
      {id:11, state:"failed", kind:"job", entry_class:"heavy", subject:"acme/box#7", action:"review",
        finished_at: SEC - 10, exit_status: 1},
      {id:10, state:"failed", kind:"job", entry_class:"heavy", subject:"acme/box#7", action:"review",
        finished_at: SEC - 20, exit_status: 1},
      {id:9, state:"failed", kind:"job", entry_class:"heavy", subject:"acme/box#7", action:"review",
        finished_at: SEC - 30, exit_status: 2},
      {id:8, state:"failed", kind:"job", entry_class:"heavy", label:"lint sweep",
        finished_at: SEC - 40, exit_status: 1},
      {id:7, state:"failed", kind:"job", entry_class:"heavy", label:"lint sweep",
        finished_at: SEC - 50, exit_status: 1},
      {id:6, state:"failed", kind:"job", entry_class:"heavy", label:"once only",
        finished_at: SEC - 60, exit_status: 1},
      {id:5, state:"done", kind:"job", entry_class:"heavy", subject:"acme/box#7", action:"review",
        finished_at: SEC - 5},
    ];
    const finished = q.lanesModel(rows, NOW).find((l) => l.key === "finished").rows;
    process.stdout.write(JSON.stringify({
      glyph: GLYPH.fail,
      ids: finished.map((r) => r.id),
      failures: finished.map((r) => r.failure),
      members: finished.map((r) => (r.members ? r.members.map((m) => m.id) : null)),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"glyph":"×","ids":[5,11,8,6],"failures":["","failed 3× today","failed 2× today","exit 1"],"members":[null,[11,10,9],[8,7],null]}' ]
}

@test "filterRows all/live/failed and summary counts as text" {
  _js '
    const rows = [
      {id:1, state:"queued"}, {id:2, state:"running"}, {id:3, state:"failed"},
      {id:4, state:"done"}, {id:5, state:"cancelled"}, {id:6, state:"tempfail"},
      {id:7, state:"failed"},
    ];
    process.stdout.write(JSON.stringify({
      all: q.filterRows(rows, "all").map((r) => r.id),
      live: q.filterRows(rows, "live").map((r) => r.id),
      failed: q.filterRows(rows, "failed").map((r) => r.id),
      summary: q.summary(rows),
      empty: q.summary([]),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"all":[1,2,3,4,5,6,7],"live":[1,2],"failed":[3,7],"summary":"2 live · 2 failed","empty":"0 live · 0 failed"}' ]
}

@test "staleness maps result kinds and keeps last good rows" {
  _js '
    const NOW = 1_000_000;
    const rows = [{id:1, state:"running"}];
    const cases = [
      q.staleness({result:{ok:true, rows}, at: NOW - 4000}, NOW),
      q.staleness({result:{ok:true, rows}, at: NOW - 60_000}, NOW),
      q.staleness({result:{ok:false, error:{kind:"timeout"}, rows}, at: NOW - 360_000}, NOW),
      q.staleness({result:{ok:false, error:{kind:"missing-tool"}, rows}, at: NOW - 1000}, NOW),
      q.staleness({result:{ok:false, error:{kind:"outdated"}, rows}, at: NOW - 1000}, NOW),
      q.staleness({result:{ok:false, error:{kind:"exec"}, rows}, at: NOW - 360_000}, NOW),
    ];
    process.stdout.write(JSON.stringify(cases.map((s) => ({kind:s.kind, words:s.words, ids:s.rows.map((r) => r.id)}))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"fresh","words":"updated 4s ago","ids":[1]},{"kind":"stale","words":"updated 1m ago","ids":[1]},{"kind":"unreachable","words":"not reachable - last read 6m ago","ids":[1]},{"kind":"notInstalled","words":"otto-queue not installed - run otto-workbench sync","ids":[1]},{"kind":"outdated","words":"otto-queue outdated - run otto-workbench sync","ids":[1]},{"kind":"unreachable","words":"not reachable - last read 6m ago","ids":[1]}]' ]
}

@test "refreshSpinning is true only after LOAD_DELAY_MS" {
  _js '
    process.stdout.write(JSON.stringify({
      delay: LOAD_DELAY_MS,
      none: q.refreshSpinning(null, 5000),
      early: q.refreshSpinning(0, LOAD_DELAY_MS - 1),
      at: q.refreshSpinning(0, LOAD_DELAY_MS),
      late: q.refreshSpinning(100, 100 + LOAD_DELAY_MS),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"delay":1000,"none":false,"early":false,"at":true,"late":true}' ]
}

@test "lanesModel keeps Running then Waiting then Finished even when Running coalesces" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const rows = [
      {id:1, state:"done", action:"review", finished_at: SEC - 10},
      {id:2, state:"queued", action:"review", created_at: SEC - 10},
      {id:3, state:"running", action:"review", started_at: SEC - 10},
      {id:4, state:"running", action:"review", started_at: SEC - 20},
      {id:5, state:"running", action:"review", started_at: SEC - 30},
    ];
    const lanes = q.lanesModel(rows, NOW);
    process.stdout.write(JSON.stringify({
      keys: lanes.map((l) => l.key),
      runningCount: lanes[0].count,
      runningRows: lanes[0].rows.length,
      groupAction: lanes[0].rows[0].action,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"keys":["running","waiting","finished"],"runningCount":3,"runningRows":1,"groupAction":"review"}' ]
}

@test "coalesce groups COALESCE_MIN same-action rows and leaves fewer as individuals" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const two = [
      {id:1, state:"running", action:"review", started_at: SEC - 10},
      {id:2, state:"running", action:"review", started_at: SEC - 20},
    ];
    const three = [
      ...two,
      {id:3, state:"running", action:"review", started_at: SEC - 40},
    ];
    const under = q.lanesModel(two, NOW).find((l) => l.key === "running");
    const over = q.lanesModel(three, NOW).find((l) => l.key === "running");
    const g = over.rows[0];
    process.stdout.write(JSON.stringify({
      min: q.COALESCE_MIN,
      underIds: under.rows.map((r) => r.id),
      overKeys: over.rows.map((r) => r.key),
      count: g.count,
      action: g.action,
      memberIds: g.members.map((m) => m.id),
      leadId: g.lead.id,
      timing: g.timing,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"min":3,"underIds":[1,2],"overKeys":["running:review"],"count":3,"action":"review","memberIds":[1,2,3],"leadId":1,"timing":"ran 40s"}' ]
}

@test "repeat-failure grouping runs before action coalesce and does not double-group" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const sameSubject = [
      {id:11, state:"failed", subject:"acme/box#7", action:"review",
        finished_at: SEC - 10, exit_status: 1},
      {id:10, state:"failed", subject:"acme/box#7", action:"review",
        finished_at: SEC - 20, exit_status: 1},
      {id:9, state:"failed", subject:"acme/box#7", action:"review",
        finished_at: SEC - 30, exit_status: 2},
    ];
    const mixed = [
      ...sameSubject,
      {id:8, state:"failed", subject:"acme/box#8", action:"review",
        finished_at: SEC - 40, exit_status: 1},
      {id:7, state:"done", subject:"acme/box#9", action:"review",
        finished_at: SEC - 5},
    ];
    const only = q.lanesModel(sameSubject, NOW).find((l) => l.key === "finished");
    const after = q.lanesModel(mixed, NOW).find((l) => l.key === "finished");
    process.stdout.write(JSON.stringify({
      onlyIds: only.rows.map((r) => r.id),
      onlyFailure: only.rows[0].failure,
      onlyMembers: only.rows[0].members.map((m) => m.id),
      afterCount: after.count,
      afterKeys: after.rows.map((r) => r.key ?? r.id),
      afterFailure: after.rows[0].failure,
      afterMemberKinds: after.rows[0].members.map((m) => m.members ? "fail-group" : m.state),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"onlyIds":[11],"onlyFailure":"failed 3× today","onlyMembers":[11,10,9],"afterCount":3,"afterKeys":["finished:review"],"afterFailure":"2 failed · 1 done","afterMemberKinds":["done","fail-group","failed"]}' ]
}

@test "a finished action group keeps member order, subjects, and basename fallback" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const rows = [
      {id:4, state:"failed", argv:["/usr/bin/git","fetch"], subject:"acme/box#4",
        finished_at: SEC - 40},
      {id:3, state:"done", argv:["/usr/bin/git","status"], subject:"acme/box#3",
        finished_at: SEC - 10},
      {id:2, state:"done", argv:["/usr/bin/git","diff"], subject:"acme/box#2",
        finished_at: SEC - 20},
      {id:1, state:"cancelled", argv:["/usr/bin/git","log"], subject:"acme/box#1",
        finished_at: SEC - 30},
      {id:5, state:"done", argv:["/usr/bin/git","show"], subject:"acme/box#5",
        finished_at: SEC - 50},
    ];
    const g = q.lanesModel(rows, NOW).find((l) => l.key === "finished").rows[0];
    process.stdout.write(JSON.stringify({
      action: g.action,
      memberIds: g.members.map((m) => m.id),
      leadId: g.lead.id,
      subjects: g.subjects,
      timing: g.timing,
      failure: g.failure,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"action":"git","memberIds":[3,2,1,4,5],"leadId":3,"subjects":{"names":["acme/box#3","acme/box#2","acme/box#1"],"more":"+2 more"},"timing":"10s ago","failure":"1 failed · 3 done · 1 cancelled"}' ]
}

@test "subjectOf prefers ledger subject and otherwise parses owner/repo#N from the label" {
  _js '
    process.stdout.write(JSON.stringify([
      q.subjectOf({subject:"otto-nation/otto-workbench#2140", label:"pr review · other/repo#1"}),
      q.subjectOf({label:"pr review · otto-nation/otto-workbench#2140"}),
      q.subjectOf({label:"lint sweep"}),
      q.subjectOf({subject:"  "}),
      q.subjectOf(null),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["otto-nation/otto-workbench#2140","otto-nation/otto-workbench#2140",null,null,null]' ]
}
