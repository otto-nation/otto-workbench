#!/usr/bin/env bats
# Tests for the herdr board's host health model (pure: severity, gauges, staleness, loadHealth).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/hosts.js ai/herdr/app/mods/_lib/hosts_load.js ai/herdr/app/mods/_lib/load_backoff.js ai/herdr/app/mods/_lib/queue_lanes.js

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
    import * as h from '$REPO_ROOT/ai/herdr/app/mods/_lib/hosts.js';
    import * as l from '$REPO_ROOT/ai/herdr/app/mods/_lib/hosts_load.js';
    import * as g from '$REPO_ROOT/ai/herdr/app/mods/_lib/load_backoff.js';
    const out = (v) => process.stdout.write(JSON.stringify(v));
    const health = (over = {}) => {
      const nest = (key, def) => {
        if (!Object.prototype.hasOwnProperty.call(over, key)) return def;
        if (over[key] == null || typeof over[key] !== 'object' || Array.isArray(over[key])) {
          return over[key];
        }
        return { ...def, ...over[key] };
      };
      const rest = { ...over };
      for (const k of ['load', 'mem', 'disk', 'procs', 'heavy', 'queue']) delete rest[k];
      return {
        cores: 4,
        load: nest('load', { m1: 1, m5: 1, m15: 1 }),
        mem: nest('mem', { total_mb: 8000, available_mb: 4000, swap_used_mb: 0 }),
        disk: nest('disk', { home: { size_mb: 100, used_mb: 10 }, docker: null }),
        procs: nest('procs', { count: 2, zombies: 0, top_cpu: [], top_mem: [] }),
        heavy: nest('heavy', { 'golangci-lint': 0, go: 0, gh: 0, tasks: 0 }),
        queue: nest('queue', { running: 0, queued: 0 }),
        ...rest,
      };
    };
    $1
  "
}

@test "severityOf: quiet host is pass/ok with no reasons" {
  _js 'out(h.severityOf(health()))'
  [ "$status" -eq 0 ]
  [ "$output" = '{"kind":"pass","word":"ok","reasons":[]}' ]
}

@test "severityOf load boundary pins BUSY_PER_CORE and STRAINED_PER_CORE" {
  _js 'out({
         double: g.STRAINED_PER_CORE === g.BUSY_PER_CORE * 2,
         busy: g.BUSY_PER_CORE,
         strained: g.STRAINED_PER_CORE,
         atBusy: h.severityOf(health({ load: { m1: 6.0 } })),
         justUnderBusy: h.severityOf(health({ load: { m1: 5.99 } })),
         atStrained: h.severityOf(health({ load: { m1: 12 } })),
         justUnderStrained: h.severityOf(health({ load: { m1: 11.99 } })),
       })'
  [ "$status" -eq 0 ]
  run jq -e '.double==true and .busy==1.5 and .strained==3
    and .atBusy=={"kind":"warn","word":"loaded","reasons":["load 1.5 per core"]}
    and .justUnderBusy.kind=="pass" and .justUnderBusy.word=="ok" and .justUnderBusy.reasons==[]
    and .atStrained=={"kind":"fail","word":"strained","reasons":["load 3.0 per core"]}
    and .justUnderStrained.kind=="warn" and .justUnderStrained.word=="loaded"' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "severityOf memory: strictly below 10% free is loaded" {
  _js 'out({
         at10: h.severityOf(health({ mem: { available_mb: 800 } })),
         justUnder: h.severityOf(health({ mem: { available_mb: 799 } })),
         strained: h.severityOf(health({ mem: { available_mb: 399 } })),
       })'
  [ "$status" -eq 0 ]
  run jq -e '.at10.kind=="pass" and .at10.word=="ok" and .at10.reasons==[]
    and .justUnder.kind=="warn" and .justUnder.word=="loaded"
    and .justUnder.reasons==["memory 10% free"]
    and .strained.kind=="fail" and .strained.word=="strained"' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "severityOf disk: 90% used is loaded, 95% strained, worst of home and docker" {
  _js 'out({
         loaded: h.severityOf(health({ disk: { home: { size_mb: 100, used_mb: 90 } } })),
         strained: h.severityOf(health({ disk: { home: { size_mb: 100, used_mb: 95 } } })),
         docker: h.severityOf(health({ disk: { docker: { size_mb: 100, used_mb: 96 } } })),
       })'
  [ "$status" -eq 0 ]
  run jq -e '.loaded.kind=="warn" and .loaded.word=="loaded"
    and .loaded.reasons==["disk 90% used"]
    and .strained.kind=="fail" and .strained.word=="strained"
    and .docker.kind=="fail" and .docker.word=="strained"
    and .docker.reasons==["disk 96% used"]' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "severityOf null sections never raise; worst-of lists both reasons" {
  _js 'out({
         nulls: h.severityOf(health({ mem: null, disk: null, load: null })),
         both: h.severityOf(health({ load: { m1: 6 }, disk: { home: { size_mb: 100, used_mb: 95 } } })),
       })'
  [ "$status" -eq 0 ]
  run jq -e '.nulls=={"kind":"pass","word":"ok","reasons":[]}
    and .both.kind=="fail" and .both.word=="strained"
    and .both.reasons==["load 1.5 per core","disk 95% used"]' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "hostStaleness: loading, fresh/stale at STALE_MS, unreachable, outdated" {
  _js 'const now = 1_000_000;
       out({
         none: h.hostStaleness({}, now),
         fresh: h.hostStaleness({ result: { ok: true }, at: now - 5000 }, now),
         atStale: h.hostStaleness({ result: { ok: true }, at: now - 60_000 }, now),
         justFresh: h.hostStaleness({ result: { ok: true }, at: now - 59_999 }, now),
         timeout: h.hostStaleness({ result: { ok: false, error: { kind: "timeout" } }, at: now - 90_000 }, now),
         missing: h.hostStaleness({ result: { ok: false, error: { kind: "missing-tool" } } }, now),
         outdated: h.hostStaleness({ result: { ok: false, error: { kind: "outdated" } } }, now),
       })'
  [ "$status" -eq 0 ]
  run jq -e '.none.kind=="loading" and .none.words=="Loading…"
    and .fresh.kind=="fresh" and .fresh.words=="updated 5s ago"
    and .atStale.kind=="stale"
    and .justFresh.kind=="fresh"
    and .timeout.kind=="unreachable" and .timeout.words=="not reachable - last read 1m ago"
    and .missing.kind=="outdated"
    and .missing.words=="host-health missing - run otto-workbench sync on this machine"
    and .outdated.kind=="outdated"
    and .outdated.words=="host-health missing - run otto-workbench sync on this machine"' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "hostSummary status mapping, kept-health gauges, and titles" {
  _js 'const now = 1_000_000;
       const kept = health();
       const loading = h.hostSummary({}, now);
       const unreach = h.hostSummary({
         result: { ok: false, error: { kind: "timeout" } },
         health: kept,
         at: now - 1000,
       }, now);
       const outdated = h.hostSummary({ result: { ok: false, error: { kind: "outdated" } } }, now);
       const healthy = h.hostSummary({ result: { ok: true, health: kept }, at: now }, now);
       out({
         loading: loading.status,
         unreach: unreach.status,
         unreachGauges: unreach.gauges.map((x) => x.key),
         outdated: outdated.status,
         healthy: healthy.status,
         titleLocal: h.hostSummary({ on: null }, now).title,
         titleRemote: h.hostSummary({ on: "homelab" }, now).title,
       })'
  [ "$status" -eq 0 ]
  run jq -e '.loading=={"kind":"running","word":"loading"}
    and .unreach=={"kind":"warn","word":"unreachable"}
    and .unreachGauges==["load","mem","disk"]
    and .outdated=={"kind":"warn","word":"outdated"}
    and .healthy=={"kind":"pass","word":"ok"}
    and .titleLocal=="This computer"
    and .titleRemote=="homelab"' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "the disk gauge shows the worst of home and docker, the figure severity judges" {
  _js 'const s = h.hostSummary({ result: { ok: true, health: health({
         disk: { home: { size_mb: 100, used_mb: 10 }, docker: { size_mb: 100, used_mb: 96 } },
       }) } }, 1);
       const disk = s.gauges.find((x) => x.key === "disk");
       out({ status: s.status.word, kind: disk.kind, meter: disk.meter })'
  [ "$status" -eq 0 ]
  run jq -e '.status=="strained" and .kind=="fail" and (.meter.text == "96% disk") and .meter.filled==96' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "hostSummary gauges, counts, queue busy, and swap note" {
  _js 'const rich = {
         load: { m1: 6 },
         mem: { available_mb: 4000 },
         disk: { home: { size_mb: 100, used_mb: 50 } },
         heavy: { go: 1, "golangci-lint": 2 },
         procs: { count: 2, zombies: 1 },
       };
       const s = h.hostSummary({ result: { ok: true, health: health({ ...rich, queue: { running: 1, queued: 3 } }) } }, 1);
       const busy = h.hostSummary({ result: { ok: true, health: health({ ...rich, queue: { busy: true } }) } }, 1);
       const noQ = h.hostSummary({ result: { ok: true, health: health({ ...rich, queue: null }) } }, 1);
       out({
         keys: s.gauges.map((x) => x.key),
         kinds: s.gauges.map((x) => x.kind),
         loadMeter: s.gauges[0].meter,
         counts: s.counts,
         busy: busy.counts,
         noQ: noQ.counts,
         note1024: h.hostSummary({ result: { ok: true, health: health({ mem: { swap_used_mb: 1024 } }) } }, 1).note,
         note1025: h.hostSummary({ result: { ok: true, health: health({ mem: { swap_used_mb: 1025 } }) } }, 1).note,
       })'
  [ "$status" -eq 0 ]
  run jq -e '.keys==["load","mem","disk"] and .kinds==["warn","pass","pass"]
    and .loadMeter=={kind:"bar",total:100,filled:50,text:"1.5/core"}
    and .counts=="2 processes, 1 zombies · golangci-lint 2, go 1 · queue 1 running, 3 waiting"
    and (.busy | endswith("queue busy"))
    and (.noQ | test("queue") | not)
    and .note1024==null
    and .note1025=="swap 1025 MB"' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "topProcesses formats cpu/mem rows and age" {
  _js 'const p = { pid: 10, user: "isaac", age_s: 3723, cpu: 12.4, mem: 3.2, command: "go" };
       out({
         cpu: h.topProcesses(health({ procs: { top_cpu: [p] } })),
         mem: h.topProcesses(health({ procs: { top_mem: [p] } }), "mem"),
         days: h.topProcesses(health({ procs: { top_cpu: [{ ...p, age_s: 172800 }] } })),
         none: h.topProcesses(health({ procs: null })),
       })'
  [ "$status" -eq 0 ]
  run jq -e '.cpu==[{"key":"10","command":"go","meta":"isaac · 1h","right":"12% cpu · 3% mem"}]
    and .mem==[{"key":"10","command":"go","meta":"isaac · 1h","right":"12% cpu · 3% mem"}]
    and .days[0].meta=="isaac · 2d"
    and .none==[]' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "topProcesses skips malformed rows and survives a first-load failure" {
  _js 'const p = { pid: 10, user: "u", age_s: 5, cpu: 1, mem: 2, command: "go" };
       out({
         rows: h.topProcesses(health({ procs: { top_cpu: [{ pid: 1, command: "x" }, { ...p, cpu: "9" }, { ...p, age_s: undefined }, p] } })),
         first: h.hostSummary({ result: { ok: false, error: "boom" }, at: undefined, health: undefined }, 1000),
       })'
  [ "$status" -eq 0 ]
  run jq -e '(.rows|length)==2 and .rows[0].meta=="u · ?" and .first.gauges==[]' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "hostsModel collapse defaults, order, keys, and last good health" {
  _js 'const quiet = health();
       const strainedH = health({ load: { m1: 12 } });
       const last = health({ mem: { available_mb: 3000 } });
       const local = { on: null, result: { ok: true, health: quiet } };
       const remote = { on: "homelab", result: { ok: true, health: strainedH } };
       const failed = { on: "box", result: { ok: false, error: { kind: "timeout" } }, health: last };
       const absent = h.hostsModel([local, remote], 1);
       const openLocal = h.hostsModel([local], 1, { "": false });
       const collapseRemote = h.hostsModel([remote], 1, { homelab: true });
       const kept = h.hostsModel([failed], 1);
       out({
         keys: absent.map((b) => b.key),
         open: absent.map((b) => b.open),
         localOpen: openLocal[0].open,
         remoteOpen: collapseRemote[0].open,
         failedHealth: kept[0].health,
         last,
       })'
  [ "$status" -eq 0 ]
  run jq -e '.keys==["","homelab"] and .open==[false,true]
    and .localOpen==true and .remoteOpen==false
    and .failedHealth==.last' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "loadHealth parses host-health --json and classifies failures" {
  _js 'const runWith = async (stdout, code = 0, stderr = "") => {
         const calls = [];
         const exec = async (cmd, args) => {
           calls.push({ cmd, args: [...args] });
           return { code, stdout, stderr };
         };
         const r = await l.loadHealth(exec, { cwd: "/w", on: null });
         return { r, calls };
       };
       const good = health();
       const a = await runWith(JSON.stringify(good));
       const miss = await runWith("", 127, "command not found");
       const bad = await runWith("not json");
       const foo = await runWith(JSON.stringify({ foo: 1 }));
       const arr = await runWith("[]");
       out({
         ok: a.r.ok === true,
         healthEq: JSON.stringify(a.r.health) === JSON.stringify(good),
         hostHealth: String(a.calls[0].cmd).includes("host-health"),
         args: a.calls[0].args,
         miss: miss.r.error.kind,
         bad: bad.r.error.kind,
         foo: foo.r.error.kind,
         arr: arr.r.error.kind,
       })'
  [ "$status" -eq 0 ]
  run jq -e '.ok==true and .healthEq==true and .hostHealth==true
    and .args==["--json"]
    and .miss=="missing-tool" and .bad=="bad-json"
    and .foo=="outdated" and .arr=="outdated"' <<< "$output"
  [ "$status" -eq 0 ]
}

@test "toggleCollapsed stores the visible state, returns a new map and keeps other keys" {
  _js 'const m = { a: true };
       const next = h.toggleCollapsed(m, "b", true);
       out({ next, same: next === m, m, none: h.toggleCollapsed(null, "", false) })'
  [ "$status" -eq 0 ]
  run jq -e '.next=={a:true,b:true} and .same==false and .m=={a:true} and .none=={"":false}' <<< "$output"
  [ "$status" -eq 0 ]
}
