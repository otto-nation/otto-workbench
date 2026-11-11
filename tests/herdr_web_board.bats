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

BOARD_TABLE='const PULL = JSON.stringify([{ number: 3, title: "Fix it", user: { login: "bob" }, head: { ref: "feat", sha: "abc1234" },
  base: { ref: "main" }, draft: false, html_url: "https://github.com/acme/box/pull/3", labels: [], created_at: "c", updated_at: "u" }]);
const GQL = { data: { repository: { pr0: { isDraft: false, reviewDecision: null, mergeable: "MERGEABLE", additions: 1, deletions: 2,
  changedFiles: 3, mergeQueueEntry: null, commits: { nodes: [] }, latestReviews: { nodes: [] }, reviewRequests: { nodes: [] } } } } };
const LISTING = { schema_version: 1, reviews_root: "/r", reviews: [] };
const table = (terminals) => [
  { prefix: "hc ls --json --label", stdout: { terminals: [] } },
  { prefix: "hc ls --json", stdout: { terminals } },
  { prefix: "sh -c command -v", stdout: "/b/pr\n/b/gh\n/b/wt\n" },
  { prefix: "git remote get-url origin", stdout: "git@github.com:acme/box.git\n" },
  { prefix: "gh api /user", stdout: "ada\n" },
  { prefix: "gh api /repos/acme/box/pulls", stdout: PULL + "\n" },
  { prefix: "gh api graphql", stdout: GQL },
  { prefix: "pr review --list --schema-version 1", stdout: LISTING },
  { prefix: "pr batch status", code: 1, stdout: "", stderr: "pr batch: no batch runs yet" },
];'

@test "board: a terminal in a GitHub checkout yields that repo's PRs, without server paths" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const b = createBoard({ exec: fakeExec(table([{ address: 't1', cwd: '/w/box', state: 'live' }])), name: 'srv', version: 'v1', now: () => 5 });
        await b.refresh();
        const s = b.snapshot();
        out({ me: s.me, slugs: s.repoSlugs, n: s.prs.length, pr: s.prs[0].repoSlug + '#' + s.prs[0].number, cwd: 'cwd' in s.prs[0],
              checkout: s.prs[0].hasCheckout, errors: s.errors, loading: s.loading, at: s.updatedAt, version: s.version,
              rec: b.findPr('acme/box', 3).cwd, root: b.reviewsRootFor(null) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"me":"ada","slugs":["acme/box"],"n":1,"pr":"acme/box#3","cwd":false,"checkout":true,"errors":[],"loading":false,"at":5,"version":"v1","rec":"/w/box","root":"/r"}' ]
}

@test "board: snapshot listing rows keep review file paths off the page" {
  _web "$BOARD_TABLE
        const listing = {
          schema_version: 1, reviews_root: '/secret/reviews',
          reviews: [{
            repo: 'acme/box', pr_number: 3, mode: 'pr', stamp: 's1',
            review_file: '/secret/review.md', head_sha: 'abc1234', status: 'complete', reviewed_at: 'u',
            history: [{ stamp: 's0', review_file: '/secret/old.md', head_sha: 'old', status: 'complete', reviewed_at: 't' }],
          }],
        };
        const rows = table([{ address: 't1', cwd: '/w/box', state: 'live' }]);
        rows.find((r) => r.prefix.startsWith('pr review')).stdout = listing;
        const { createBoard } = await import(W + '/server/board.js');
        const b = createBoard({ exec: fakeExec(rows), name: 'srv', version: 'v1', now: () => 5 });
        await b.refresh();
        const s = b.snapshot();
        out({ leak: JSON.stringify(s).includes('/secret'), stamps: (s.prs[0].reviewRuns || []).map((r) => r.run && r.run.stamp) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"leak":false,"stamps":["s1","s0"]}' ]
}

@test "board: a terminal outside any repo is skipped, not an error" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const rows = table([{ address: 't1', cwd: '/home/a', state: 'live' }]);
        rows.unshift({ prefix: 'git remote get-url origin', code: 128, stderr: 'fatal: not a git repository (or any of the parent directories): .git' });
        const b = createBoard({ exec: fakeExec(rows), name: 'srv', version: 'v1' });
        await b.refresh();
        const s = b.snapshot();
        out({ skipped: s.skipped, errors: s.errors.length, prs: s.prs.length });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"skipped":1,"errors":0,"prs":0}' ]
}

@test "board: two terminals in one repo fetch its PRs once after the first pull; listing-only skips gh pulls" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const b = createBoard({ exec: fakeExec(table([{ address: 't1', cwd: '/w/box' }, { address: 't2', cwd: '/w/box/sub' }])), name: 'srv', version: 'v1' });
        const pulls = () => calls.filter((c) => c.cmd === 'gh' && c.args[1] === '/repos/acme/box/pulls').length;
        await b.refresh(); const first = pulls();
        await b.refresh(); const second = pulls() - first;
        await b.refresh({ listingOnly: true }); const third = pulls() - first - second;
        out([first, second, third, b.snapshot().prs.length]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[2,1,0,1]' ]
}

@test "board: each repo is published as it loads, before the slowest repo finishes" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const inner = fakeExec(table([{ address: 't1', cwd: '/w/box' }, { address: 't2', cwd: '/w/kit' }]));
        let release;
        const gate = new Promise((r) => { release = r; });
        const exec = async (cmd, args, opts = {}) => {
          if (cmd === 'git' && opts.cwd === '/w/kit') return { code: 0, stdout: 'git@github.com:acme/kit.git\\n', stderr: '' };
          if (cmd === 'gh' && args.includes('/repos/acme/kit/pulls')) { await gate; return { code: 0, stdout: '[]\\n', stderr: '' }; }
          return inner(cmd, args, opts);
        };
        const seen = [];
        const b = createBoard({ exec, name: 'srv', version: 'v1', onChange: (s) => seen.push(s) });
        const done = b.refresh();
        let early = null;
        for (let i = 0; i < 200 && !early; i++) {
          early = seen.find((s) => s.prs.length === 1);
          if (!early) await new Promise((r) => setTimeout(r, 5));
        }
        release();
        await done;
        out({ early: [early.refreshing, early.loading, early.prs[0].repoSlug], last: seen.at(-1).refreshing });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"early":[true,true,"acme/box"],"last":false}' ]
}

@test "board: refreshes asked for during a pull collapse into one more pull" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        const b = createBoard({ exec: fakeExec(table([{ address: 't1', cwd: '/w/box' }])), name: 'srv', version: 'v1' });
        const p = b.refresh(); b.refresh(); b.refresh({ listingOnly: true }); b.refresh();
        await p; await new Promise((r) => setTimeout(r, 50)); while (b.snapshot().refreshing) await new Promise((r) => setTimeout(r, 20));
        out(calls.filter((c) => c.cmd === 'hc' && c.args.length === 2).length);"
  [ "$status" -eq 0 ]
  [ "$output" = '2' ]
}

@test "board: an unreachable herdr-core keeps the last targets and reports it" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        let rows = table([{ address: 't1', cwd: '/w/box' }]);
        const exec = (cmd, args, o) => fakeExec(rows)(cmd, args, o);
        const b = createBoard({ exec, name: 'srv', version: 'v1' });
        await b.refresh();
        rows = [{ prefix: 'hc ls --json', code: 1, stderr: 'core not running' }, ...rows.slice(2)];
        await b.refresh();
        const s = b.snapshot();
        out({ prs: s.prs.length, err: s.errors[0].message });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"prs":1,"err":"herdr-core: core not running"}' ]
}

@test "board: a failed refresh over loaded data keeps the PRs and reports when they last loaded (stale, not failed)" {
  _web "$BOARD_TABLE
        const { createBoard } = await import(W + '/server/board.js');
        let rows = table([{ address: 't1', cwd: '/w/box' }]);
        let t = 5;
        const b = createBoard({ exec: (cmd, args, o) => fakeExec(rows)(cmd, args, o), name: 'srv', version: 'v1', now: () => t });
        await b.refresh();
        t = 9;
        rows = [{ prefix: 'gh api /repos/acme/box/pulls', code: 1, stderr: 'boom' }, ...rows];
        await b.refresh();
        const s = b.snapshot();
        out({ prs: s.prs.length, n: s.errors.length, at: s.errors[0].updatedAt });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"prs":1,"n":1,"at":5}' ]
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
        out({ asked, intervals: ticks.map(([, ms]) => ms) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"asked":["full","listing","full","listing"],"intervals":[180000,15000]}' ]
}
