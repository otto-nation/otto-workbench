#!/usr/bin/env bats
# Tests for herdr-app queue home model (pure: gauges, summary, collapse).
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

@test "homeModel puts This computer first and opens live or failed-today machines" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const machines = [
      {on:"mini", at:NOW, result:{ok:true, rows:[{id:1, state:"done", finished_at: SEC - 10}]}},
      {on:null, at:NOW, result:{ok:true, rows:[{id:2, state:"running", entry_class:"heavy"}]}},
      {on:"studio", at:NOW, result:{ok:true, rows:[{id:3, state:"failed", finished_at: SEC - 30}]}},
    ];
    const model = h.homeModel(machines, NOW, {});
    process.stdout.write(JSON.stringify(model.map((b) => ({
      key: b.key, title: b.summary.title, open: b.open,
    }))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"key":"","title":"This computer","open":true},{"key":"mini","title":"mini","open":false},{"key":"studio","title":"studio","open":true}]' ]
}

@test "homeModel collapsed map overrides the default open state" {
  _js '
    const NOW = 1_700_000_000_000;
    const SEC = 1_700_000_000;
    const machines = [
      {on:null, at:NOW, result:{ok:true, rows:[{id:1, state:"running"}]}},
      {on:"mini", at:NOW, result:{ok:true, rows:[{id:2, state:"done", finished_at: SEC - 10}]}},
    ];
    const model = h.homeModel(machines, NOW, {"": true, mini: false});
    process.stdout.write(JSON.stringify(model.map((b) => ({key:b.key, open:b.open}))));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"key":"","open":false},{"key":"mini","open":true}]' ]
}

@test "toggleCollapsed is immutable and flips what the reader sees, defaults included" {
  _js '
    const orig = {mini: true};
    const once = h.toggleCollapsed(orig, "mini", false);
    const twice = h.toggleCollapsed(once, "mini", true);
    const closedByDefault = h.toggleCollapsed({}, "idle", false);
    const openByDefault = h.toggleCollapsed({}, "busy", true);
    process.stdout.write(JSON.stringify({
      orig, once, twice, closedByDefault, openByDefault, same: orig === once,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"orig":{"mini":true},"once":{"mini":false},"twice":{"mini":true},"closedByDefault":{"idle":false},"openByDefault":{"busy":true},"same":false}' ]
}

@test "a machine closed by default opens on its first toggle, and an open one closes" {
  _js '
    const idle = {key: "idle", at: 1, result: {ok: true, rows: []}};
    const first = h.homeModel([idle], 1000, {})[0];
    const after = h.homeModel([idle], 1000, h.toggleCollapsed({}, "idle", first.open))[0];
    process.stdout.write(JSON.stringify({before: first.open, after: after.open}));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"before":false,"after":true}' ]
}
