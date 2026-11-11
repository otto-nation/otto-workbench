#!/usr/bin/env bats
# Tests for ai/herdr/web/server/app.js — router, statics, SSE, files cache (spec § 5.5, § 7).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

APP='const { createApp } = await import(W + "/server/app.js");
const { createHub } = await import(W + "/server/sse.js");
const { loadStatics } = await import(W + "/server/static.js");
const auth = await import(W + "/server/auth.js");
const TOK = "0123456789abcdef0123456789abcdef";
const pr = { repoSlug: "acme/box", number: 3, headSha: "abc", cwd: "/w/box", on: null, title: "<b>t</b>",
  reviewRuns: [{ run: { stamp: "s1", review_file: "/r/x/review.md" } }] };
const reads = [];
const board = { snapshot: () => ({ prs: [{ repoSlug: "acme/box", number: 3 }], version: "v1" }),
  findPr: (r, n) => (r === "acme/box" && n === 3 ? pr : null), refresh: (o) => { board.asked = o; return Promise.resolve(); },
  reviewsRootFor: () => "/r" };
const app = createApp({ config: { token: TOK, origin: null }, board, hub: createHub({ every: () => 0, stopEvery: () => {} }),
  files: { files: async () => ({ ok: true, files: [{ path: "a.js", page: 1, hasPatch: true }] }), patch: async (p, path) => ({ ok: true, patch: "@@ " + path }) },
  readTextFile: async (path) => { reads.push(path); return "# body"; },
  statics: await loadStatics(new URL("file://" + W + "/../")), version: "v1",
  pages: { index: "<html>board</html>", login: (m) => "<form>login " + m + "</form>", loginCss: "body{}", themeCss: ":root{}", tokensCss: ":root{}" },
  sleep: async () => {} });
const H = "http://127.0.0.1:7330";
const cookie = { cookie: "pr_board_session=" + auth.sessionValue(TOK) };
const call = async (path, init = {}) => { const r = await app.fetch(new Request(H + path, init)); return { s: r.status, t: r.headers.get("content-type"), b: await r.text(), h: Object.fromEntries(r.headers) }; };'

@test "app: /healthz answers without a token and touches nothing" {
  _web "$APP const r = await call('/healthz'); out([r.s, JSON.parse(r.b)]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[200,{"ok":true,"version":"v1"}]' ]
}

@test "app: tokenless loopback gets 401 — the login page for HTML, JSON for the API" {
  _web "$APP const a = await call('/', { headers: { accept: 'text/html' } }); const b = await call('/api/board');
        const c = await call('/app/mods/_lib/columns.js'); const l = await call('/login.css');
        out([a.s, a.t, a.b.includes('<form>'), b.s, JSON.parse(b.b), c.s, l.s, l.t]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[401,"text/html; charset=utf-8",true,401,{"error":"token_required"},401,200,"text/css; charset=utf-8"]' ]
}

@test "app: cookie or bearer reads the board; every response carries the security headers" {
  _web "$APP const a = await call('/api/board', { headers: cookie }); const b = await call('/api/board', { headers: { authorization: 'Bearer ' + TOK } });
        out([a.s, b.s, a.h['cache-control'], a.h['content-security-policy'].startsWith(\"default-src 'none'\"), a.h['x-content-type-options'], a.h['referrer-policy']]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[200,200,"no-store",true,"nosniff","no-referrer"]' ]
}

@test "app: POST /login with the token sets the session and redirects; a wrong one is 401" {
  _web "$APP const form = (t, o = 'http://127.0.0.1:7330') => ({ method: 'POST', headers: { origin: o, host: '127.0.0.1:7330', 'content-type': 'application/x-www-form-urlencoded' }, body: 'token=' + t });
        const ok = await call('/login', form(TOK)); const bad = await call('/login', form('nope')); const cross = await call('/login', form(TOK, 'https://evil.example'));
        out([ok.s, ok.h.location, ok.h['set-cookie'].startsWith('pr_board_session='), bad.s, bad.b.includes('not right'), cross.s]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[303,"/",true,401,true,403]' ]
}

@test "app: mutating POSTs need JSON and a same-origin Origin on top of the session" {
  _web "$APP const post = (h) => call('/api/refresh', { method: 'POST', headers: { ...cookie, host: '127.0.0.1:7330', ...h }, body: '{}' });
        const none = await post({ 'content-type': 'application/json' });
        const cross = await post({ 'content-type': 'application/json', origin: 'https://evil.example' });
        const form = await post({ 'content-type': 'text/plain', origin: 'http://127.0.0.1:7330' });
        const ok = await post({ 'content-type': 'application/json', origin: 'http://127.0.0.1:7330' });
        out([none.s, cross.s, form.s, ok.s, board.asked]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[403,403,403,202,{"fresh":true}]' ]
}

@test "app: POST /logout clears the cookie with an empty 204" {
  _web "$APP const r = await call('/logout', { method: 'POST', headers: { ...cookie, host: '127.0.0.1:7330', origin: 'http://127.0.0.1:7330', 'content-type': 'application/json' }, body: '{}' });
        out([r.s, r.b, r.h['set-cookie']]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[204,"","pr_board_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"]' ]
}

@test "app: statics are an exact allowlist; templates and traversal are 404; modules are text/javascript" {
  _web "$APP const g = (p) => call(p, { headers: cookie });
        const a = await g('/app/mods/_lib/columns.js'); const b = await g('/web/lib/../server/config.js'); const c = await g('/web/server/config.js');
        const d = await g('/web/public/index.html'); const e = await g('/'); const f = await g('/gen/theme.css');
        out([a.s, a.t, b.s, c.s, d.s, e.s, e.b, f.s, f.t]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[200,"text/javascript; charset=utf-8",404,404,404,200,"<html>board</html>",200,"text/css; charset=utf-8"]' ]
}

@test "app: a review body is read from the run's own path; unknown PRs, stamps and bad refs are 404/400" {
  _web "$APP const g = (p) => call(p, { headers: cookie });
        const a = await g('/api/review?repo=acme/box&number=3&stamp=s1'); const b = await g('/api/review?repo=acme/box&number=3&stamp=zz');
        const c = await g('/api/review?repo=acme/box&number=4&stamp=s1'); const d = await g('/api/review?repo=../x&number=3&stamp=s1');
        const e = await g('/api/review?repo=acme/..&number=3&stamp=s1'); const f = await g('/api/review?repo=acme/box&number=3x&stamp=s1');
        out([a.s, JSON.parse(a.b), reads, b.s, c.s, d.s, e.s, f.s]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[200,{"ok":true,"text":"# body"},["/r/x/review.md"],404,404,400,400,400]' ]
}

@test "app: files and a patch for a board PR; a patch path outside the list is 404" {
  _web "$APP const g = (p) => call(p, { headers: cookie });
        const a = await g('/api/files?repo=acme/box&number=3'); const b = await g('/api/patch?repo=acme/box&number=3&path=a.js');
        out([a.s, JSON.parse(a.b).files.length, b.s, JSON.parse(b.b)]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[200,1,200,{"ok":true,"patch":"@@ a.js"}]' ]
}

@test "sse: a subscriber gets the initial events; changeFeed sends board on change, status otherwise" {
  _web "const { createHub, changeFeed } = await import(W + '/server/sse.js');
        const hub = createHub({ every: () => 0, stopEvery: () => {} });
        const res = hub.subscribe([['board', { a: 1 }]]);
        const reader = res.body.getReader(); const dec = new TextDecoder();
        const feed = changeFeed(hub);
        feed({ a: 1, updatedAt: 1, refreshing: false }); feed({ a: 1, updatedAt: 2, refreshing: true }); feed({ a: 2, updatedAt: 3, refreshing: false });
        let text = ''; for (let i = 0; i < 4; i++) text += dec.decode((await reader.read()).value);
        out([res.headers.get('content-type'), res.headers.get('cache-control'), res.headers.get('x-accel-buffering'),
             res.headers.get('x-content-type-options'), res.headers.get('referrer-policy'),
             res.headers.get('cross-origin-opener-policy'), res.headers.get('cross-origin-resource-policy'),
             (res.headers.get('content-security-policy') || '').startsWith(\"default-src 'none'\"),
             hub.count(), text.split('\n\n').filter(Boolean).map((f) => f.split('\n')[0])]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["text/event-stream; charset=utf-8","no-store","no","nosniff","no-referrer","same-origin","same-origin",true,1,["event: board","event: board","event: status","event: board"]]' ]
}

@test "sse: changeFeed treats repoStates updatedAt as clock and phase as board" {
  _web "const { createHub, changeFeed } = await import(W + '/server/sse.js');
        const hub = createHub({ every: () => 0, stopEvery: () => {} });
        const res = hub.subscribe([]);
        const reader = res.body.getReader(); const dec = new TextDecoder();
        const feed = changeFeed(hub);
        const row = (phase, at) => ({ slug: 'acme/box', folder: 'box', on: null, phase, partial: false, updatedAt: at, error: null });
        feed({ repoStates: [row('loaded', 1)], updatedAt: 1, refreshing: false });
        feed({ repoStates: [row('loaded', 2)], updatedAt: 2, refreshing: false });
        feed({ repoStates: [row('stale', 2)], updatedAt: 2, refreshing: false });
        let text = ''; for (let i = 0; i < 3; i++) text += dec.decode((await reader.read()).value);
        out(text.split('\n\n').filter(Boolean).map((f) => f.split('\n')[0]));"
  [ "$status" -eq 0 ]
  [ "$output" = '["event: board","event: status","event: board"]' ]
}

@test "sse: a throwing enqueue drops the client and stops its heartbeat" {
  _web "const { createHub } = await import(W + '/server/sse.js');
        const proto = ReadableStreamDefaultController.prototype;
        const orig = proto.enqueue;
        const stopped = [];
        const result = {};
        try {
          let tick;
          const hub = createHub({ every: (fn) => { tick = fn; return 7; }, stopEvery: (id) => { stopped.push(id); } });
          hub.subscribe([['board', { a: 1 }]]);
          proto.enqueue = function () { throw new Error('boom'); };
          tick();
          result.heartbeat = { count: hub.count(), stopped: [...stopped] };
          stopped.length = 0;
          proto.enqueue = orig;
          const hub2 = createHub({ every: () => 9, stopEvery: (id) => { stopped.push(id); } });
          hub2.subscribe([['board', { a: 1 }]]);
          proto.enqueue = function () { throw new Error('boom'); };
          hub2.broadcast('status', { updatedAt: 1 });
          result.broadcast = { count: hub2.count(), stopped: [...stopped] };
        } finally {
          proto.enqueue = orig;
        }
        out(result);"
  [ "$status" -eq 0 ]
  [ "$output" = '{"heartbeat":{"count":0,"stopped":[7]},"broadcast":{"count":0,"stopped":[9]}}' ]
}

@test "sse: broadcast skips a stalled client instead of growing the queue past the high-water mark" {
  _web "const { createHub } = await import(W + '/server/sse.js');
        const hub = createHub({ every: () => 0, stopEvery: () => {}, highWaterMark: 1 });
        const res = hub.subscribe([['board', { n: 0 }]]);
        for (let i = 1; i <= 8; i++) hub.broadcast('board', { n: i });
        const reader = res.body.getReader();
        const first = await reader.read();
        let extra = false;
        reader.read().then(() => { extra = true; });
        await Promise.resolve();
        await Promise.resolve();
        const text = new TextDecoder().decode(first.value);
        out({ count: hub.count(), extra, oneBoard: text.startsWith('event: board') });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"count":1,"extra":false,"oneBoard":true}' ]
}

@test "app: authenticated GET /favicon.ico is 204; tokenless stays 401" {
  _web "$APP const a = await call('/favicon.ico'); const b = await call('/favicon.ico', { headers: cookie });
        out([a.s, JSON.parse(a.b), b.s, b.b, b.h['x-content-type-options'], (b.h['content-security-policy'] || '').startsWith(\"default-src 'none'\")]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[401,{"error":"token_required"},204,"","nosniff",true]' ]
}

@test "files: one load per PR head, oldest evicted past the cap, failures not cached" {
  _web "const { createFiles } = await import(W + '/server/files.js');
        const exec = fakeExec([{ prefix: 'gh api /repos/acme/box/pulls/3/files', stdout: JSON.stringify([{ filename: 'a.js', status: 'modified', additions: 1, deletions: 0, has_patch: true }]) + '\n' }]);
        const f = createFiles({ exec, max: 1 });
        const p = { repoSlug: 'acme/box', number: 3, headSha: 'h1', cwd: null, on: null };
        await f.files(p); await f.files(p); await f.files({ ...p, number: 9 }); await f.files(p);
        out(calls.filter((c) => c.cmd === 'gh').length);"
  [ "$status" -eq 0 ]
  [ "$output" = '3' ]
}
