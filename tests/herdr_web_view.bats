#!/usr/bin/env bats
# Tests for ai/herdr/web/lib/view.js — the PR board page's view model.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

VIEW_FIX='const pr = (n, o = {}) => ({ number: n, repoSlug: "acme/box", title: "t" + n, author: "bob", viewer: "ada", branch: "b", state: "OPEN",
  isDraft: false, inMergeQueue: false, mergeable: "MERGEABLE", ciStatus: "passing", reviewDecision: null, myReviewState: null,
  isRequestedReviewer: false, hasNewChangesSinceReview: false, reviewing: false, reviewRuns: [], labels: [], busy: false, ...o });
const snap = { me: "ada", repoSlugs: ["acme/box", "acme/kit"], prs: [pr(1), pr(2, { isDraft: true }), pr(3, { author: "ada", mergeable: "CONFLICTING" }),
  pr(4, { repoSlug: "acme/kit", author: "cy" }), pr(5, { state: "MERGED" }), pr(6, { author: "ada", isDraft: true })] };'

@test "view: filter defaults come from _lib DEFAULT_FILTERS (drafts hidden on both tabs), not a restated copy" {
  _web "const v = await import(W + '/lib/view.js'); const c = await import(L + '/columns.js');
        out([Object.keys(c.DEFAULT_FILTERS).every((k) => v.DEFAULT_UI.filters[k] === c.DEFAULT_FILTERS[k]), v.DEFAULT_UI.filters.drafts, v.DEFAULT_UI.filters.mineDrafts]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[true,false,false]' ]
}

@test "view: team tab hides drafts and landed by default; a Repo chip picks one repo; empty lanes hidden; no scope state" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const ui = v.DEFAULT_UI;
        const m = v.boardModel(snap, ui);
        const k = v.boardModel(snap, v.toggleFilter(ui, 'repo:acme/kit'));
        out({ cols: m.columns.map((c) => c.key + ':' + c.items.map((p) => p.number).join(',')), kit: k.columns.flatMap((c) => c.items.map((p) => p.number)),
              authors: m.authors, repos: m.repoSlugs, count: [m.filterCount, k.filterCount], ui: Object.keys(v.DEFAULT_UI) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"cols":["open:1,3,4"],"kit":[4],"authors":["bob","cy"],"repos":["acme/box","acme/kit"],"count":[0,1],"ui":["tab","theme","filters"]}' ]
}

@test "view: mine tab buckets the viewer's PRs with MINE_COLUMNS; drafts hidden by default (Draft lane empty, so hidden), shown with its own Drafts chip" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const off = v.boardModel(snap, { ...v.DEFAULT_UI, tab: 'mine' });
        const on = v.boardModel(snap, v.toggleFilter({ ...v.DEFAULT_UI, tab: 'mine' }, 'mineDrafts'));
        const stored = v.boardModel(snap, { ...v.DEFAULT_UI, tab: 'mine', filters: { ...v.DEFAULT_UI.filters, mineDrafts: true } });
        out([off.columns.map((c) => c.key), off.sections.map((s) => s.key + ':' + s.count), on.columns.map((c) => c.key), on.sections.map((s) => s.key + ':' + s.count), stored.columns.map((c) => c.key)]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[["has-conflicts"],["has-conflicts:1"],["has-conflicts","draft"],["has-conflicts:1","draft:1"],["has-conflicts","draft"]]' ]
}

@test "view: toggleFilter is immutable and counts every chip off its default, including Drafts" {
  _web "const v = await import(W + '/lib/view.js');
        const a = v.DEFAULT_UI; const b = v.toggleFilter(v.toggleFilter(v.toggleFilter(a, 'author:Bob'), 'repo:acme/box'), 'landed');
        const c = v.toggleFilter(b, 'author:bob');
        const d = v.toggleFilter(a, 'drafts');
        const m = v.toggleFilter({ ...a, tab: 'mine' }, 'mineDrafts');
        out([a.filters.authors, b.filters.authors, b.filters.repos, b.filters.landed, c.filters.authors,
             v.boardModel({ prs: [] }, b).filterCount, v.boardModel({ prs: [] }, d).filterCount, v.boardModel({ prs: [] }, m).filterCount]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[[],["bob"],["acme/box"],true,[],3,1,1]' ]
}

@test "view: card badges are text, status or tag (never pills), as prs/board.js badges()" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const p = pr(1, { isDraft: true, state: 'MERGED', inMergeQueue: true, busy: true, reviewRuns: [{}, {}], labels: ['a', 'b', 'c'] });
        out([v.cardBadges(p), v.cardBadges(p, { hideBusy: true }).map((b) => b.text)]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[[{"kind":"text","text":"Draft"},{"kind":"status","status":"pass","text":"Merged"},{"kind":"status","status":"running","text":"In merge queue"},{"kind":"status","status":"running","text":"Batch working"},{"kind":"text","text":"2 reviews"},{"kind":"tag","text":"a"},{"kind":"tag","text":"b"}],["Draft","Merged","In merge queue","2 reviews","a","b"]]' ]
}

@test "view: h() puts untrusted text in a text node, never innerHTML" {
  _web "class N {}
        class E extends N {
          constructor(tag) { super(); this.tagName = tag; this.children = []; this.attrs = {}; this.className = ''; this.innerHTML = ''; this.dataset = {}; }
          setAttribute(k, v) { this.attrs[k] = v; }
          addEventListener() {}
          append(...c) { this.children.push(...c); }
        }
        globalThis.Node = N;
        globalThis.document = { createElement: (t) => new E(t), createTextNode: (s) => ({ text: s }) };
        const { h } = await import(W + '/public/dom.js');
        const el = h('div', { class: 't', title: '<b>nope</b>' }, '<img src=x onerror=alert(1)>');
        out([el.innerHTML, el.children[0].text, el.attrs.title, el.className]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["","<img src=x onerror=alert(1)>","<b>nope</b>","t"]' ]
}

@test "view: numbers, decision text, https-only PR URL, generated files last, status kinds, stale vs failed" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        out([v.cardNumber(pr(7)), v.cardNumber({ number: 7 }), v.decisionText('CHANGES_REQUESTED'),
             v.prUrl({ url: 'https://github.com/a/b/pull/1' }), v.prUrl({ url: 'javascript:alert(1)', repoSlug: 'a/b', number: 1 }),
             v.sortedFiles([{ path: 'g', generated: true }, { path: 'a', generated: false }]).map((f) => f.path),
             ['success', 'warning', 'info', 'muted'].map(v.postKind), ['ok', 'Partial', 'Error'].map(v.healthKind), ['A', 'D', 'M'].map(v.fileMark),
             v.errorStatus({ updatedAt: 1000 }, 121000), v.errorStatus({ updatedAt: null }, 5), v.GLYPH]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["box #7","#7","changes requested","https://github.com/a/b/pull/1","https://github.com/a/b/pull/1",["a","g"],["pass","warn","none","none"],[null,"warn","fail"],["add","del","mod"],{"status":"warn","text":"stale · 2m ago"},{"status":"fail","text":"failed"},{"pass":"✓","fail":"×","running":"…","warn":"!","none":"·"}]' ]
}

@test "view: empty-lanes is read with filterOn, not Boolean" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const ui = { ...v.DEFAULT_UI, filters: { ...v.DEFAULT_UI.filters, empty: 'yes' } };
        out(v.boardModel(snap, ui).columns.map((c) => c.key));"
  [ "$status" -eq 0 ]
  [ "$output" = '["open"]' ]
}

@test "view: prUrl accepts only credential-free https; host is a hostname" {
  _web "const v = await import(W + '/lib/view.js');
        const js = v.prUrl({ url: 'javascript:alert(1)' });
        const http = v.prUrl({ url: 'http://github.com/a/b/pull/1' });
        const cred = v.prUrl({ url: 'https://user:pw@evil/' });
        const nl = v.prUrl({ url: 'https://github.com/a/b/pull/1\\n' });
        const ok = v.prUrl({ url: 'https://github.com/a/b/pull/1' });
        const host = v.prUrl({ repoSlug: 'a/b', number: 1, host: 'evil.com/steal' });
        out([js, http, cred, nl, ok, host]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["","","","","https://github.com/a/b/pull/1","https://github.com/a/b/pull/1"]' ]
}

FAKE_DOM='class N {}
        class E extends N {
          constructor(tag) { super(); this.tagName = tag; this.children = []; this.attrs = {}; this.className = ""; this.innerHTML = ""; this.dataset = {}; this.listeners = []; }
          setAttribute(k, v) { this.attrs[k] = v; }
          addEventListener(type, fn) { this.listeners.push([type, fn]); }
          append(...c) { this.children.push(...c); }
          replaceChildren(...c) { this.children = [...c]; }
        }
        globalThis.Node = N;
        globalThis.document = { createElement: (t) => new E(t), createTextNode: (s) => ({ text: s }) };'

@test "view: h() skips non-function on* keys and refuses a style attribute" {
  _web "$FAKE_DOM
        const { h } = await import(W + '/public/dom.js');
        const el = h('div', { onclick: 'alert(1)', onfoo: 1, style: 'x:1', title: 'ok', onmouseover: () => 1 }, 't');
        out([el.attrs, el.listeners.map((l) => l[0])]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"title":"ok"},["mouseover"]]' ]
}

@test "view: setChildren drops null and false rather than rendering their text" {
  _web "$FAKE_DOM
        const { setChildren } = await import(W + '/public/dom.js');
        const el = document.createElement('div');
        const node = document.createTextNode('keep');
        setChildren(el, null, false, node);
        out(el.children);"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"text":"keep"}]' ]
}

@test "view: history rows and files are buttons; the expanded body is a sibling" {
  DETAIL="$REPO_ROOT/ai/herdr/web/public/detail_view.js"
  run grep -F "h('button', { class: 'card run', type: 'button'" "$DETAIL"
  [ "$status" -eq 0 ]
  run grep -F "type: 'button', onclick: () => onPick" "$DETAIL"
  [ "$status" -eq 0 ]
  run grep -F "e.stopPropagation()" "$DETAIL"
  [ "$status" -eq 0 ]
  run grep -F "h('div', { class: 'card run'" "$DETAIL"
  [ "$status" -eq 1 ]
  run grep -E "h\('div'.*onPick" "$DETAIL"
  [ "$status" -eq 1 ]
}

@test "view: EventSource JSON parse is guarded and the 401 probe is one-shot until a message" {
  _web 'class FakeES {
          constructor(url) { this.url = url; this.fns = {}; }
          addEventListener(name, fn) { (this.fns[name] ??= []).push(fn); }
          fire(name, e) { for (const f of this.fns[name] ?? []) f(e); }
        }
        globalThis.EventSource = FakeES;
        const fetches = [];
        globalThis.fetch = async (url) => { fetches.push(String(url)); return { status: 200, ok: true, json: async () => ({ ok: true }) }; };
        globalThis.location = { reload() {} };
        const got = [];
        const { events } = await import(W + "/public/api.js");
        const es = events({ board: (d) => got.push(d) });
        es.fire("error"); es.fire("error"); es.fire("error");
        const afterErrors = fetches.length;
        let threw = false;
        try { es.fire("board", { data: "{bad" }); } catch { threw = true; }
        es.fire("board", { data: JSON.stringify({ ok: 1 }) });
        es.fire("error"); es.fire("error");
        out({ afterErrors, threw, got, fetches, url: es.url });'
  [ "$status" -eq 0 ]
  [ "$output" = '{"afterErrors":1,"threw":false,"got":[{"ok":1}],"fetches":["/api/board","/api/board"],"url":"/api/events"}' ]
}
