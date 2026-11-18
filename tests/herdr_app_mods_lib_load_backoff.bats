#!/usr/bin/env bats
# Tests for the herdr board's load gate (pure: parse, factor, tick skipping, exec reader).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/load_backoff.js

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
    import * as g from '$REPO_ROOT/ai/herdr/app/mods/_lib/load_backoff.js';
    const out = (v) => process.stdout.write(JSON.stringify(v));
    const tick = () => new Promise((r) => setTimeout(r, 0));
    $1
  "
}

@test "parseLoadavg reads the 1-minute figure and refuses anything else" {
  _js 'out([g.parseLoadavg("3.52 2.10 1.00 2/400 1234\n"), g.parseLoadavg(""), g.parseLoadavg("x y"), g.parseLoadavg(null)])'
  [ "$status" -eq 0 ]
  [ "$output" = '[3.52,null,null,null]' ]
}

@test "factorFor is 1 below the busy mark, 2 at it, 4 at twice it, and 1 when unknown" {
  _js 'out([0.5, 1.49, 1.5, 2.9, 3, 12, NaN, null].map(g.factorFor))'
  [ "$status" -eq 0 ]
  [ "$output" = '[1,1,2,2,4,4,1,1]' ]
}

@test "a quiet machine runs every tick" {
  _js 'const gate = g.createLoadGate({ read: () => ({ load: 0.2, cores: 4 }) });
       const r = []; for (let i = 0; i < 4; i++) { r.push(gate.pass()); await tick(); } out(r)'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,true,true]' ]
}

@test "a busy machine runs every second tick, a very busy one every fourth" {
  _js 'const run = async (load) => {
         const gate = g.createLoadGate({ read: () => ({ load, cores: 4 }) });
         gate.pass(); await tick();
         const r = []; for (let i = 0; i < 8; i++) { r.push(gate.pass()); await tick(); } return r.filter(Boolean).length;
       };
       out([await run(7), await run(14)])'
  [ "$status" -eq 0 ]
  [ "$output" = '[4,2]' ]
}

@test "the gate resumes every tick once the load drops" {
  _js 'let load = 14; const gate = g.createLoadGate({ read: () => ({ load, cores: 4 }) });
       gate.pass(); await tick(); load = 0.1; gate.pass(); await tick();
       const r = []; for (let i = 0; i < 3; i++) { r.push(gate.pass()); await tick(); } out(r)'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,true]' ]
}

@test "a reader that throws or cannot say lets every tick run" {
  _js 'const bad = g.createLoadGate({ read: async () => { throw new Error("x"); } });
       const none = g.createLoadGate({ read: () => null });
       const r = []; for (let i = 0; i < 3; i++) { r.push(bad.pass(), none.pass()); await tick(); } out(r.every(Boolean))'
  [ "$status" -eq 0 ]
  [ "$output" = 'true' ]
}

@test "execLoadReader reads nproc once and /proc/loadavg each time; null off Linux" {
  _js 'const calls = [];
       const ok = async (cmd) => { calls.push(cmd); return cmd === "nproc" ? { code: 0, stdout: "4\n" } : { code: 0, stdout: "6.0 1 1 1/1 1" }; };
       const read = g.execLoadReader(ok);
       const a = await read(); const b = await read();
       const mac = g.execLoadReader(async () => ({ code: 1, stdout: "" }));
       out([a, b, calls, await mac()])'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"load":6,"cores":4},{"load":6,"cores":4},["nproc","cat","cat"],null]' ]
}

@test "a read still in flight is not started again" {
  _js 'let started = 0; let release;
       const gate = g.createLoadGate({ read: () => { started += 1; return new Promise((r) => { release = r; }); } });
       gate.pass(); gate.pass(); gate.pass(); await tick();
       const during = started;
       release({ load: 1, cores: 4 }); await tick();
       gate.pass(); await tick();
       out([during, started])'
  [ "$status" -eq 0 ]
  [ "$output" = '[1,2]' ]
}

@test "two gates keep separate skip counts" {
  _js 'const mk = () => g.createLoadGate({ read: () => ({ load: 7, cores: 4 }) });
       const a = mk(); const b = mk();
       a.pass(); b.pass(); await tick();
       const r = [a.pass(), b.pass(), b.pass(), a.pass()]; out(r)'
  [ "$status" -eq 0 ]
  [ "$output" = '[false,false,true,true]' ]
}

@test "execLoadReader passes on to nproc and cat, and omits it when the target is local" {
  _js 'const rec = [];
       const exec = async (cmd, args, opts = {}) => {
         rec.push({ cmd, hasOn: Object.prototype.hasOwnProperty.call(opts, "on"), on: opts.on ?? null });
         return cmd === "nproc" ? { code: 0, stdout: "4\\n" } : { code: 0, stdout: "1.0 1 1 1/1 1" };
       };
       await g.execLoadReader(exec, "mac")();
       await g.execLoadReader(exec, null)();
       out(rec)'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"cmd":"nproc","hasOn":true,"on":"mac"},{"cmd":"cat","hasOn":true,"on":"mac"},{"cmd":"nproc","hasOn":false,"on":null},{"cmd":"cat","hasOn":false,"on":null}]' ]
}

@test "createGateMap.filter asks each machine once; a busy host skips every target on it" {
  _js 'const exec = async (cmd, args, opts = {}) => {
         if (cmd === "nproc") return { code: 0, stdout: "4\\n" };
         const load = opts.on === "hot" ? "20.0" : "0.1";
         return { code: 0, stdout: load + " 1 1 1/1 1" };
       };
       const map = g.createGateMap(exec);
       const targets = [{ on: "hot", cwd: "/a" }, { on: "hot", cwd: "/b" }, { on: null, cwd: "/c" }];
       map.filter(targets); await tick();
       out([
         map.filter(targets).map((t) => t.cwd),
         map.filter(targets).map((t) => t.cwd),
         map.filter(targets).map((t) => t.cwd),
         map.filter(targets).map((t) => t.cwd),
       ])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["/c"],["/c"],["/c"],["/a","/b","/c"]]' ]
}

@test "createReaders shares one probe across two gate maps" {
  _js 'let nproc = 0;
       const exec = async (cmd) => {
         if (cmd === "nproc") { nproc += 1; return { code: 0, stdout: "4\\n" }; }
         return { code: 0, stdout: "20.0 1 1 1/1 1" };
       };
       const readers = g.createReaders(exec);
       const a = g.createGateMap(exec, readers);
       const b = g.createGateMap(exec, readers);
       a.filter([{ on: "hot" }]); b.filter([{ on: "hot" }]); await tick();
       out(nproc)'
  [ "$status" -eq 0 ]
  [ "$output" = '1' ]
}
