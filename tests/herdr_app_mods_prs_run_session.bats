#!/usr/bin/env bats
# The PRs mod's Open session: Workflows asks _lib/run_session.js to open a run's pi session and
# hands the views {gate, opening, error, waiting, onOpen} for a run row and a Needs-you card.
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
# seen: [{line, on}] with a remote preamble unwrapped; ran: herdr run() calls.
RUNS="$(cat <<'RUNS_JS'
const { Workflows } = await import(M + "/workflows.js");
const seen = []; const ran = [];
const LIST = { runs: [{ id: "R1", run_dir: "/s/R1", started_at: "2026-11-14T10:00:00Z", status: "done", active: false, items: 1, repos: ["o/n"] }] };
const STATUS = { schema_version: 2, active: false, run_dir: "/s/R1", run: { id: "R1", status: "done", steps: ["ci"],
  items: [{ key: "o/n#7", repo: "o/n", pr: 7, status: "awaiting_decision", findings: [{ reason: "ci_failing", step: "ci" }] }] } };
const REPORT = { sessions: [{ pr: "o/n#7", commands: [] }, { pr: "o/n#8", commands: [] }] };
const RECIPE = { schema_version: 1, session_id: "batch-R1", name: "batch o/n R1", cwd: "/w/n", argv: ["pi"], env: {} };
// First match wins; the nesting validator reads this heredoc as shell, so no `if` lines here.
const json = (v) => ({ code: 0, stdout: JSON.stringify(v), stderr: "" });
const ANSWERS = [
  [(l) => l.includes("batch status --list"), json(LIST)],
  [(l) => l.includes("batch status R1"), json(STATUS)],
  [(l) => l.includes("batch report"), json(REPORT)],
  [(l) => l === "pr batch session R1 --json", json(RECIPE)],
  [(l) => l.startsWith("hc ls "), json({ terminals: [] })],
  [(l) => l.startsWith("hc run "), json({ address: "t9" })],
];
const unwrap = (cmd, args) => (cmd === "sh" && args[2] === "sh" ? [args[3], ...args.slice(4)] : [cmd, ...args]);
H.exec = async (cmd, args, opts = {}) => {
  const line = unwrap(cmd, args).join(" ");
  seen.push({ line, on: opts.on ?? null });
  const hit = ANSWERS.find(([match]) => match(line));
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

@test "run session: Open session from a PR's Batch tab starts the session and keeps its tab" {
  _host "$RUNS
    const { w, settle } = await load();
    w.openItem('o/n#7'); await settle();
    const s = w.tabState().runSession;
    const before = { gate: s.gate, waiting: s.waiting, opening: s.opening };
    s.onOpen();
    const during = w.tabState().runSession.opening;
    await settle();
    out({ before, during, after: w.tabState().runSession.error, ran,
      asked: seen.filter((x) => x.line.startsWith('pr batch session') || x.line.startsWith('hc ')).map((x) => x.line.split(' --label')[0]) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"before":{"gate":"unknown","waiting":"waiting on you: 2 PRs","opening":false},"during":true,"after":null,"ran":[["terminal.keep",{"terminal":"t9"}]],"asked":["pr batch session R1 --json","hc ls --json","hc run --bg --json --cwd /w/n"]}' ]
}

@test "run session: a remote run's session is asked of and started on its machine" {
  _host "$RUNS
    const { w, settle } = await load('homelab');
    w.openItem('o/n#7'); await settle();
    w.tabState().runSession.onOpen(); await settle();
    const asked = seen.find((x) => x.line === 'pr batch session R1 --json');
    const started = seen.find((x) => x.line.startsWith('hc run '));
    out({ askedOn: asked.on, startedOn: started.line.includes('--on homelab') });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"askedOn":"homelab","startedOn":true}' ]
}

@test "run session: a machine whose features lack batch-session is outdated and runs nothing" {
  _host "$RUNS
    const { w, settle } = await load();
    w.featuresOf = () => ['review-publish'];
    w.openItem('o/n#7'); await settle();
    const s = w.tabState().runSession;
    seen.length = 0;
    s.onOpen(); await settle();
    out({ gate: s.gate, error: w.tabState().runSession.error, seen: seen.filter((x) => !x.line.includes('batch report')).length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"gate":"outdated","error":"Update pr on this machine to open sessions (otto-workbench sync)","seen":0}' ]
}

@test "run session: the pinned run's row offers Open session with the report's count beside it" {
  _host "$RUNS
    const { w, settle } = await load();
    const unpinned = w.barState().session;
    w.pin(null, 'R1'); await settle();
    const s = w.barState().session;
    const reports = seen.filter((x) => x.line.includes('batch report')).length;
    w.refresh(w.acx); await settle();
    s.onOpen(); await settle();
    out({ unpinned, gate: s.gate, waiting: s.waiting, reports,
      again: seen.filter((x) => x.line.includes('batch report')).length, ran });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"unpinned":null,"gate":"unknown","waiting":"waiting on you: 2 PRs","reports":1,"again":1,"ran":[["terminal.keep",{"terminal":"t9"}]]}' ]
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

@test "run session: the run row and the Needs-you card both draw sessionControl, labelled Open session" {
  local view="$REPO_ROOT/ai/herdr/app/mods/prs/workflows_view.js"
  run grep -c 'sessionControl("open-session-run", session)' "$view"
  [ "$output" = "1" ]
  run grep -c 'sessionControl("open-session", state.runSession)' "$view"
  [ "$output" = "1" ]
  run grep -c 'ghostButton(id, "Open session", () => s.onOpen())' "$view"
  [ "$output" = "1" ]
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
