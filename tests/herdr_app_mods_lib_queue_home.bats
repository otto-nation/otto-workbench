#!/usr/bin/env bats
# Tests for herdr-app queue home model (pure: meters, overview, machine pages).
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
    import { RECENT_TERMINAL, staleness } from '$REPO_ROOT/ai/herdr/app/mods/_lib/queue_lanes.js';
    $1
  "
}

@test "meterModel is segments at 8, a bar above 8, and text-only without a limit" {
  _js '
    process.stdout.write(JSON.stringify([
      h.meterModel(2, 3),
      h.meterModel(0, 4),
      h.meterModel(3, 3),
      h.meterModel(5, 3),
      h.meterModel(2, 8),
      h.meterModel(2, 9),
      h.meterModel(2, null),
      h.meterModel(0, null),
      h.meterModel(2, 0),
      h.METER_SEGMENT_MAX,
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"segments","filled":2,"total":3,"text":"2/3"},{"kind":"segments","filled":0,"total":4,"text":"0/4"},{"kind":"segments","filled":3,"total":3,"text":"3/3"},{"kind":"segments","filled":3,"total":3,"text":"5/3"},{"kind":"segments","filled":2,"total":8,"text":"2/8"},{"kind":"bar","filled":2,"total":9,"text":"2/9"},{"kind":"none","filled":2,"total":null,"text":"2 running"},{"kind":"none","filled":0,"total":null,"text":"0 running"},{"kind":"segments","filled":0,"total":0,"text":"2/0"},8]' ]
}

@test "machineSummary counts live rows and failed-24h, reuses staleness, hostNote is null" {
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
      failed: s.failed,
      truncated: s.truncated,
      hostNote: s.hostNote,
      staleKind: s.staleness.kind,
      staleWords: s.staleness.words,
      sameWords: s.staleness.words === stale.words,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"title":"This computer","gauges":[{"class":"heavy","kind":"segments","filled":2,"total":3,"text":"2/3"},{"class":"light","kind":"segments","filled":0,"total":4,"text":"0/4"}],"counts":"2 running · 3 queued · 1 failed 24h","failed":1,"truncated":false,"hostNote":null,"staleKind":"fresh","staleWords":"updated 4s ago","sameWords":true}' ]
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
    process.stdout.write(JSON.stringify({title:s.title, gauges:s.gauges, counts:s.counts, truncated:s.truncated}));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"title":"mini","gauges":[{"class":"heavy","kind":"none","filled":1,"total":null,"text":"1 running"},{"class":"light","kind":"none","filled":0,"total":null,"text":"0 running"}],"counts":"1 running · 1 queued · 0 failed 24h","truncated":false}' ]
}

@test "machineSummary sets truncated when the status list is at RECENT_TERMINAL" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const under = Array.from({length: RECENT_TERMINAL - 1}, (_, i) => ({
      id: i + 1, state: "done", finished_at: SEC - i,
    }));
    const at = [...under, {id: RECENT_TERMINAL, state: "failed", finished_at: SEC - 5}];
    const sUnder = h.machineSummary({on:"mini", at:NOW, result:{ok:true, rows:under}}, NOW);
    const sAt = h.machineSummary({on:"mini", at:NOW, result:{ok:true, rows:at}}, NOW);
    process.stdout.write(JSON.stringify({
      cap: RECENT_TERMINAL,
      under: sUnder.truncated,
      atCap: sAt.truncated,
      failed: sAt.failed,
      counts: sAt.counts,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"cap":20,"under":false,"atCap":true,"failed":1,"counts":"0 running · 0 queued · 1 failed 24h"}' ]
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
  [ "$output" = '{"keys":["","mini","studio"],"titles":["This computer","mini","studio"],"counts":["1 running · 0 queued · 0 failed 24h","0 running · 0 queued · 0 failed 24h","0 running · 0 queued · 1 failed 24h"],"hostNote":null,"staleKind":"fresh","staleWords":"updated 0s ago","staleRows":null,"open":null,"rows":null}' ]
}

@test "machinePage returns the card from uncoalesced rows, Running first, no filter" {
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
    const page = h.machinePage(entry, NOW);
    process.stdout.write(JSON.stringify({
      title: page.summary.title,
      counts: page.summary.counts,
      hostNote: page.summary.hostNote,
      filter: page.filter ?? null,
      filterSummary: page.filterSummary ?? null,
      keys: page.lanes.map((l) => l.key),
      cards: page.lanes.map((l) => l.card),
      runningCount: page.lanes[0].count,
      runningRows: page.lanes[0].rows.length,
      staleRows: page.summary.staleness.rows ?? null,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"title":"mini","counts":"3 running · 1 queued · 1 failed 24h","hostNote":null,"filter":null,"filterSummary":null,"keys":["running","waiting","finished"],"cards":[true,true,true],"runningCount":3,"runningRows":3,"staleRows":null}' ]
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
