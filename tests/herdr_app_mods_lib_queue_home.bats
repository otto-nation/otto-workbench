#!/usr/bin/env bats
# Tests for herdr-app queue home model (pure: gauges, overview, machine pages).
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
    import * as h from '$REPO_ROOT/ai/herdr/app/mods/_lib/queue_home.js';
    import { staleness } from '$REPO_ROOT/ai/herdr/app/mods/_lib/queue_lanes.js';
    $1
  "
}

@test "slotGauge draws one glyph per slot, caps at 8, and falls back without a limit" {
  _js '
    process.stdout.write(JSON.stringify([
      h.slotGauge(2, 3),
      h.slotGauge(0, 4),
      h.slotGauge(3, 3),
      h.slotGauge(5, 3),
      h.slotGauge(2, 8),
      h.slotGauge(2, 9),
      h.slotGauge(2, null),
      h.slotGauge(0, null),
      h.slotGauge(2, 0),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["██░ 2/3","░░░░ 0/4","███ 3/3","███ 5/3","██░░░░░░ 2/8","2/9","2 running","0 running","2/0"]' ]
}

@test "machineSummary counts live rows and failed-today, reuses staleness, hostNote is null" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const rows = [
      {id:1, state:"running", entry_class:"heavy"},
      {id:2, state:"running", entry_class:"heavy"},
      {id:3, state:"queued", entry_class:"light"},
      {id:4, state:"queued", entry_class:"light"},
      {id:5, state:"queued", entry_class:"heavy"},
      {id:6, state:"failed", finished_at: SEC - 60},
      {id:7, state:"failed", finished_at: SEC - (24 * 3600) - 1},
      {id:8, state:"done", finished_at: SEC - 10},
    ];
    const entry = {
      on: null,
      at: NOW - 4000,
      result: {
        ok: true,
        rows,
        slots: {heavy:{running:2, limit:3}, light:{running:0, limit:4}},
      },
    };
    const s = h.machineSummary(entry, NOW);
    const stale = staleness(entry, NOW);
    process.stdout.write(JSON.stringify({
      title: s.title,
      gauges: s.gauges,
      counts: s.counts,
      hostNote: s.hostNote,
      staleKind: s.staleness.kind,
      staleWords: s.staleness.words,
      sameWords: s.staleness.words === stale.words,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"title":"This computer","gauges":[{"class":"heavy","text":"██░ 2/3"},{"class":"light","text":"░░░░ 0/4"}],"counts":"2 running · 3 queued · 1 failed today","hostNote":null,"staleKind":"fresh","staleWords":"updated 4s ago","sameWords":true}' ]
}

@test "machineSummary without slots uses running counts and n running text" {
  _js '
    const NOW = 1_700_000_000_000;
    const s = h.machineSummary({
      on: "mini",
      at: NOW,
      result: {ok:true, rows:[
        {id:1, state:"running", entry_class:"heavy"},
        {id:2, state:"queued", entry_class:"light"},
      ], slots:null},
    }, NOW);
    process.stdout.write(JSON.stringify({title:s.title, gauges:s.gauges, counts:s.counts}));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"title":"mini","gauges":[{"class":"heavy","text":"1 running"},{"class":"light","text":"0 running"}],"counts":"1 running · 1 queued · 0 failed today"}' ]
}

@test "homeModel puts This computer first; cards have no rows and no open state" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const machines = [
      {on:"mini", at:NOW, result:{ok:true, rows:[{id:1, state:"done", finished_at: SEC - 10}]}},
      {on:null, at:NOW, result:{ok:true, rows:[{id:2, state:"running", entry_class:"heavy"}]}},
      {on:"studio", at:NOW, result:{ok:true, rows:[{id:3, state:"failed", finished_at: SEC - 30}]}},
    ];
    const model = h.homeModel(machines, NOW);
    const local = model[0];
    process.stdout.write(JSON.stringify({
      keys: model.map((b) => b.key),
      titles: model.map((b) => b.title),
      counts: model.map((b) => b.counts),
      hostNote: local.hostNote,
      staleKind: local.staleness.kind,
      staleWords: local.staleness.words,
      staleRows: local.staleness.rows ?? null,
      open: local.open ?? null,
      rows: local.rows ?? null,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"keys":["","mini","studio"],"titles":["This computer","mini","studio"],"counts":["1 running · 0 queued · 0 failed today","0 running · 0 queued · 0 failed today","0 running · 0 queued · 1 failed today"],"hostNote":null,"staleKind":"fresh","staleWords":"updated 0s ago","staleRows":null,"open":null,"rows":null}' ]
}

@test "machinePage returns the card, filter summary from uncoalesced rows, Running first" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const entry = {
      on: "mini", key: "mini", at: NOW,
      result: {ok:true, rows:[
        {id:1, state:"running", action:"review", entry_class:"heavy", started_at: SEC - 10},
        {id:2, state:"running", action:"review", entry_class:"heavy", started_at: SEC - 20},
        {id:3, state:"running", action:"review", entry_class:"heavy", started_at: SEC - 30},
        {id:4, state:"queued", action:"comments", entry_class:"light", created_at: SEC - 5},
        {id:5, state:"failed", action:"review", finished_at: SEC - 40, exit_status: 1},
      ]},
    };
    const page = h.machinePage(entry, NOW, "all");
    const live = h.machinePage(entry, NOW, "live");
    const bad = h.machinePage(entry, NOW, "nope");
    process.stdout.write(JSON.stringify({
      title: page.summary.title,
      counts: page.summary.counts,
      hostNote: page.summary.hostNote,
      filter: page.filter,
      filterSummary: page.filterSummary,
      keys: page.lanes.map((l) => l.key),
      runningCount: page.lanes[0].count,
      runningRows: page.lanes[0].rows.length,
      liveFilter: live.filter,
      liveKeys: live.lanes.map((l) => l.key),
      liveFinished: live.lanes[2].count,
      badFilter: bad.filter,
      staleRows: page.summary.staleness.rows ?? null,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"title":"mini","counts":"3 running · 1 queued · 1 failed today","hostNote":null,"filter":"all","filterSummary":"4 live · 1 failed","keys":["running","waiting","finished"],"runningCount":3,"runningRows":1,"liveFilter":"live","liveKeys":["running","waiting","finished"],"liveFinished":0,"badFilter":"all","staleRows":null}' ]
}

@test "selectMachine and clearMachine are immutable; pageOf falls back when the machine is gone" {
  _js '
    const orig = {machine: null};
    const selected = h.selectMachine(orig, "mini");
    const cleared = h.clearMachine(selected);
    const machines = [
      {on:null, key:""},
      {on:"mini", key:"mini"},
    ];
    process.stdout.write(JSON.stringify({
      orig,
      selected,
      cleared,
      same: orig === selected,
      hit: h.pageOf(selected, machines)?.key ?? null,
      miss: h.pageOf({machine: "gone"}, machines),
      home: h.pageOf({machine: null}, machines),
      local: h.pageOf({machine: ""}, machines)?.key ?? null,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"orig":{"machine":null},"selected":{"machine":"mini"},"cleared":{"machine":null},"same":false,"hit":"mini","miss":null,"home":null,"local":""}' ]
}
