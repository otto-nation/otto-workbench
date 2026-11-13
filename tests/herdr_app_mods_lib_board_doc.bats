#!/usr/bin/env bats
# The mod reads `pr board`: the document's records, the pull sequence, the outdated-pr error.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js ai/herdr/app/mods/prs/board_view.js tests/fixtures/board_document.json

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  LIB="$REPO_ROOT/ai/herdr/app/mods/_lib"
  FIX="$REPO_ROOT/tests/fixtures/board_document.json"
}

teardown() {
  common_teardown
}

# $1: a script body; `doc`, `d` (board_doc), `p` (board_pull), `b` (board_data) are in scope.
_js() {
  run node --input-type=module -e "
    import fs from 'node:fs';
    const d = await import('$LIB/board_doc.js');
    const p = await import('$LIB/board_pull.js');
    const b = await import('$LIB/board_data.js');
    const doc = JSON.parse(fs.readFileSync('$FIX', 'utf8'));
    $1
  "
}

@test "boardArgv carries phase, prs, stamp and offline in pr board's flags" {
  _js '
    process.stdout.write(JSON.stringify([
      d.boardArgv("/w", "listing"),
      d.boardArgv("/w", "status", { prs: [3, 4], stamp: "s1" }),
      d.boardArgv("/w", "listing", { offline: true }),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["board","--schema-version","1","--checkout","/w","--phase","listing"],["board","--schema-version","1","--checkout","/w","--phase","status","--prs","3,4","--if-changed","s1"],["board","--schema-version","1","--checkout","/w","--phase","listing","--offline"]]' ]
}

@test "rowToPr renames the wire fields and passes lanes, issues and presentation through" {
  _js '
    const pr = d.rowToPr(doc.rows[0]);
    process.stdout.write(JSON.stringify({
      repoSlug: pr.repoSlug, base: pr.baseBranch, sha: pr.headSha, mine: pr.isMine,
      lanes: pr.lanes === doc.rows[0].lanes, pres: pr.presentation === doc.rows[0].presentation,
      run: pr.reviewRuns[0].run.stamp, posted: pr.reviewRuns[0].postedEvent,
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"repoSlug":"acme/box","base":"main","sha":"aaa","mine":true,"lanes":true,"pres":true,"run":"20260102T000000","posted":"comment"}' ]
}

@test "mergeRows: complete replaces; a patch updates by repo and number and appends new rows" {
  _js '
    const a = [{ repoSlug: "o/n", number: 1, v: "old" }, { repoSlug: "o/n", number: 2, v: "keep" }];
    const rows = [{ repoSlug: "o/n", number: 1, v: "new" }, { repoSlug: "o/n", number: 3, v: "add" }];
    process.stdout.write(JSON.stringify([
      d.mergeRows(a, rows, true).map((r) => r.v), d.mergeRows(a, rows, false).map((r) => r.v),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["new","add"],["new","keep","add"]]' ]
}

@test "docToState: a failed repo keeps the rows, clears the phase and carries the error" {
  _js '
    const ok = d.docToState(doc, { cwd: "/w", on: null });
    const bad = d.docToState({ ...doc, repos: [{ slug: "acme/box", phase: "failed", error: { kind: "auth", message: "x" } }] }, { cwd: "/w", on: null }, ok);
    process.stdout.write(JSON.stringify([ok.phase, ok.rows.length, bad.phase ?? null, bad.error.kind, bad.rows.length, ok.reviewsRoot]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["loaded",4,null,"auth",4,""]' ]
}

@test "docToState: features from the doc, kept when absent, empty when neither has them" {
  _js '
    const withFeat = d.docToState({ ...doc, features: ["review-publish"] }, { cwd: "/w", on: null });
    const kept = d.docToState({ ...doc, features: undefined }, { cwd: "/w", on: null }, withFeat);
    const none = d.docToState({ ...doc, features: undefined }, { cwd: "/w", on: null });
    process.stdout.write(JSON.stringify([withFeat.features, kept.features, none.features]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["review-publish"],["review-publish"],[]]' ]
}

@test "withRunning marks the PRs a live review terminal is running for" {
  _js '
    const prs = d.withRunning(doc.rows.map(d.rowToPr), new Map([[7, { terminal: "t9" }]]));
    process.stdout.write(JSON.stringify(prs.map((p) => [p.number, p.reviewing, p.reviewTerminal])));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[[7,true,"t9"],[8,false,null],[10,false,null],[9,false,null]]' ]
}

@test "loadBoard: an old pr that lacks board, this schema or a flag is outdated, with the pr's words" {
  _js '
    const run = async (stderr) => (await b.loadBoard(async () => ({ code: 2, stdout: "", stderr }), { cwd: "/w", on: null }, "listing")).error.kind;
    process.stdout.write(JSON.stringify([
      await run("pr: error: argument command: invalid choice: '"'"'board'"'"'"),
      await run("✗ pr: unsupported --schema-version '"'"'9'"'"' — this build serves 1"),
      await run("pr board: error: unrecognized arguments: --offline"),
      await run("pr: unrecognized option '"'"'--x'"'"'"),
    ]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["outdated","outdated","outdated","failed"]' ]
}

@test "loadBoard: asks for the phase with its timeout, and refuses a document that is not that phase" {
  _js '
    const calls = [];
    const exec = async (cmd, args, o) => { calls.push([args.slice(0, 6).join(" "), o.timeout]); return { code: 0, stdout: JSON.stringify(doc), stderr: "" }; };
    const ok = await b.loadBoard(exec, { cwd: "/w", on: null }, "status");
    const wrong = await b.loadBoard(exec, { cwd: "/w", on: null }, "listing");
    const nopres = await b.loadBoard(async () => ({ code: 0, stdout: JSON.stringify({ ...doc, rows: [{ number: 1 }] }), stderr: "" }), { cwd: "/w", on: null }, "status");
    process.stdout.write(JSON.stringify([ok.ok, calls[0], wrong.error.kind, nopres.error.kind]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,["board --schema-version 1 --checkout /w --phase",180000],"outdated","outdated"]' ]
}

# A fake pr: answers each phase from the fixture, recording the phase and flags.
_pull() {
  _js "
    const calls = [];
    const exec = async (cmd, args) => {
      if (cmd === 'hc') return { code: 0, stdout: '[]', stderr: '' };
      const phase = args[args.indexOf('--phase') + 1];
      calls.push(phase + (args.includes('--offline') ? ':offline' : '') + (args.includes('--if-changed') ? ':stamp' : ''));
      const answer = globalThis.answer?.(phase, args);
      return { code: 0, stdout: JSON.stringify(answer ?? { ...doc, phase, stamp: 'st-' + phase }), stderr: '' };
    };
    const seen = [];
    const publish = (s) => seen.push([s.phase ?? null, (s.prs ?? []).length, s.error?.kind ?? null]);
    $1
  "
}

@test "pullTarget: listing then status then owed, each published as it lands" {
  _pull '
    const r = await p.pullTarget(exec, { cwd: "/w", on: null }, null, { owed: true, now: 1000 }, publish);
    process.stdout.write(JSON.stringify([r.ok, calls, seen.map((s) => s[0])]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,["listing","status","owed"],["hydrating","loaded","loaded"]]' ]
}

@test "pullTarget: owed is skipped when Mine is not shown or the last read is fresh" {
  _pull '
    await p.pullTarget(exec, { cwd: "/w", on: null }, null, { owed: false, now: 1000 }, publish);
    const a = [...calls]; calls.length = 0;
    const prev = { repo: { slug: "acme/box" }, rows: [], owedAt: 990, stamps: {} };
    await p.pullTarget(exec, { cwd: "/w", on: null }, prev, { owed: true, now: 1000 }, publish);
    process.stdout.write(JSON.stringify([a, calls]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["listing","status"],["listing","status"]]' ]
}

@test "pullTarget: a listing-only pull reads the cache offline and sends no stamp for it the first time" {
  _pull '
    const prev = { repo: { slug: "acme/box", host: "github.com" }, rows: [], stamps: {} };
    const r = await p.pullTarget(exec, { cwd: "/w", on: null }, prev, { listingOnly: true, now: 1000 }, publish);
    process.stdout.write(JSON.stringify([r.ok, calls]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,["listing:offline"]]' ]
}

@test "pullTarget: an offline listing answered queued (nothing cached) falls through to a full pull" {
  _pull '
    globalThis.answer = (phase, args) => args.includes("--offline")
      ? { ...doc, phase, stamp: "o", rows: [], repos: [{ ...doc.repos[0], phase: "queued" }] } : null;
    const prev = { repo: { slug: "acme/box", host: "github.com" }, rows: [], stamps: {} };
    await p.pullTarget(exec, { cwd: "/w", on: null }, prev, { listingOnly: true, now: 1000 }, publish);
    process.stdout.write(JSON.stringify(calls));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '["listing:offline","listing","status"]' ]
}

@test "pullTarget: an unchanged answer keeps the rows and a fresh pull sends no stamps" {
  _pull '
    globalThis.answer = (phase, args) => args.includes("--if-changed")
      ? { schema_version: 1, phase, stamp: "x", unchanged: true } : null;
    const first = [];
    await p.pullTarget(exec, { cwd: "/w", on: null }, null, { now: 1 }, (s) => first.push(s));
    const prev = first.at(-1);
    calls.length = 0;
    await p.pullTarget(exec, { cwd: "/w", on: null }, prev, { now: 2 }, publish);
    const stamped = [...calls]; calls.length = 0;
    await p.pullTarget(exec, { cwd: "/w", on: null }, prev, { now: 3, fresh: true }, publish);
    process.stdout.write(JSON.stringify([stamped, calls, seen.map((s) => s[1])]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["listing:stamp","status:stamp"],["listing","status"],[4,4,4,4]]' ]
}

@test "pullTarget: a repo the document reports failed is published with its error and ends the pull" {
  _pull '
    globalThis.answer = (phase) => ({ ...doc, phase, stamp: "f", repos: [{ slug: "acme/box", phase: "failed", error: { kind: "not-github", message: "m" } }] });
    const r = await p.pullTarget(exec, { cwd: "/w", on: null }, null, { now: 1 }, publish);
    process.stdout.write(JSON.stringify([r.ok, calls, seen]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,["listing"],[[null,0,"not-github"]]]' ]
}

@test "pullTarget: an exec failure returns the error and publishes nothing" {
  _pull '
    const bad = async () => ({ code: 2, stdout: "", stderr: "pr: error: argument command: invalid choice: '"'"'board'"'"'" });
    const r = await p.pullTarget(bad, { cwd: "/w", on: "m1" }, null, { now: 1 }, publish);
    process.stdout.write(JSON.stringify([r.ok, r.error.kind, seen.length]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[false,"outdated",0]' ]
}

@test "pullTarget: owed may be a getter; false when owed would start skips the read" {
  _pull '
    let want = false;
    const r = await p.pullTarget(exec, { cwd: "/w", on: null }, null, { owed: () => want, now: 1000 }, publish);
    process.stdout.write(JSON.stringify([r, calls]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":true},["listing","status"]]' ]
}

@test "pullTarget: an owed answer that lands after the getter turned false is dropped, not published" {
  _pull '
    let want = true;
    globalThis.answer = (phase) => { if (phase === "owed") want = false; return null; };
    const r = await p.pullTarget(exec, { cwd: "/w", on: null }, null, { owed: () => want, now: 1000 }, publish);
    process.stdout.write(JSON.stringify([r, calls, seen.length]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":true,"owed":"dropped"},["listing","status","owed"],2]' ]
}

@test "pullTarget: a failed owed read is reported with its error; the rows stay as status left them" {
  _pull '
    const fail = async (cmd, args) => (args.includes("owed") ? { code: 1, stdout: "", stderr: "boom" } : exec(cmd, args));
    const r = await p.pullTarget(fail, { cwd: "/w", on: null }, null, { owed: true, now: 1000 }, publish);
    process.stdout.write(JSON.stringify([r, seen.map((s) => s[0])]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[{"ok":true,"owed":"failed","owedError":{"kind":"failed","message":"boom"}},["hydrating","loaded"]]' ]
}

@test "pullTarget: a read owed says so" {
  _pull '
    const r = await p.pullTarget(exec, { cwd: "/w", on: null }, null, { owed: () => true, now: 1000 }, publish);
    process.stdout.write(JSON.stringify(r));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":true,"owed":"read"}' ]
}

@test "eachByMachine: one at a time per machine, groups in parallel, order kept" {
  _js '
    const items = [{ on: null, id: 1 }, { on: "", id: 2 }, { on: "m1", id: 3 }, { on: "m1", id: 4 }];
    let live = 0, maxAll = 0;
    const liveBy = new Map();
    const maxBy = new Map();
    const orderBy = new Map();
    const wave = (n) => {
      let c = 0, go;
      const started = new Promise((r) => { go = r; });
      const gates = [];
      return {
        started,
        arrive() {
          let release;
          const wait = new Promise((r) => { release = r; });
          gates.push(release);
          if (++c === n) go();
          return wait;
        },
        release() { for (const r of gates) r(); },
      };
    };
    const first = wave(2);
    const second = wave(2);
    let n = 0;
    const done = p.eachByMachine(items, (i) => i.on, async (item) => {
      const m = p.machineOf(item.on);
      live += 1;
      maxAll = Math.max(maxAll, live);
      liveBy.set(m, (liveBy.get(m) ?? 0) + 1);
      maxBy.set(m, Math.max(maxBy.get(m) ?? 0, liveBy.get(m)));
      const gate = n++ < 2 ? first : second;
      await gate.arrive();
      live -= 1;
      liveBy.set(m, liveBy.get(m) - 1);
      const seq = orderBy.get(m) ?? [];
      seq.push(item.id);
      orderBy.set(m, seq);
    });
    await first.started;
    first.release();
    await second.started;
    second.release();
    await done;
    process.stdout.write(JSON.stringify({
      maxAll, maxBy: [...maxBy.entries()].sort(), order: [...orderBy.entries()].sort(),
    }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"maxAll":2,"maxBy":[["",1],["m1",1]],"order":[["",[1,2]],["m1",[3,4]]]}' ]
}

@test "eachByMachine: a rejection does not stop the rest of its machine and is rethrown after" {
  _js '
    const seen = [];
    let err = null;
    try {
      await p.eachByMachine(
        [{ on: "m", id: 1 }, { on: "m", id: 2 }, { on: "m", id: 3 }],
        (i) => i.on,
        async (item) => {
          seen.push(item.id);
          if (item.id === 1) throw new Error("boom");
        },
      );
    } catch (e) { err = e.message; }
    process.stdout.write(JSON.stringify([seen, err]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[[1,2,3],"boom"]' ]
}

@test "machineOf: null, undefined and empty string are the empty key" {
  _js 'process.stdout.write(JSON.stringify([p.machineOf(null), p.machineOf(undefined), p.machineOf(""), p.machineOf("m1")]));'
  [ "$status" -eq 0 ]
  [ "$output" = '["","","","m1"]' ]
}

@test "board_store pull groups targets by machine instead of Promise.all" {
  grep -q 'eachByMachine(targets' "$REPO_ROOT/ai/herdr/app/mods/_lib/board_store.js"
  run grep -q 'Promise.all(targets' "$REPO_ROOT/ai/herdr/app/mods/_lib/board_store.js"
  [ "$status" -eq 1 ]
}

@test "pullTarget: a queued offline answer falls through at most once per 180s" {
  _pull '
    globalThis.answer = (phase, args) => args.includes("--offline")
      ? { ...doc, phase, stamp: "o", rows: [], repos: [{ ...doc.repos[0], phase: "queued" }] } : null;
    const prev = { repo: { slug: "acme/box", host: "github.com" }, rows: [], stamps: {} };
    const falls = [];
    await p.pullTarget(exec, { cwd: "/w", on: null }, prev, { listingOnly: true, now: 1000, onFall: (t) => falls.push(t) }, publish);
    const first = [...calls]; calls.length = 0; seen.length = 0;
    await p.pullTarget(exec, { cwd: "/w", on: null }, prev, { listingOnly: true, now: 180999, fellAt: 1000, onFall: (t) => falls.push(t) }, publish);
    const mid = [...calls]; const pub = seen.length; calls.length = 0; seen.length = 0;
    await p.pullTarget(exec, { cwd: "/w", on: null }, prev, { listingOnly: true, now: 181000, fellAt: 1000, onFall: (t) => falls.push(t) }, publish);
    process.stdout.write(JSON.stringify([first, mid, calls, pub, falls]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["listing:offline","listing","status"],["listing:offline"],["listing:offline","listing","status"],0,[1000,181000]]' ]
}

@test "pullTarget: a never-loaded listing-only pull publishes a loaded offline answer" {
  _pull '
    const r = await p.pullTarget(exec, { cwd: "/w", on: null }, null, { listingOnly: true, now: 1000 }, publish);
    process.stdout.write(JSON.stringify([r.ok, calls, seen.length]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[true,["listing:offline"],1]' ]
}

@test "pullTarget: a never-loaded queued target falls through once, then publishes while throttled" {
  _pull '
    globalThis.answer = (phase, args) => args.includes("--offline")
      ? { ...doc, phase, stamp: "o", rows: [], repos: [{ ...doc.repos[0], phase: "queued" }] } : null;
    const falls = [];
    await p.pullTarget(exec, { cwd: "/w", on: null }, null, { listingOnly: true, now: 1000, onFall: (t) => falls.push(t) }, publish);
    const first = [...calls]; calls.length = 0;
    const n1 = seen.length;
    await p.pullTarget(exec, { cwd: "/w", on: null }, null, { listingOnly: true, now: 2000, fellAt: 1000, onFall: (t) => falls.push(t) }, publish);
    process.stdout.write(JSON.stringify([first, calls, seen.slice(n1), falls]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["listing:offline","listing","status"],["listing:offline"],[["queued",0,null]],[1000]]' ]
}

@test "scope: document error kinds are skippable in All spaces and read as words" {
  run node --input-type=module -e "
    const s = await import('$LIB/scope.js');
    process.stdout.write(JSON.stringify([
      s.skippable({ kind: 'no-origin' }), s.skippable({ kind: 'not-git' }), s.skippable({ kind: 'auth' }),
      s.friendlyError({ kind: 'outdated' }, 'box', 'all'),
      s.friendlyError({ kind: 'not-git' }, null, 'this'),
      s.friendlyError({ kind: 'not-github' }, null, 'all'),
      s.friendlyError({ kind: 'gh-failed', message: 'boom' }, null, 'all'),
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,false,"the workbench `pr` on box does not match this board (run otto-workbench sync there)","this space is not in a git repository; open a space in a repo or use All spaces","origin is not a GitHub remote","boom"]' ]
}

@test "lanes: laneOf picks the reviewing variant" {
  run node --input-type=module -e "
    const c = await import('$LIB/columns.js');
    const pr = { lanes: { team: 'reviewed', mine: 'has-comments', team_reviewing: 'in-queue', mine_reviewing: 'in-review' }, isMine: true };
    process.stdout.write(JSON.stringify([
      c.laneOf(pr, 'team'), c.laneOf({ ...pr, reviewing: true }, 'mine'),
      c.isMine(pr, 'someone'), c.columnsFor([{ board: 'team', key: 'b', order: 1 }, { board: 'mine', key: 'x', order: 0 }, { board: 'team', key: 'a', order: 0 }], 'team').map((x) => x.key),
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '["reviewed","in-review",true,["a","b"]]' ]
}

@test "presentationOf: a document row draws as the document says; a live review or batch item re-splices the live slot" {
  run node --input-type=module -e "
    import fs from 'node:fs';
    const m = await import('$LIB/pr_status.js');
    const d = await import('$LIB/board_doc.js');
    const doc = JSON.parse(fs.readFileSync('$FIX', 'utf8'));
    const pr = d.rowToPr(doc.rows[0]);
    const same = m.prPresentation(pr) === pr.presentation;
    const live = m.prPresentation({ ...pr, reviewing: true });
    const n = pr.presentation.issues.length;
    process.stdout.write(JSON.stringify([same, live.review.text, live.badges[n].text, live.row_badges.map((b) => b.text), live.badges.length - pr.presentation.badges.length]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[true,"Review running","Review running",["2 unresolved threads","Review running"],1]' ]
}
