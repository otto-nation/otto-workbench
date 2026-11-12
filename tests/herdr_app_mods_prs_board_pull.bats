#!/usr/bin/env bats
# Pins prs/board_view.js's pull sequence — tools, terminals, pr board phases, owed cadence,
# representatives, stale keys, errors — so moving it into _lib is provably behaviour-preserving.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/prs/*.js ai/herdr/app/mods/_lib/*.js ai/herdr/app/mods/_ui/*.js tests/herdr_app_host_helper.bash tests/fixtures/board_document.json

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_app_host_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  _host_setup
}
teardown() { common_teardown; }

# One space per folder; the first is active and focused. `machine` makes it a remote target.
BOARD="$(cat <<'BOARD_JS'
const fs = await import("node:fs");
const doc = JSON.parse(fs.readFileSync(FIX, "utf8"));
const docAs = (slug, phase, extra = {}) => ({ code: 0, stdout: JSON.stringify({ ...doc, phase, stamp: "st-" + phase, repos: [{ ...doc.repos[0], slug }], ...extra }), stderr: "" });
const spacesOf = (folders, machine = null) => {
  H.spacesList = folders.map((f, i) => ({ active: i === 0, machine, folder: f, tabs: [{ id: "tab" + i }] }));
  H.panesList = folders.map((f, i) => ({ pane: "p" + i, tab: "tab" + i, cwd: f }));
  H.focusedPane = { pane: "p0" };
};
let t = 1000;
Date.now = () => t;
const { default: PrsBoard } = await import(M + "/board_view.js");
// Base fallback: before Step 5 (and at the merge base check-new-tests runs) PrsBoard has no
// stateView; this is render's own lines 306-308, so those runs read exactly the view render draws.
if (!PrsBoard.prototype.stateView) {
  const { boardState } = await import(M + "/_lib/scope.js");
  const key = (c) => JSON.stringify({ on: c?.on ?? null, cwd: c?.cwd ?? null });
  PrsBoard.prototype.stateView = function () {
    const targets = this.activeTargets();
    return boardState(targets, targets.map((c) => this.cache.get(key(c)) ?? {}), H.state["prs.scope"] === "all" ? "all" : "this");
  };
}
// The workbench layout with the PRs panel shown or hidden (_lib/fullscreen.js node ids).
const layoutWith = (hidden) => ({ id: "root", children: [{ id: "main" },
  { id: "panel", ...(hidden ? { hidden: true } : {}), children: [{ id: "panel-body", active: "prs-panel" }] }] });
// Resolves when an exec call labelled `l` has been made (a gated answer is waiting on it).
const reached = async (l) => { for (let i = 0; i < 200 && !labels().includes(l); i++) await new Promise((r) => setTimeout(r, 1)); };
const start = async (exec) => {
  H.exec = exec;
  const b = new PrsBoard();
  const host = fakeCx();
  b.init({}, host.cx);
  await host.settle();
  return { b, ...host };
};
const labels = () => calls.map((c) => c.label);
const take = () => { const l = labels(); calls.length = 0; return l; };
BOARD_JS
)"

@test "owed on demand: first pull checks tools, reads terminals, then listing and status; Team reads no owed and its repo's owed is idle" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const { b, timers } = await start(fakePr(doc));
    const v = b.stateView();
    out({ calls: labels(), every: timers.filter((x) => x.kind === 'every').map((x) => x.ms),
          repo: v.repoStates, n: v.prs.length, errors: v.errors });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"calls":["tools","hc:ls:label","pr:listing","pr:status"],"every":[15000,3000,180000,15000],"repo":[{"slug":"acme/box","folder":"box","on":null,"phase":"loaded","error":null,"partial":false,"owed":{"state":"idle","error":null},"updatedAt":1000}],"n":4,"errors":[]}' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: a listing-only refresh with a cached repo skips the tools check and reads the cache offline" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const { b, cx, settle } = await start(fakePr(doc)); take();
    b.refresh(cx, { listingOnly: true }); await settle();
    out(take());"
  [ "$status" -eq 0 ]
  [ "$output" = '["hc:ls:label","pr:listing:offline"]' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: the 15 s timer polls a remote target's listing and leaves a local one to the watch" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const local = await start(fakePr(doc)); take();
    local.timers.filter((x) => x.ms === 15000).at(-1).fn(local.cx); await local.settle();
    const l = take();
    spacesOf(['/w/box'], 'mac');
    const remote = await start(fakePr(doc)); take();
    remote.timers.filter((x) => x.ms === 15000).at(-1).fn(remote.cx); await remote.settle();
    out({ local: l, remote: calls.map((c) => c.label + '@' + c.on) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"local":[],"remote":["hc:ls:label@null","pr:listing:offline@mac"]}' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: Mine reads owed after status, then only once a minute; Has Comments rows come from it" {
  _host "$BOARD
    spacesOf(['/w/box']); H.state['prs.tab'] = 'mine';
    const { b, cx, settle } = await start(fakePr(doc));
    const first = take();
    t = 1030; b.refresh(cx, { listingOnly: true }); await settle(); const soon = take();
    t = 61000; b.refresh(cx, { listingOnly: true }); await settle(); const due = take();
    const seven = b.stateView().prs.find((p) => p.number === 7);
    out({ first, soon, due, lane: seven.lanes.mine });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"first":["tools","hc:ls:label","pr:listing","pr:status","pr:owed"],"soon":["hc:ls:label","pr:listing:offline"],"due":["hc:ls:label","pr:listing:offline:stamp","pr:owed:stamp"],"lane":"has-comments"}' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: switching to Mine is a comments-only refresh: offline listing, then owed" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const { b, settle } = await start(fakePr(doc)); take();
    b.showMine(); await settle();
    out(take());"
  [ "$status" -eq 0 ]
  [ "$output" = '["hc:ls:label","pr:listing:offline","pr:owed"]' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: refreshes asked for during a pull collapse into one more, and a full one wins" {
  _host "$BOARD
    spacesOf(['/w/box']);
    H.exec = fakePr(doc);
    const b = new PrsBoard(); const { cx, settle } = fakeCx();
    b.init({}, cx); b.refresh(cx, { listingOnly: true }); b.refresh(cx);
    await settle();
    out(labels());"
  [ "$status" -eq 0 ]
  [ "$output" = '["tools","hc:ls:label","pr:listing","pr:status","hc:ls:label","pr:listing:stamp","pr:status:stamp"]' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: Refresh asks for every row (no stamps) and clears its label when the pull lands" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const { b, settle } = await start(fakePr(doc)); take();
    b.refreshNow(); const during = b.manual; await settle();
    out({ calls: take(), during, after: b.manual });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"calls":["hc:ls:label","pr:listing","pr:status"],"during":true,"after":false}' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: All spaces pulls one checkout per repo once the repo is known" {
  _host "$BOARD
    spacesOf(['/w/box', '/w/box2']); H.state['prs.scope'] = 'all';
    const { b, cx, settle } = await start(fakePr(doc));
    const first = labels().filter((l) => l === 'pr:status').length; take();
    b.refresh(cx); await settle();
    out({ first, second: labels().filter((l) => l === 'pr:status').length, active: b.activeTargets().map((c) => c.cwd), repos: b.stateView().repoStates.length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"first":2,"second":0,"active":["/w/box"],"repos":1}' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: a checkout no space shows any more is forgotten (its next pull sends no stamp)" {
  _host "$BOARD
    const answer = (l, o) => (l.startsWith('pr:') && o.cwd === '/w/kit' ? docAs('acme/kit', l.split(':')[1]) : null);
    spacesOf(['/w/box', '/w/kit']); H.state['prs.scope'] = 'all';
    const { b, cx, settle } = await start(fakePr(doc, answer));
    spacesOf(['/w/box']); b.refresh(cx); await settle();
    spacesOf(['/w/box', '/w/kit']); take(); b.refresh(cx); await settle();
    out(calls.filter((c) => c.label.startsWith('pr:listing')).map((c) => c.cwd + ' ' + c.label).sort());"
  [ "$status" -eq 0 ]
  [ "$output" = '["/w/box pr:listing:stamp","/w/kit pr:listing"]' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: a failed refresh over loaded rows is stale with the last good time; never loaded is failed" {
  _host "$BOARD
    spacesOf(['/w/box']);
    let broken = false;
    const { b, cx, settle } = await start(fakePr(doc, (l) => (broken && l.startsWith('pr:') ? { code: 1, stdout: '', stderr: 'boom' } : null)));
    broken = true; t = 2000; b.refresh(cx); await settle();
    const v = b.stateView();
    const fresh = await start(fakePr(doc, (l) => (l.startsWith('pr:') ? { code: 1, stdout: '', stderr: 'boom' } : null)));
    const f = fresh.b.stateView();
    out({ stale: [v.repoStates[0].phase, v.errors[0].updatedAt, v.errors[0].error.kind, v.prs.length],
          failed: [f.repoStates[0].phase, f.errors[0].updatedAt] });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"stale":["stale",1000,"failed",4],"failed":["failed",null]}' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: a missing gh is an install error naming it, before any terminal or pr call" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const { b } = await start(fakePr(doc, (l) => (l === 'tools' ? { code: 0, stdout: '/b/pr\n', stderr: '' } : null)));
    out({ calls: labels(), error: b.stateView().errors[0].error });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"calls":["tools"],"error":{"kind":"missing-tool","tools":["gh"],"message":"gh"}}' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: a space with no folder reads Not in a repository and runs nothing" {
  _host "$BOARD
    H.spacesList = [{ active: true, machine: null, folder: null, tabs: [] }]; H.panesList = []; H.focusedPane = null;
    const { b } = await start(fakePr(doc));
    out({ calls: labels(), error: b.stateView().errors[0].error });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"calls":[],"error":{"kind":"failed","message":"Not in a repository"}}' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: a pull whose target stopped being shown commits nothing" {
  _host "$BOARD
    let release; const gate = new Promise((r) => { release = r; });
    spacesOf(['/w/box', '/w/kit']);
    H.exec = fakePr(doc, async (l, o) => (l === 'pr:listing' && o.cwd === '/w/box' ? (await gate, null) : null));
    const b = new PrsBoard(); const { cx, settle } = fakeCx();
    b.init({}, cx);
    for (let i = 0; i < 100 && !labels().includes('pr:listing'); i++) await new Promise((r) => setTimeout(r, 1));
    H.spacesList[0].active = false; H.spacesList[1].active = true; H.focusedPane = { pane: 'p1' };
    release(); await settle();
    H.spacesList[0].active = true; H.spacesList[1].active = false; H.focusedPane = { pane: 'p0' };
    take(); b.refresh(cx); await settle();
    out(labels().filter((l) => l.startsWith('pr:')));"
  [ "$status" -eq 0 ]
  [ "$output" = '["pr:listing","pr:status"]' ]
}

# passes-at-base: characterizes the mod before the extraction
@test "mod pull: a local target's reviews root is watched once; a failed watch falls back to polling" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const watched = [];
    H.watchFolder = (root) => { watched.push(root); return Promise.reject(new Error('no fsevents')); };
    const { b, cx, settle } = await start(fakePr({ ...doc, reviews_root: '/r' }));
    b.refresh(cx); await settle();
    out({ watched, poll: b.pollListing });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"watched":["/r"],"poll":true}' ]
}

@test "owed on demand: a failed owed read is the repo's owed error; rows kept, the repo loaded" {
  _host "$BOARD
    spacesOf(['/w/box']); H.state['prs.tab'] = 'mine';
    const { b } = await start(fakePr(doc, (l) => (l.startsWith('pr:owed') ? { code: 1, stdout: '', stderr: 'boom' } : null)));
    const v = b.stateView();
    out({ calls: labels(), errors: v.errors, phase: v.repoStates[0].phase, n: v.prs.length, owed: v.repoStates[0].owed ?? null });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"calls":["tools","hc:ls:label","pr:listing","pr:status","pr:owed"],"errors":[],"phase":"loaded","n":4,"owed":{"state":"error","error":{"kind":"failed","message":"boom"}}}' ]
}

@test "owed on demand: an owed answer that lands after Mine->Team is dropped" {
  _host "$BOARD
    spacesOf(['/w/box']); H.state['prs.tab'] = 'mine';
    let release; const gate = new Promise((r) => { release = r; });
    const patched = { ...doc, phase: 'owed', complete: false, stamp: 'st-owed',
      rows: [{ ...doc.rows[1], lanes: { ...doc.rows[1].lanes, mine: 'has-comments' } }] };
    H.exec = fakePr(doc, async (l) => (l === 'pr:owed' ? (await gate, { code: 0, stdout: JSON.stringify(patched), stderr: '' }) : null));
    const b = new PrsBoard(); const { cx, settle } = fakeCx();
    b.init({}, cx);
    await reached('pr:owed');
    H.state['prs.tab'] = 'team';
    release(); await settle();
    const v = b.stateView();
    out({ lane: v.prs.find((p) => p.number === 8).lanes.mine, owed: v.repoStates[0].owed ?? null });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"lane":"awaiting-review","owed":{"state":"idle","error":null}}' ]
}

@test "owed on demand: a hidden panel reads no owed; coming on screen on Mine reads it" {
  _host "$BOARD
    spacesOf(['/w/box']); H.state['prs.tab'] = 'mine';
    H.props = () => ({ placement: 'panel' }); H.layout = () => layoutWith(true);
    const { timers, cx, settle } = await start(fakePr(doc));
    const hidden = take();
    H.layout = () => layoutWith(false);
    timers.find((x) => x.ms === 1000).fn(cx); await settle();
    out({ hidden, shown: take() });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"hidden":["tools","hc:ls:label","pr:listing","pr:status"],"shown":["hc:ls:label","pr:listing:offline","pr:owed"]}' ]
}
