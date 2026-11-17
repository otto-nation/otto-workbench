#!/usr/bin/env bats
# Tests for opening, renaming and closing a pi session from the Sessions panel (pure over fake herdr deps).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/sessions_open.js ai/herdr/app/mods/_lib/run_session.js ai/herdr/app/mods/_lib/launch.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# _js BODY — runs BODY with o (sessions_open.js), out(v), row(over) and fake(opts) in scope.
# fake() returns {calls, d}: d is the deps object; calls.exec / calls.run record what ran.
_js() {
  run node --input-type=module -e "
    import * as o from '$REPO_ROOT/ai/herdr/app/mods/_lib/sessions_open.js';
    const out = (v) => process.stdout.write(JSON.stringify(v));
    const row = (over = {}) => Object.assign({ id: 's1', file: '/s/x_s1.jsonl', cwd: '/w/a', on: null, live: false, pid: null }, over);
    const fake = ({ ls = { code: 0, stdout: '[]', stderr: '' }, close = { code: 0, stdout: '', stderr: '' } } = {}) => {
      const calls = { exec: [], run: [] };
      const runReply = { code: 0, stdout: JSON.stringify({ address: 'T1' }), stderr: '' };
      const d = {
        exec: async (cmd, args) => {
          calls.exec.push([cmd].concat(args));
          if (cmd === 'hc' && args[0] === 'ls') return ls;
          if (cmd === 'hc') return runReply;
          return close;
        },
        run: (name, args) => { calls.run.push([name, args]); },
        panes: () => [],
        spaces: () => [],
        terminals: () => [{ address: 'T1' }],
        sleep: async () => {},
      };
      return { calls, d };
    };
    $1
  "
}

@test "openPiSession: a live terminal labelled with the session is brought forward" {
  _js '
    const f = fake({ ls: { code: 0, stdout: JSON.stringify([{ address: "T9", state: "live" }]), stderr: "" } });
    const res = await o.openPiSession(f.d, row({ live: true, pid: 7 }));
    out([res, f.calls.exec, f.calls.run]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":true,"opened":"focused"},[["hc","ls","--json","--label","otto.session=s1"]],[["terminal.keep",{"terminal":"T9"}]]]' ]
}

@test "openPiSession: a closed session starts pi --session in its cwd with the session label" {
  _js '
    const f = fake();
    const res = await o.openPiSession(f.d, row());
    const started = f.calls.exec[1];
    out([res, started.slice(0, 6), started.includes("otto.session=s1"), started.slice(-3), f.calls.run]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":true,"opened":"started"},["hc","run","--bg","--json","--cwd","/w/a"],true,["pi","--session","/s/x_s1.jsonl"],[["terminal.keep",{"terminal":"T1"}]]]' ]
}

@test "openPiSession: rename of a closed session opens it with --name" {
  _js '
    const f = fake();
    const res = await o.openPiSession(f.d, row(), { name: "New name" });
    out([res.ok, f.calls.exec[1].slice(-5)]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,["pi","--session","/s/x_s1.jsonl","--name","New name"]]' ]
}

@test "openPiSession: a live session with no herdr terminal is never started twice" {
  _js '
    const f = fake();
    const res = await o.openPiSession(f.d, row({ live: true, pid: 4242 }));
    out([res, f.calls.exec.length]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":false,"message":"running outside herdr (pid 4242) — open it where it runs"},1]' ]
}

@test "openPiSession: renaming a live session points at /name and runs nothing" {
  _js '
    const f = fake({ ls: { code: 0, stdout: JSON.stringify([{ address: "T9", state: "live" }]), stderr: "" } });
    const res = await o.openPiSession(f.d, row({ live: true, pid: 7 }), { name: "x" });
    out([res, f.calls.run.length, f.calls.exec.length]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":false,"message":"Rename it in the session with /name"},0,1]' ]
}

@test "openPiSession: a failed terminal lookup refuses rather than starting a second pi" {
  _js '
    const f = fake({ ls: { code: 1, stdout: "", stderr: "no socket" } });
    const res = await o.openPiSession(f.d, row());
    out([res, f.calls.exec.length]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":false,"message":"could not look for an open session: no socket"},1]' ]
}

@test "openPiSession: a second open of the same file while one is pending starts nothing" {
  _js '
    const f = fake();
    const [a, b] = await Promise.all([o.openPiSession(f.d, row()), o.openPiSession(f.d, row())]);
    out([a.ok, b.ok, f.calls.exec.filter((c) => c[1] === "run").length]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false,1]' ]
}

@test "closePiSession: asks pi-sessions close on the session's machine and reports a refusal" {
  _js '
    const ok = fake();
    const a = await o.closePiSession(ok.d.exec, row());
    const no = fake({ close: { code: 1, stdout: "", stderr: "pi-sessions: s1 is not running here" } });
    const b = await o.closePiSession(no.d.exec, row());
    out([a, ok.calls.exec[0], b.ok, b.message]);
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":true},["pi-sessions","close","s1"],false,"pi-sessions: s1 is not running here"]' ]
}
