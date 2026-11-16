#!/usr/bin/env bats
# Render must not call cx.notify (herdr throws; the last good frame stays).
# The harness stubs gpui chains, so a test can pass even if a host call other
# than notify is illegal in render.
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

BOARD="$(cat <<'BOARD_JS'
const fs = await import("node:fs");
const doc = JSON.parse(fs.readFileSync(FIX, "utf8"));
const spacesOf = (folders, machine = null) => {
  H.spacesList = folders.map((f, i) => ({ active: i === 0, machine, folder: f, tabs: [{ id: "tab" + i }] }));
  H.panesList = folders.map((f, i) => ({ pane: "p" + i, tab: "tab" + i, cwd: f }));
  H.focusedPane = { pane: "p0" };
};
const { default: PrsBoard } = await import(M + "/main.js");
const start = async (exec = fakePr(doc)) => {
  H.exec = exec;
  const b = new PrsBoard();
  const host = fakeCx();
  let rendering = false;
  host.cx.notify = () => {
    if (rendering) throw new Error("notify during render");
  };
  const draw = (board) => {
    rendering = true;
    try {
      board.render(host.cx);
      return { ok: true };
    } catch (err) {
      const error = String(err?.message ?? err);
      if (error.includes("notify during render")) return { ok: false, error };
      return { ok: true };
    } finally {
      rendering = false;
    }
  };
  b.init({}, host.cx);
  await host.settle();
  return { b, draw, ...host };
};
BOARD_JS
)"

@test "render notify: switching PRs does not notify; reset clears picker and openKey" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const { b, cx, draw } = await start();
    const prs = b.stateView().prs;
    const a = prs.find((p) => p.number === 7);
    const next = prs.find((p) => p.number === 8);
    let resets = 0;
    const reset = b.models.reset.bind(b.models);
    b.models.reset = () => { resets += 1; reset(); };
    b.openDetail(a, cx);
    b.openKey = 'run-a';
    const first = draw(b);
    b.openDetail(next, cx);
    const second = draw(b);
    out({ ok: [first.ok, second.ok], err: first.error || second.error || null, resets, openKey: b.openKey });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":[true,true],"err":null,"resets":1,"openKey":null}' ]
}

@test "render notify: a Batch-tab PR with an item does not notify; openKey matches the item" {
  _host "$BOARD
    spacesOf(['/w/box']);
    const { b, cx, draw, settle } = await start();
    const pr = b.stateView().prs.find((p) => p.number === 7);
    const item = { key: pr.repoSlug + '#' + pr.number, status: 'running' };
    b.workflows.itemFor = (slug, n) => (slug === pr.repoSlug && n === pr.number ? item : null);
    b.tab = 'batch';
    b.openDetail(pr, cx);
    const first = draw(b);
    const second = draw(b);
    await settle();
    out({ ok: [first.ok, second.ok], err: first.error || second.error || null, openKey: b.workflows.openKey });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":[true,true],"err":null,"openKey":"acme/box#7"}' ]
}

@test "render notify: a board with no targets does not notify on draw" {
  _host "$BOARD
    const { b, draw } = await start();
    b.activeTargets = () => [];
    b.workflows.keys = null;
    const first = draw(b);
    const second = draw(b);
    out({ ok: [first.ok, second.ok], err: first.error || second.error || null });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":[true,true],"err":null}' ]
}
