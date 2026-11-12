#!/usr/bin/env bats
# Tests for ai/herdr/web/server/board.js — pulls, snapshot, and poller (spec § 5.3, § 5.6).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

BOARD_TABLE='const fs = await import("node:fs");
const DOC = JSON.parse(fs.readFileSync(W + "/../../../tests/fixtures/board_document.json", "utf8"));
const docAs = (slug, phase, extra = {}) => ({ ...DOC, phase, stamp: "st-" + phase, repos: [{ ...DOC.repos[0], slug }], ...extra });
const table = (terminals) => [
  { prefix: "hc ls --json --label", stdout: { terminals: [] } },
  { prefix: "hc ls --json", stdout: { terminals } },
  { prefix: "sh -c command -v", stdout: "/b/pr\n/b/gh\n/b/wt\n" },
];
const phaseOf = (args) => args[args.indexOf("--phase") + 1];
// answer(phase, opts) may return a whole exec result ({code, …}) or document fields to lay over the default.
const withPr = (rows, slugOf = () => "acme/box", extra = {}, answer = () => null) => async (cmd, args, opts = {}) => {
  if (cmd === "pr" && args[0] === "board") {
    calls.push({ cmd, args: [...args], cwd: opts.cwd ?? null, on: opts.on ?? null, timeout: opts.timeout ?? null });
    const over = await answer(phaseOf(args), opts);
    if (over && "code" in over) return over;
    return { code: 0, stdout: JSON.stringify({ ...docAs(slugOf(opts.cwd), phaseOf(args), extra), ...(over ?? {}) }), stderr: "" };
  }
  return fakeExec(rows)(cmd, args, opts);
};'

@test "board: a terminal in a GitHub checkout yields that repo's PRs from pr board, without server paths; no page on Mine reads no owed" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const b = createBoard({ exec: withPr(table([{ address: 't1', cwd: '/w/box', state: 'live' }]), undefined, { reviews_root: '/r' }), name: 'srv', version: 'v1', now: () => 5 });
        await b.refresh();
        const s = b.snapshot();
        out({ me: s.me, slugs: s.repoSlugs, n: s.prs.length, cwd: 'cwd' in s.prs[0], checkout: s.prs[0].hasCheckout,
              errors: s.errors, at: s.updatedAt, rec: b.findPr('acme/box', 7).cwd, root: b.reviewsRootFor(null),
              cols: s.columns.length, owed: s.repoStates[0].owed,
              phases: calls.filter((c) => c.cmd === 'pr').map((c) => phaseOf(c.args) + ':' + c.timeout) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"me":"me","slugs":["acme/box"],"n":4,"cwd":false,"checkout":true,"errors":[],"at":5,"rec":"/w/box","root":"/r","cols":15,"owed":{"state":"idle","error":null},"phases":["listing:60000","status:180000"]}' ]
}

@test "board: while a page wants owed it is read, then again only once a minute" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        let t = 1000;
        const b = createBoard({ exec: withPr(table([{ address: 't1', cwd: '/w/box', state: 'live' }])), name: 'srv', version: 'v1', now: () => t, want: () => ({ owed: true }) });
        const take = () => { const p = calls.filter((c) => c.cmd === 'pr').map((c) => ({ phase: phaseOf(c.args), offline: c.args.includes('--offline'), timeout: c.timeout })); calls.length = 0; return p; };
        await b.refresh(); const a = take();
        t = 1010; await b.refresh({ listingOnly: true }); const b1 = take();
        t = 70000; await b.refresh({ listingOnly: true }); const c = take();
        out({ steps: [a, b1, c].map((xs) => xs.map((x) => x.phase)), offline: [b1, c].map((xs) => xs.filter((x) => x.phase === 'listing').every((x) => x.offline)),
              owedTimeout: c.find((x) => x.phase === 'owed').timeout, owed: b.snapshot().repoStates[0].owed.state });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"steps":[["listing","status","owed"],["listing"],["listing","owed"]],"offline":[true,true],"owedTimeout":120000,"owed":"ready"}' ]
}

@test "board: owed follows want: none while unwanted, read when wanted, a late answer dropped" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        let showing = false, late = false, t = 1000;
        const exec = withPr(table([{ address: 't1', cwd: '/w/box', state: 'live' }]), undefined, {}, (phase) => (phase === 'owed' && late ? (showing = false, { complete: false, rows: [{ ...DOC.rows[1], lanes: { ...DOC.rows[1].lanes, mine: 'has-comments' } }] }) : null));
        const b = createBoard({ exec, name: 'srv', version: 'v1', now: () => t, want: () => ({ owed: showing }) });
        const take = () => { const p = calls.filter((c) => c.cmd === 'pr').map((c) => phaseOf(c.args)); calls.length = 0; return p; };
        await b.refresh(); const a = { phases: take(), owed: b.snapshot().repoStates[0].owed.state };
        showing = true; await b.refresh({ listingOnly: true }); const b1 = { phases: take(), owed: b.snapshot().repoStates[0].owed.state };
        late = true; t = 70000; await b.refresh({ listingOnly: true });
        const eight = b.snapshot().prs.find((p) => p.number === 8);
        const c = { phases: take(), mine: eight.lanes.mine, owed: b.snapshot().repoStates[0].owed.state };
        t = 140000; await b.refresh({ listingOnly: true }); const d = take();
        out({ a, b1, c, d });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"a":{"phases":["listing","status"],"owed":"idle"},"b1":{"phases":["listing","owed"],"owed":"ready"},"c":{"phases":["listing","owed"],"mine":"awaiting-review","owed":"ready"},"d":["listing"]}' ]
}

@test "board: a failed owed read is the repo's owed error, in words" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const exec = withPr(table([{ address: 't1', cwd: '/w/box', state: 'live' }]), undefined, {}, (phase) => (phase === 'owed' ? { code: 1, stdout: '', stderr: 'boom' } : null));
        const b = createBoard({ exec, name: 'srv', version: 'v1', want: () => ({ owed: true }) });
        await b.refresh();
        const s = b.snapshot();
        out({ owed: s.repoStates[0].owed, errors: s.errors, n: s.prs.length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"owed":{"state":"error","error":"boom"},"errors":[],"n":4}' ]
}

@test "board: Mine's Has Comments lane is drawn from the document, loading until owed lands" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const v = await import(W + '/lib/view.js');
        const ui = { ...v.DEFAULT_UI, tab: 'mine' };
        const wanted = createBoard({ exec: withPr(table([{ address: 't1', cwd: '/w/box', state: 'live' }])), name: 'srv', version: 'v1', want: () => ({ owed: true }) });
        await wanted.refresh();
        const none = createBoard({ exec: withPr(table([{ address: 't1', cwd: '/w/box', state: 'live' }])), name: 'srv', version: 'v1' });
        await none.refresh();
        out({ want: v.boardModel(wanted.snapshot(), ui).columns.map((c) => [c.key, c.status]), none: v.boardModel(none.snapshot(), ui).columns.map((c) => [c.key, c.status]) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"want":[["has-comments","ready"]],"none":[["has-comments","loading"]]}' ]
}

@test "board: snapshot keeps review file paths and checkouts off the page" {
  _web "$BOARD_TABLE
        const extra = {
          reviews_root: '/secret/reviews',
          repos: [{ ...DOC.repos[0], slug: 'acme/box', checkout: '/secret/w' }],
          rows: DOC.rows.map((r, i) => i ? r : { ...r, review_runs: r.review_runs.map((run, j) => j ? run : { ...run, review_file: '/secret/r.md' }) }),
        };
        const { createBoard } = await import(W + '/server/board.js');
        const b = createBoard({ exec: withPr(table([{ address: 't1', cwd: '/w/box', state: 'live' }]), undefined, extra), name: 'srv', version: 'v1', now: () => 5 });
        await b.refresh();
        const s = b.snapshot();
        out({ leak: JSON.stringify(s).includes('/secret'), stamps: (s.prs[0].reviewRuns || []).map((r) => r.run && r.run.stamp) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"leak":false,"stamps":["20260102T000000","20260101T000000"]}' ]
}

@test "board: a terminal outside any repo is skipped, not an error" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const exec = withPr(table([{ address: 't1', cwd: '/home/a', state: 'live' }]), undefined, {}, () => ({ repos: [{ slug: null, phase: 'failed', error: { kind: 'not-git', message: 'x' } }], rows: [] }));
        const b = createBoard({ exec, name: 'srv', version: 'v1' });
        await b.refresh();
        const s = b.snapshot();
        out({ skipped: s.skipped, errors: s.errors.length, prs: s.prs.length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"skipped":1,"errors":0,"prs":0}' ]
}

@test "board: two terminals in one repo pull it once after the first pull; listing-only reads no status" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const b = createBoard({ exec: withPr(table([{ address: 't1', cwd: '/w/box' }, { address: 't2', cwd: '/w/box/sub' }])), name: 'srv', version: 'v1' });
        const statusN = () => calls.filter((c) => c.cmd === 'pr' && phaseOf(c.args) === 'status').length;
        await b.refresh(); const first = statusN();
        await b.refresh(); const second = statusN() - first;
        await b.refresh({ listingOnly: true }); const third = statusN() - first - second;
        out([first, second, third, b.snapshot().repoSlugs.length]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[2,1,0,1]' ]
}

@test "board: each repo is published as it loads, before the slowest repo finishes" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        let release;
        const gate = new Promise((r) => { release = r; });
        const slugOf = (cwd) => (cwd && String(cwd).includes('kit') ? 'acme/kit' : 'acme/box');
        const exec = withPr(table([{ address: 't1', cwd: '/w/box' }, { address: 't2', cwd: '/w/kit' }]), slugOf, {}, async (phase, opts) => {
          if (phase === 'listing' && opts.cwd === '/w/kit') await gate;
          return null;
        });
        const seen = [];
        const b = createBoard({ exec, name: 'srv', version: 'v1', onChange: (s) => seen.push(s) });
        const done = b.refresh();
        let early = null;
        for (let i = 0; i < 200 && !early; i++) {
          early = seen.find((s) => (s.repoStates ?? []).some((r) => r.folder === 'box' && r.phase === 'loaded')
            && (s.repoStates ?? []).some((r) => r.folder === 'kit' && r.phase === 'loading'));
          if (!early) await new Promise((r) => setTimeout(r, 5));
        }
        release();
        await done;
        const box = early.repoStates.find((r) => r.folder === 'box');
        const kit = early.repoStates.find((r) => r.folder === 'kit');
        out({ early: [early.refreshing, box.phase, kit.phase], last: seen.at(-1).refreshing });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"early":[true,"loaded","loading"],"last":false}' ]
}

@test "board: refreshes asked for during a pull collapse into one more pull" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const b = createBoard({ exec: withPr(table([{ address: 't1', cwd: '/w/box' }])), name: 'srv', version: 'v1' });
        const p = b.refresh(); b.refresh(); b.refresh({ listingOnly: true }); b.refresh();
        await p; await new Promise((r) => setTimeout(r, 50)); while (b.snapshot().refreshing) await new Promise((r) => setTimeout(r, 20));
        out(calls.filter((c) => c.cmd === 'hc' && c.args.length === 2).length);"
  [ "$status" -eq 0 ]
  [ "$output" = '2' ]
}

@test "board: an unreachable herdr-core keeps the last targets and reports it" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        let failHc = false;
        const inner = withPr(table([{ address: 't1', cwd: '/w/box' }]));
        const exec = async (cmd, args, o) => {
          if (failHc && cmd === 'hc' && args.length === 2) return { code: 1, stdout: '', stderr: 'core not running' };
          return inner(cmd, args, o);
        };
        const b = createBoard({ exec, name: 'srv', version: 'v1' });
        await b.refresh();
        failHc = true;
        await b.refresh();
        const s = b.snapshot();
        out({ prs: s.prs.length, err: s.errors[0].message });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"prs":4,"err":"herdr-core: core not running"}' ]
}

@test "board: a failed refresh over loaded data keeps the PRs and reports when they last loaded" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        let failPr = false;
        const inner = withPr(table([{ address: 't1', cwd: '/w/box' }]));
        let t = 5;
        const exec = async (cmd, args, o) => {
          if (failPr && cmd === 'pr') return { code: 1, stdout: '', stderr: 'boom' };
          return inner(cmd, args, o);
        };
        const b = createBoard({ exec, name: 'srv', version: 'v1', now: () => t });
        await b.refresh();
        t = 9;
        failPr = true;
        await b.refresh();
        const s = b.snapshot();
        out({ prs: s.prs.length, n: s.errors.length, at: s.errors[0].updatedAt, phase: s.repoStates[0].phase });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"prs":4,"n":1,"at":5,"phase":"stale"}' ]
}

@test "board: a gh partial is the document's partial" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const extra = { repos: [{ ...DOC.repos[0], slug: 'acme/box', partial: true }] };
        const b = createBoard({ exec: withPr(table([{ address: 't1', cwd: '/w/box' }]), undefined, extra), name: 'srv', version: 'v1' });
        await b.refresh();
        out(b.snapshot().repoStates[0].partial);"
  [ "$status" -eq 0 ]
  [ "$output" = 'true' ]
}

@test "poller: wakes a never-pulled board fully, a recent one not at all, and polls only with a page connected" {
  _web "const { createPoller } = await import(W + '/server/board.js');
        const asked = []; let at = null; let pages = 0; const ticks = [];
        const board = { refresh: (o = {}) => { asked.push(o.listingOnly ? 'listing' : 'full'); return Promise.resolve(); }, pulledAt: () => at };
        let t = 1000000;
        const p = createPoller({ board, hub: { count: () => pages }, fullMs: 180000, listingMs: 15000, now: () => t,
          every: (fn, ms) => { ticks.push([fn, ms]); return ticks.length; }, stopEvery: () => {} });
        p.start(); p.wake();
        at = t; t += 1000; p.wake();
        t += 20000; p.wake();
        for (const [fn] of ticks) fn();
        pages = 1; for (const [fn] of ticks) fn();
        const base = [...asked];
        at = t; p.wake(true); const recent = asked.slice(base.length);
        at = null; p.wake(true); const never = asked.slice(base.length + recent.length);
        out({ asked: base, intervals: ticks.map(([, ms]) => ms), recent, never });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"asked":["full","listing","full","listing"],"intervals":[180000,15000],"recent":["listing"],"never":["full"]}' ]
}
