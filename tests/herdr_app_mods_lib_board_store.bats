#!/usr/bin/env bats
# _lib/board_store.js: the board's pulls over many checkouts, shared by the mod and the web server.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js tests/fixtures/board_document.json

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  LIB="$REPO_ROOT/ai/herdr/app/mods/_lib"
  FIX="$REPO_ROOT/tests/fixtures/board_document.json"
}
teardown() { common_teardown; }

# $1: a script body with `s` (board_store), `doc`, `calls`, `fake(answer?)`, `docAs(slug, phase)`.
# Body is argv, not interpolated into -e: the plan's double-quoted literals cannot live in a
# double-quoted node -e (Task 2 harness ruling). node --no-deprecation: DEP0205 on node 26.
_store() {
  local runjs="$BATS_TEST_TMPDIR/store-run.mjs"
  {
    printf 'import fs from "node:fs";\n'
    printf 'const s = await import(%s);\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$LIB/board_store.js")"
    printf 'const doc = JSON.parse(fs.readFileSync(%s, "utf8"));\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$FIX")"
    printf '%s\n' \
      'const calls = [];' \
      'const docAs = (slug, phase, extra = {}) => ({ code: 0, stdout: JSON.stringify({ ...doc, phase, stamp: "st-" + phase, repos: [{ ...doc.repos[0], slug }], ...extra }), stderr: "" });' \
      'const fake = (answer = () => null) => async (cmd, args, opts = {}) => {' \
      '  const l = cmd === "sh" ? "tools" : cmd === "hc" ? "hc" : "pr:" + args[args.indexOf("--phase") + 1] + (args.includes("--offline") ? ":offline" : "") + (args.includes("--if-changed") ? ":stamp" : "");' \
      '  calls.push({ l, cwd: opts.cwd ?? null });' \
      '  const c = await answer(l, opts); if (c) return c;' \
      '  if (l === "tools") return { code: 0, stdout: "/b/pr\n/b/gh\n", stderr: "" };' \
      '  if (l === "hc") return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };' \
      '  return docAs("acme/box", l.split(":")[1].replace(":offline", ""));' \
      '};' \
      "const body = new (async () => {}).constructor('s', 'doc', 'calls', 'fake', 'docAs', process.argv[2]);" \
      'await body(s, doc, calls, fake, docAs);'
  } > "$runjs"
  run node --no-deprecation "$runjs" "$1"
}

@test "store: a pull checks tools, reads terminals once, runs the phases and reports each change" {
  _store '
    const st = s.createBoardStore({ exec: fake(), now: () => 7 });
    const ev = [];
    const r = await st.pull({ targets: [{ on: null, cwd: "/w/a" }, { on: null, cwd: "/w/b" }] },
      { onChange: () => ev.push("change"), onCommit: (state, ctx) => ev.push("commit:" + ctx.cwd + ":" + state.phase) });
    const v = st.view([{ on: null, cwd: "/w/a" }], "all");
    process.stdout.write(JSON.stringify({ hc: calls.filter((c) => c.l === "hc").length, ev: ev.filter((e) => e.includes("/w/a") || e === "change").length,
      first: ev.slice(0, 3), terminals: r.terminals, phase: v.repoStates[0].phase, at: v.repoStates[0].updatedAt }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"hc":1,"ev":9,"first":["change","commit:/w/a:hydrating","change"],"terminals":[],"phase":"loaded","at":7}' ]
}

@test "store: owed rides a pull only when want asks for it and it is due; the default wants nothing" {
  _store '
    let t = 1000;
    const st = s.createBoardStore({ exec: fake(), now: () => t });
    const ctx = { on: null, cwd: "/w/a" };
    const mine = () => ({ owed: true });
    await st.pull({ targets: [ctx], want: mine }); const a = calls.map((c) => c.l); calls.length = 0;
    t = 1010; await st.pull({ targets: [ctx], want: mine, listingOnly: true }); const b = calls.map((c) => c.l); calls.length = 0;
    t = 70000; await st.pull({ targets: [ctx], want: mine, listingOnly: true }); const c = calls.map((x) => x.l); calls.length = 0;
    t = 140000; await st.pull({ targets: [ctx], listingOnly: true }); const d = calls.map((x) => x.l);
    process.stdout.write(JSON.stringify([a, b, c, d]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["tools","hc","pr:listing","pr:status","pr:owed"],["hc","pr:listing:offline"],["hc","pr:listing:offline:stamp","pr:owed:stamp"],["hc","pr:listing:offline:stamp"]]' ]
}

@test "store: listing-only skips tools only when a repo is already cached" {
  _store '
    const ctx = { on: null, cwd: "/w/a" };
    const st = s.createBoardStore({ exec: fake() });
    await st.pull({ targets: [ctx], listingOnly: true });
    const first = calls.map((c) => c.l);
    calls.length = 0;
    await st.pull({ targets: [ctx], listingOnly: true, isCurrent: () => false });
    const second = calls.map((c) => c.l);
    process.stdout.write(JSON.stringify({ first, second }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"first":["tools","hc","pr:listing:offline"],"second":["hc"]}' ]
}

@test "store: active keeps one target per known repo; keep() prunes the rest" {
  _store '
    const st = s.createBoardStore({ exec: fake((l, o) => (l.startsWith("pr:") && o.cwd === "/w/kit" ? docAs("acme/kit", l.split(":")[1]) : null)) });
    const box = { on: null, cwd: "/w/box" }, box2 = { on: null, cwd: "/w/box2" }, kit = { on: null, cwd: "/w/kit" };
    const before = st.active([box, box2, kit]).length;
    await st.pull({ targets: [box, box2, kit] });
    const after = st.active([box, box2, kit]).map((c) => c.cwd);
    await st.pull({ targets: [box], keep: () => [box] });
    process.stdout.write(JSON.stringify({ before, after, kit: st.stateOf(kit), box2: st.stateOf(box2) }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"before":3,"after":["/w/box","/w/kit"],"kit":{},"box2":{}}' ]
}

@test "store: a target no longer current commits nothing; no folder reads Not in a repository" {
  _store '
    const st = s.createBoardStore({ exec: fake() });
    const ctx = { on: null, cwd: "/w/a" };
    await st.pull({ targets: [ctx, { on: null, cwd: null }], isCurrent: () => false });
    process.stdout.write(JSON.stringify([st.stateOf(ctx), st.stateOf({ on: null, cwd: null }).error, calls.map((c) => c.l)]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{},{"kind":"failed","message":"Not in a repository"},["tools"]]' ]
}

@test "store: a failed pull keeps rows and the last good time, drops the phase, and reads stale" {
  _store '
    let t = 5, broken = false;
    const st = s.createBoardStore({ exec: fake((l) => (broken && l.startsWith("pr:") ? { code: 1, stdout: "", stderr: "boom" } : null)), now: () => t });
    const ctx = { on: null, cwd: "/w/a" };
    await st.pull({ targets: [ctx] }); broken = true; t = 9; await st.pull({ targets: [ctx] });
    const state = st.stateOf(ctx), v = st.view([ctx], "all");
    process.stdout.write(JSON.stringify([state.stale, "phase" in state, state.rows.length, v.repoStates[0].phase, v.errors[0].updatedAt]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false,4,"stale",5]' ]
}

@test "store: tools are checked once per machine; a missing tool fails the target by name" {
  _store '
    const st = s.createBoardStore({ exec: fake((l, o) => (l === "tools" && o.on === "m" ? { code: 0, stdout: "/b/gh\n", stderr: "" } : null)) });
    await st.pull({ targets: [{ on: null, cwd: "/w/a" }] }); await st.pull({ targets: [{ on: null, cwd: "/w/a" }] });
    await st.pull({ targets: [{ on: "m", cwd: "/w/r" }] });
    process.stdout.write(JSON.stringify([calls.filter((c) => c.l === "tools").length, st.stateOf({ on: "m", cwd: "/w/r" }).error.tools]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[2,["pr"]]' ]
}

@test "store: reviewsRoot answers per machine from the committed document" {
  _store '
    const st = s.createBoardStore({ exec: fake((l) => (l.startsWith("pr:") ? docAs("acme/box", l.split(":")[1], { reviews_root: "/r" }) : null)) });
    await st.pull({ targets: [{ on: null, cwd: "/w/a" }] });
    process.stdout.write(JSON.stringify([st.reviewsRoot(null), st.reviewsRoot("mac")]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["/r",null]' ]
}

@test "store: owed goes idle, loading, ready while wanted, each change reported" {
  _store '
    const st = s.createBoardStore({ exec: fake(), now: () => 1000 });
    const ctx = { on: null, cwd: "/w/a" };
    const seen = [];
    const before = st.stateOf(ctx).owedState ?? "idle";
    await st.pull({ targets: [ctx], want: () => ({ owed: true }) }, { onChange: () => seen.push(st.stateOf(ctx).owedState ?? "idle") });
    process.stdout.write(JSON.stringify([before, [...new Set(seen)], st.stateOf(ctx).owedState, st.view([ctx], "all").repoStates[0].owed]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["idle",["idle","loading","ready"],"ready",{"state":"ready","error":null}]' ]
}

@test "store: an owed answer that lands after want turned false is dropped and the repo goes back to idle" {
  _store '
    let showing = true;
    const st = s.createBoardStore({ exec: fake((l) => { if (l.startsWith("pr:owed")) showing = false; return null; }), now: () => 1000 });
    const ctx = { on: null, cwd: "/w/a" };
    await st.pull({ targets: [ctx], want: () => ({ owed: showing }) });
    process.stdout.write(JSON.stringify([st.stateOf(ctx).owedState, st.stateOf(ctx).owedAt ?? null]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["idle",null]' ]
}

@test "store: a failed owed read is error with its reason, rows kept; the next good read is ready" {
  _store '
    let t = 1000, broken = true;
    const st = s.createBoardStore({ exec: fake((l) => (broken && l.startsWith("pr:owed") ? { code: 1, stdout: "", stderr: "boom" } : null)), now: () => t });
    const ctx = { on: null, cwd: "/w/a" }, want = () => ({ owed: true });
    await st.pull({ targets: [ctx], want });
    const a = st.stateOf(ctx);
    broken = false; t = 70000; await st.pull({ targets: [ctx], want, listingOnly: true });
    process.stdout.write(JSON.stringify([a.owedState, a.owedError, a.rows.length, a.stale, st.stateOf(ctx).owedState]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["error",{"kind":"failed","message":"boom"},4,false,"ready"]' ]
}

@test "store: a failed owed read retries on the owedDue cadence, not every pull" {
  _store '
    let t = 1000;
    const st = s.createBoardStore({ exec: fake((l) => (l.startsWith("pr:owed") ? { code: 1, stdout: "", stderr: "boom" } : null)), now: () => t });
    const ctx = { on: null, cwd: "/w/a" }, want = () => ({ owed: true });
    await st.pull({ targets: [ctx], want });
    const afterFail = [st.stateOf(ctx).owedState, st.stateOf(ctx).owedError];
    calls.length = 0;
    t = 1030; await st.pull({ targets: [ctx], want, listingOnly: true });
    const at30 = calls.filter((c) => c.l.startsWith("pr:owed")).length;
    const mid = st.stateOf(ctx).owedState;
    calls.length = 0;
    t = 62000; await st.pull({ targets: [ctx], want, listingOnly: true });
    const at60 = calls.filter((c) => c.l.startsWith("pr:owed")).length;
    process.stdout.write(JSON.stringify({ afterFail, at30, mid, at60 }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"afterFail":["error",{"kind":"failed","message":"boom"}],"at30":0,"mid":"error","at60":1}' ]
}

@test "store: a dropped first owed read stays due; a dropped re-read keeps the previous good owedAt" {
  _store '
    let showing = true, t = 1000, drop = true;
    const st = s.createBoardStore({ exec: fake((l) => { if (drop && l.startsWith("pr:owed")) showing = false; return null; }), now: () => t });
    const ctx = { on: null, cwd: "/w/a" };
    await st.pull({ targets: [ctx], want: () => ({ owed: showing }) });
    const first = [st.stateOf(ctx).owedState, st.stateOf(ctx).owedAt ?? null];
    showing = true; drop = true; calls.length = 0;
    t = 1030; await st.pull({ targets: [ctx], want: () => ({ owed: showing }), listingOnly: true });
    const stillDue = calls.filter((c) => c.l.startsWith("pr:owed")).length;
    drop = false; showing = true;
    t = 70000; await st.pull({ targets: [ctx], want: () => ({ owed: showing }), listingOnly: true });
    const good = st.stateOf(ctx).owedAt;
    drop = true; showing = true; calls.length = 0;
    t = 140000; await st.pull({ targets: [ctx], want: () => ({ owed: showing }), listingOnly: true });
    process.stdout.write(JSON.stringify({
      first, stillDue, good, afterDrop: st.stateOf(ctx).owedAt, state: st.stateOf(ctx).owedState,
      reread: calls.filter((c) => c.l.startsWith("pr:owed")).length,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"first":["idle",null],"stillDue":1,"good":70000,"afterDrop":70000,"state":"ready","reread":1}' ]
}

@test "store: nothing wanted never marks owed loading and never reads it" {
  _store '
    const st = s.createBoardStore({ exec: fake() });
    const ctx = { on: null, cwd: "/w/a" };
    await st.pull({ targets: [ctx] });
    process.stdout.write(JSON.stringify([st.stateOf(ctx).owedState ?? "idle", calls.some((c) => c.l.startsWith("pr:owed"))]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["idle",false]' ]
}

@test "store: pull serializes targets on one machine; other machines run in parallel" {
  _store '
    const inner = fake();
    const live = new Map();
    const maxBy = new Map();
    let liveAll = 0, maxAll = 0;
    const exec = async (cmd, args, opts = {}) => {
      const m = opts.on == null || opts.on === "" ? "" : String(opts.on);
      liveAll += 1; maxAll = Math.max(maxAll, liveAll);
      live.set(m, (live.get(m) ?? 0) + 1);
      maxBy.set(m, Math.max(maxBy.get(m) ?? 0, live.get(m)));
      await new Promise((r) => setTimeout(r, 15));
      try { return await inner(cmd, args, opts); }
      finally { live.set(m, live.get(m) - 1); liveAll -= 1; }
    };
    const st = s.createBoardStore({ exec });
    await st.pull({ targets: [
      { on: "m", cwd: "/w/a" }, { on: "m", cwd: "/w/b" }, { on: "n", cwd: "/w/c" },
    ] });
    process.stdout.write(JSON.stringify({ maxAll, maxM: maxBy.get("m"), maxN: maxBy.get("n") }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"maxAll":2,"maxM":1,"maxN":1}' ]
}

@test "store: a queued listing-only fall-through is at most once per FULL_PULL_MS per target" {
  _store '
    let t = 1000;
    const queued = (slug) => docAs(slug, "listing", { repos: [{ ...doc.repos[0], slug, phase: "queued" }] });
    const st = s.createBoardStore({
      exec: fake((l) => (l.includes(":offline") ? queued("acme/box") : null)),
      now: () => t,
    });
    const ctx = { on: null, cwd: "/w/a" };
    await st.pull({ targets: [ctx], listingOnly: true });
    const first = calls.map((c) => c.l); calls.length = 0;
    t = 180999; await st.pull({ targets: [ctx], listingOnly: true });
    const mid = calls.map((c) => c.l); calls.length = 0;
    t = 181000; await st.pull({ targets: [ctx], listingOnly: true });
    process.stdout.write(JSON.stringify({ first, mid, late: calls.map((c) => c.l) }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"first":["tools","hc","pr:listing:offline","pr:listing","pr:status"],"mid":["hc","pr:listing:offline"],"late":["hc","pr:listing:offline","pr:listing:stamp","pr:status:stamp"]}' ]
}

@test "store: keep() prunes the fall-through stamp so a dropped target can fall through again" {
  _store '
    let t = 1000;
    const queued = (slug) => docAs(slug, "listing", { repos: [{ ...doc.repos[0], slug, phase: "queued" }] });
    const st = s.createBoardStore({
      exec: fake((l) => (l.includes(":offline") ? queued("acme/box") : null)),
      now: () => t,
    });
    const ctx = { on: null, cwd: "/w/a" };
    await st.pull({ targets: [ctx], listingOnly: true });
    await st.pull({ targets: [ctx], keep: () => [] });
    calls.length = 0;
    t = 2000; await st.pull({ targets: [ctx], listingOnly: true });
    process.stdout.write(JSON.stringify(calls.map((c) => c.l)));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["hc","pr:listing:offline","pr:listing","pr:status"]' ]
}
