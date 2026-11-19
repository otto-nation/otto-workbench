#!/usr/bin/env bats
# The PRs mod's target Open session (`pr target session`) and Batch-tab trail helpers.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/prs/*.js ai/herdr/app/mods/_lib/*.js ai/herdr/app/mods/_ui/*.js tests/herdr_app_host_helper.bash

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_app_host_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  _host_setup
}
teardown() { common_teardown; }

# One finished run R1 over o/n#7 (needs you) on machine `on`; its report has two sessions.
# seen: [{line, on}] with a remote preamble unwrapped; ran: herdr run() calls. Like herdr, a
# terminal `hc run` started is listed by terminals() only after the next sleep (H.shows = false: never).
RUNS="$(cat <<'RUNS_JS'
const { Workflows } = await import(M + "/workflows.js");
const seen = []; const ran = [];
const LIST = { runs: [{ id: "R1", run_dir: "/s/R1", started_at: "2026-11-14T10:00:00Z", status: "done", active: false, items: 1, repos: ["o/n"] }] };
const STATUS = { schema_version: 2, active: false, run_dir: "/s/R1", run: { id: "R1", status: "done", steps: ["ci"],
  items: [{ key: "o/n#7", repo: "o/n", pr: 7, status: "awaiting_decision", findings: [{ reason: "ci_failing", step: "ci" }] }] } };
const REPORT = { sessions: [{ pr: "o/n#7", commands: [] }, { pr: "o/n#8", commands: [] }] };
// First match wins; the nesting validator reads this heredoc as shell, so no `if` lines here.
const json = (v) => ({ code: 0, stdout: JSON.stringify(v), stderr: "" });
const ANSWERS = [
  [(l) => l.includes("batch status --list"), json(LIST)],
  [(l) => l.includes("batch status R1"), json(STATUS)],
  [(l) => l.includes("batch report"), json(REPORT)],
  [(l) => l.startsWith("hc ls "), json({ terminals: [] })],
  [(l) => l.startsWith("hc run "), json({ address: "t9" })],
];
const unwrap = (cmd, args) => (cmd === "sh" && args[2] === "sh" ? [args[3], ...args.slice(4)] : [cmd, ...args]);
let unlisted = [];
H.shows = true;
H.onSleep = () => { H.terminalsList.push(...unlisted); unlisted = []; };
H.exec = async (cmd, args, opts = {}) => {
  const line = unwrap(cmd, args).join(" ");
  seen.push({ line, on: opts.on ?? null });
  const hit = ANSWERS.find(([match]) => match(line));
  const shown = hit && line.startsWith("hc run ") && H.shows;
  unlisted.push(...(shown ? [{ address: JSON.parse(hit[1].stdout).address }] : []));
  return hit ? hit[1] : { code: 1, stdout: "", stderr: "unexpected " + line };
};
H.run = (name, args) => { ran.push([name, args]); return null; };
const load = async (on = null) => {
  const host = fakeCx();
  const w = new Workflows(host.cx, () => [{ on, cwd: "/w/box" }]);
  w.show();
  await host.settle();
  return { w, ...host };
};
RUNS_JS
)"

@test "open trail: the trail terminal is kept once herdr lists it" {
  _host "$RUNS
    const { w, settle } = await load();
    w.openTrail({ on: null, id: 'R1', trailRoot: 'inv-1', repo: 'o/n', cwd: '/w/n' }); await settle();
    out({ error: w.trailError, ran, listed: H.terminalsList.map((t) => t.address) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"error":null,"ran":[["terminal.keep",{"terminal":"t9"}]],"listed":["t9"]}' ]
}

@test "open trail: a trail terminal herdr never lists is reported, with no keep" {
  _host "$RUNS
    H.shows = false;
    const { w, settle } = await load();
    w.openTrail({ on: null, id: 'R1', trailRoot: 'inv-1', repo: 'o/n', cwd: '/w/n' }); await settle();
    out({ error: w.trailError, ran });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"error":"started t9, but herdr did not show it within 3s (hc attach t9)","ran":[]}' ]
}

@test "open trail: a throwing terminals() is reported as the trail error, with no keep" {
  _host "$RUNS
    H.terminals = () => { throw new Error('host gone'); };
    const { w, settle } = await load();
    w.openTrail({ on: null, id: 'R1', trailRoot: 'inv-1', repo: 'o/n', cwd: '/w/n' }); await settle();
    out({ error: w.trailError, ran });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"error":"host gone","ran":[]}' ]
}

@test "run session: the board hands Workflows each machine's pr board features" {
  _host "
    const fs = await import('node:fs');
    const doc = JSON.parse(fs.readFileSync(FIX, 'utf8'));
    H.spacesList = [{ active: true, machine: null, folder: '/w/box', tabs: [{ id: 'tab0' }] }];
    H.panesList = [{ pane: 'p0', tab: 'tab0', cwd: '/w/box' }];
    H.focusedPane = { pane: 'p0' };
    H.exec = fakePr(doc);
    const { default: PrsBoard } = await import(M + '/board_view.js');
    const b = new PrsBoard();
    const host = fakeCx();
    b.init({}, host.cx);
    await host.settle();
    out([JSON.stringify(b.workflows.featuresOf(null)) === JSON.stringify(doc.features), b.workflows.featuresOf('elsewhere')]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[true,null]' ]
}

# A standalone PR (no run) opens its target's session: asked on the branch, started in the
# recipe's worktree, and gated on the machine's target-session feature.
@test "target session: Open session asks pr for the branch and starts pr target session in its worktree" {
  _host "$RUNS
    const TARGET = { schema_version: 1, session_id: 'target-abc', name: 'target o/n feat/x', cwd: '/w/feat-x', argv: ['pi'], env: {} };
    ANSWERS.unshift([(l) => l === 'pr --branch feat/x target session --json', json(TARGET)]);
    const { w, settle } = await load();
    w.featuresOf = () => ['target-session'];
    const d = { repo: 'o/n', number: 9, cwd: '/w/n', presentation: { branch: 'feat/x' } };
    const before = w.targetSession(null, d);
    before.onOpen();
    const during = w.targetSession(null, d).opening;
    await settle();
    const after = w.targetSession(null, d);
    out({ gate: before.gate, waiting: before.waiting, during, error: after.error, ran,
      asked: seen.filter((x) => x.line.startsWith('pr --branch') || x.line.startsWith('hc ')).map((x) => x.line.split(' --label')[0]) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"gate":"ready","waiting":null,"during":true,"error":null,"ran":[["terminal.keep",{"terminal":"t9"}]],"asked":["pr --branch feat/x target session --json","hc ls --json","hc run --bg --json --cwd /w/feat-x"]}' ]
}

@test "target session: a machine whose features lack target-session is outdated and runs nothing" {
  _host "$RUNS
    const { w, settle } = await load();
    w.featuresOf = () => ['batch-session'];
    const d = { repo: 'o/n', number: 9, cwd: '/w/n', presentation: { branch: 'feat/x' } };
    const s = w.targetSession(null, d);
    seen.length = 0;
    s.onOpen(); await settle();
    out({ gate: s.gate, error: w.targetSession(null, d).error, seen: seen.length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"gate":"outdated","error":"Update pr on this machine to open sessions (otto-workbench sync)","seen":0}' ]
}

@test "target session: a PR with no branch says so and asks pr nothing" {
  _host "$RUNS
    const { w, settle } = await load();
    w.featuresOf = () => ['target-session'];
    const d = { repo: 'o/n', number: 9, cwd: '/w/n', presentation: { branch: '' } };
    seen.length = 0;
    w.targetSession(null, d).onOpen(); await settle();
    out({ error: w.targetSession(null, d).error, seen: seen.length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"error":"This PR'"'"'s branch is not known yet","seen":0}' ]
}

# The host cannot mount renderDetailHeader (gpui-kit), so the header wiring is pinned by source.
@test "target session: the detail header draws the control only when a targetSession is passed" {
  local detail="$REPO_ROOT/ai/herdr/app/mods/prs/detail.js"
  run grep -c '.when(Boolean(targetSession), (e) => e.child(sessionControl("open-session-target", targetSession)))' "$detail"
  [ "$output" = "1" ]
  run grep -c 'targetSession: this.workflows.targetSession(this.detail.on, this.detail)' "$REPO_ROOT/ai/herdr/app/mods/prs/main.js"
  [ "$output" = "1" ]
}

@test "workflows: the 15 s timer reads the remote's load and then skips batch status while that host is busy" {
  _host "
    const { Workflows } = await import(M + '/workflows.js');
    const tick = () => new Promise((r) => setTimeout(r, 0));
    const seen = []; const loadOns = [];
    H.exec = async (cmd, args, opts = {}) => {
      if (cmd === 'nproc' || (cmd === 'cat' && args[0] === '/proc/loadavg')) {
        loadOns.push(opts.on ?? null);
        const load = opts.on === 'hot' ? '20.0' : '0.1';
        return cmd === 'nproc' ? { code: 0, stdout: '4\\n' } : { code: 0, stdout: load + ' 1 1 1/1 1' };
      }
      const line = (cmd === 'sh' && args[2] === 'sh' ? [args[3], ...args.slice(4)] : [cmd, ...args]).join(' ');
      seen.push(line);
      if (line.includes('batch status --list')) return { code: 0, stdout: JSON.stringify({ runs: [] }), stderr: '' };
      return { code: 1, stdout: '', stderr: 'unexpected ' + line };
    };
    const fire = async (host) => {
      host.timers.filter((x) => x.ms === 15000)[0].fn(host.cx);
      await host.settle();
      await tick();
    };
    const host = fakeCx();
    const w = new Workflows(host.cx, () => [{ on: 'hot', cwd: '/w/box' }]);
    w.show();
    await host.settle();
    const afterShow = seen.length;
    seen.length = 0;
    await fire(host);
    seen.length = 0;
    await fire(host);
    out({ afterShow, probed: [...new Set(loadOns)], skipped: seen });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"afterShow":1,"probed":["hot"],"skipped":[]}' ]
}
