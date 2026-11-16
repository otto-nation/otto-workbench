#!/usr/bin/env bats
# Space PRs mod: listing-only pulls, no notify in render, #N opens a target session,
# the group header selects the space. The gpui stubs are chainable no-ops (on_click /
# child discarded), so nested clicks cannot be dispatched; cases e/f call openTarget /
# selectSpace and case e also pins the ref handler to openTarget in source.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/space-prs/*.js ai/herdr/app/mods/_lib/*.js ai/herdr/app/mods/_ui/*.js tests/herdr_app_host_helper.bash tests/fixtures/board_document.json

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_app_host_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  _host_setup space-prs
}
teardown() { common_teardown; }

MOD="$(cat <<'MOD_JS'
const fs = await import("node:fs");
const doc = JSON.parse(fs.readFileSync(FIX, "utf8"));
const RECIPE = { session_id: "target-abc", name: "t", cwd: "/w/box" };
doc.rows.forEach((r) => { r.worktree ??= "/wt/" + r.number; });
const spacesOf = () => {
  H.spacesList = [{ id: "s0", name: "box", active: true, machine: null, folder: "/w/box", tabs: [{ id: "tab0" }] }];
  H.panesList = [{ pane: "p0", tab: "tab0", cwd: "/w/box", branch: "me/fix/comments" }];
  H.focusedPane = { pane: "p0" };
  H.terminalsList = [];
  H.agentsList = [];
  H.terminals = () => H.terminalsList;
  H.agents = () => H.agentsList;
};
const { default: SpacePrs } = await import(M + "/main.js");
const { spaceWorktreePrs } = await import(M + "/_lib/space_prs.js");
const groupsOf = (v) => spaceWorktreePrs({
  spaces: H.spacesList, panes: H.panesList,
  terminals: H.terminalsList ?? [], agents: H.agentsList ?? [],
  stateOf: v.store.repoStateOf,
});
const clickOf = (v) => {
  const g = groupsOf(v)[0];
  const row = g?.rows?.[0];
  return row == null ? null : { ...row, on: g.on, spaceId: g.spaceId, repo: g.repo };
};
const labels = () => calls.map((c) => c.label);
const take = () => { const l = labels(); calls.length = 0; return l; };
const start = async (exec = fakePr(doc)) => {
  H.exec = exec;
  const ran = [];
  H.run = (name, args) => { ran.push([name, args]); return null; };
  const v = new SpacePrs();
  const host = fakeCx();
  let rendering = false;
  host.cx.notify = () => { if (rendering) throw new Error("notify during render"); };
  const draw = () => {
    rendering = true;
    try { v.render(host.cx); return { ok: true }; }
    catch (err) {
      const error = String(err?.message ?? err);
      return error.includes("notify during render") ? { ok: false, error } : { ok: true };
    }
    finally { rendering = false; }
  };
  v.init({}, host.cx);
  await host.settle();
  return { v, draw, ran, ...host };
};
MOD_JS
)"

@test "space prs: fakePr pulls are listing-only; never online listing or status" {
  _host "$MOD
    spacesOf();
    await start(fakePr(doc));
    out(labels());"
  [ "$status" -eq 0 ]
  [ "$output" = '["tools","hc:ls:label","pr:listing:offline"]' ]
}

@test "space prs: render does not call cx.notify" {
  _host "$MOD
    spacesOf();
    const { draw } = await start();
    const first = draw();
    const second = draw();
    out({ ok: [first.ok, second.ok], err: first.error || second.error || null });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":[true,true],"err":null}' ]
}

@test "space prs: a space on an open PR branch shows its name and #N" {
  _host "$MOD
    spacesOf();
    const { v, draw } = await start();
    draw();
    const g = groupsOf(v)[0];
    out({ name: g?.spaceName, ref: g?.rows?.[0]?.pr ? '#' + g.rows[0].pr.number : null, n: groupsOf(v).length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"name":"box","ref":"#7","n":1}' ]
}

@test "space prs: a new pane cwd re-pulls on the next render" {
  _host "$MOD
    spacesOf();
    const { draw, settle } = await start();
    draw(); await settle(); take();
    H.panesList = [{ pane: 'p0', tab: 'tab0', cwd: '/w/other', branch: 'me/fix/comments' }];
    draw(); await settle();
    out(take());"
  [ "$status" -eq 0 ]
  [ "$output" = '["hc:ls:label","pr:listing:offline"]' ]
}

@test "space prs: the #N ref asks pr target session and focuses a matching agent pane" {
  _host "$MOD
    const seen = [];
    const base = fakePr(doc, (l) => (l === 'pr:--branch' ? { code: 0, stdout: JSON.stringify(RECIPE), stderr: '' } : null));
    const exec = async (cmd, args, opts = {}) => { seen.push([cmd, ...args].join(' ')); return base(cmd, args, opts); };
    spacesOf();
    H.spacesList.push({ id: 's-agent', name: 'agent', machine: null, folder: '/w/box', tabs: [{ id: 'tab-agent' }] });
    H.panesList.push({ pane: 'p-agent', tab: 'tab-agent', cwd: '/w/box', status: 'working', terminal: 't-agent' });
    const { v, draw, ran, settle } = await start(exec);
    draw(); await settle();
    seen.length = 0; ran.length = 0;
    const src = fs.readFileSync(M + '/main.js', 'utf8');
    v.openTarget(clickOf(v)); await settle();
    out({ wired: src.includes('this.openTarget(row)'), seen, ran });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"wired":true,"seen":["pr --branch me/fix/comments target session --json","hc ls --json --label otto.session=target-abc"],"ran":[["space.select",{"id":"s-agent"}],["tab.select",{"id":"tab-agent"}],["pane.focus",{"pane":"p-agent"}]]}' ]
}

@test "space prs: a row click selects that space and does not ask target session" {
  _host "$MOD
    const seen = [];
    const base = fakePr(doc);
    const exec = async (cmd, args, opts = {}) => { seen.push([cmd, ...args].join(' ')); return base(cmd, args, opts); };
    spacesOf();
    const { v, draw, ran, settle } = await start(exec);
    draw(); await settle();
    seen.length = 0; ran.length = 0;
    v.selectSpace(groupsOf(v)[0]);
    out({ seen, ran });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"seen":[],"ran":[["space.select",{"id":"s0"}]]}' ]
}

@test "space prs: an owned row's #N focuses the labelled agent and asks pr nothing" {
  _host "$MOD
    const seen = [];
    const base = fakePr(doc);
    const exec = async (cmd, args, opts = {}) => { seen.push([cmd, ...args].join(' ')); return base(cmd, args, opts); };
    spacesOf();
    H.spacesList.push({ id: 's-agent', name: 'agent', machine: null, folder: '/w/box', tabs: [{ id: 'tab-agent' }] });
    H.panesList.push({ pane: 'p-own', tab: 'tab-agent', cwd: '/w/box', status: 'idle', terminal: 't-own' });
    H.terminalsList = [{ address: 't-own', labels: { 'otto.worktree': '/wt/7' } }];
    const { v, draw, ran, settle } = await start(exec);
    draw(); await settle();
    seen.length = 0; ran.length = 0;
    const row = clickOf(v);
    v.openTarget(row); await settle();
    out({ owner: row.owner?.terminal ?? null, seen, ran });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"owner":"t-own","seen":[],"ran":[["space.select",{"id":"s-agent"}],["tab.select",{"id":"tab-agent"}],["pane.focus",{"pane":"p-own"}]]}' ]
}

@test "space prs: with no owner, #N focuses a shell pane in the worktree and asks pr nothing" {
  _host "$MOD
    const seen = [];
    const base = fakePr(doc);
    const exec = async (cmd, args, opts = {}) => { seen.push([cmd, ...args].join(' ')); return base(cmd, args, opts); };
    spacesOf();
    H.panesList.push({ pane: 'p-sh', tab: 'tab0', cwd: '/wt/7', status: null, terminal: 't-sh' });
    const { v, draw, ran, settle } = await start(exec);
    draw(); await settle();
    seen.length = 0; ran.length = 0;
    const row = clickOf(v);
    v.openTarget(row); await settle();
    out({ owner: row.owner, seen, ran });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"owner":null,"seen":[],"ran":[["space.select",{"id":"s0"}],["tab.select",{"id":"tab0"}],["pane.focus",{"pane":"p-sh"}]]}' ]
}

@test "space prs: with neither, #N asks pr target session from the worktree" {
  _host "$MOD
    const cwds = [];
    const base = fakePr(doc, (l) => (l === 'pr:--branch' ? { code: 0, stdout: JSON.stringify(RECIPE), stderr: '' } : null));
    const exec = async (cmd, args, opts = {}) => {
      if (cmd === 'pr' && args[0] === '--branch') cwds.push(opts.cwd ?? null);
      return base(cmd, args, opts);
    };
    spacesOf();
    const { v, draw, settle } = await start(exec);
    draw(); await settle();
    v.openTarget(clickOf(v)); await settle();
    out(cwds);"
  [ "$status" -eq 0 ]
  [ "$output" = '["/wt/7"]' ]
}
