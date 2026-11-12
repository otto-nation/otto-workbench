#!/usr/bin/env bats
# The mod draws Has Comments loading and error from the shared board model.
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
let t = 1000;
Date.now = () => t;
const { default: PrsBoard } = await import(M + "/board_view.js");
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

@test "owed view: switching Team->Mine shows Has Comments loading, then ready once owed lands" {
  _host "$BOARD
    spacesOf(['/w/box']);
    let release; const gate = new Promise((r) => { release = r; });
    const { b, settle } = await start(fakePr(doc, async (l) => (l === 'pr:owed' ? (await gate, null) : null)));
    const lane = () => b.model(b.stateView(), 'mine').lanes.find((l) => l.key === 'has-comments');
    b.showMine();
    await reached('pr:owed');
    const during = [lane().status, lane().message];
    release(); await settle();
    out({ during, after: lane().status });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"during":["loading","Loading comments…"],"after":"ready"}' ]
}

@test "owed view: Mine->Team stops owed reads; refreshes on Team read none" {
  _host "$BOARD
    spacesOf(['/w/box']); H.state['prs.tab'] = 'mine';
    const { b, cx, settle } = await start(fakePr(doc)); take();
    H.state['prs.tab'] = 'team';
    t = 200000; b.refresh(cx); await settle(); b.refresh(cx, { listingOnly: true }); await settle();
    out(take().filter((l) => l.startsWith('pr:owed')));"
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "owed view: both layouts hand the lane's status and message to the shared components" {
  run grep -c 'status: col.status, message: col.message' "$REPO_ROOT/ai/herdr/app/mods/prs/board.js"
  [ "$output" = "1" ]
  run grep -c 'status: s.status, message: s.message' "$REPO_ROOT/ai/herdr/app/mods/prs/compact.js"
  [ "$output" = "1" ]
}
