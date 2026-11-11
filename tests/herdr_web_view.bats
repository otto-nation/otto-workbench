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

@test "view: mine tab buckets the viewer's PRs with MINE_COLUMNS; drafts hidden by default, shown with the Drafts chip in the lane for their real state" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const off = v.boardModel(snap, { ...v.DEFAULT_UI, tab: 'mine' });
        const on = v.boardModel(snap, v.toggleFilter({ ...v.DEFAULT_UI, tab: 'mine' }, 'mineDrafts'));
        const stored = v.boardModel(snap, { ...v.DEFAULT_UI, tab: 'mine', filters: { ...v.DEFAULT_UI.filters, mineDrafts: true } });
        out([off.columns.map((c) => c.key), off.sections.map((s) => s.key + ':' + s.count), on.columns.map((c) => c.key), on.sections.map((s) => s.key + ':' + s.count), stored.columns.map((c) => c.key)]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[["has-conflicts"],["has-conflicts:1"],["has-conflicts","awaiting-review"],["has-conflicts:1","awaiting-review:1"],["has-conflicts","awaiting-review"]]' ]
}

@test "view: toggleFilter is immutable and counts every chip off its default, including Drafts" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const a = v.DEFAULT_UI; const b = v.toggleFilter(v.toggleFilter(v.toggleFilter(a, 'author:Bob'), 'repo:acme/box'), 'landed');
        const c = v.toggleFilter(b, 'author:bob');
        const d = v.toggleFilter(a, 'drafts');
        const m = v.toggleFilter({ ...a, tab: 'mine' }, 'mineDrafts');
        out([a.filters.authors, b.filters.authors, b.filters.repos, b.filters.landed, c.filters.authors,
             v.boardModel(snap, b).filterCount, v.boardModel(snap, d).filterCount, v.boardModel(snap, m).filterCount]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[[],["bob"],["acme/box"],true,[],3,1,1]' ]
}

@test "view: boardModel exclude facets, hideBots, and stale author include; bar keys per tab" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const shot = { ...snap, prs: [...snap.prs, pr(7, { author: 'dependabot[bot]' }), pr(8, { labels: ['bug'] })] };
        const filters = { ...v.DEFAULT_UI.filters, excludeRepos: ['acme/kit'], excludeLabels: ['bug'],
          excludeAuthors: ['cy'], authors: ['stale-login'], hideBots: true };
        const team = v.boardModel(shot, { ...v.DEFAULT_UI, filters });
        const mine = v.boardModel(shot, { ...v.DEFAULT_UI, tab: 'mine', filters });
        const nums = (m) => m.columns.flatMap((c) => c.items.map((p) => p.number)).sort((a, b) => a - b);
        out([nums(team), nums(mine), team.bar.tags.map((t) => t.text), team.bar.facets.map((f) => f.key), mine.bar.facets.map((f) => f.key)]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[[1,3],[3],["not kit","not bug","not cy","Bots hidden"],["show","repos","labels","authors"],["show","repos","labels"]]' ]
}

@test "view: card badges are text, status or tag (never pills), as prPresentation().badges" {
  _web "$VIEW_FIX const m = await import(L + '/pr_status.js');
        const p = pr(1, { isDraft: true, state: 'OPEN', inMergeQueue: true, mergeable: 'CONFLICTING', ciStatus: 'failing', reviewDecision: 'CHANGES_REQUESTED', busy: true, reviewRuns: [{}, {}], labels: ['a', 'b', 'c'] });
        out(m.prPresentation(p).badges);"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"status","status":"fail","text":"Conflicts"},{"kind":"status","status":"fail","text":"CI failing"},{"kind":"status","status":"warn","text":"Changes requested"},{"kind":"status","status":"running","text":"Batch · working"},{"kind":"text","status":null,"text":"2 reviews"},{"kind":"tag","status":null,"text":"a"},{"kind":"tag","status":null,"text":"b"}]' ]
}

@test "view: ciUnknown is a CI unknown status badge" {
  _web "$VIEW_FIX const m = await import(L + '/pr_status.js');
        const s = await import(W + '/lib/snapshot.js');
        const p = pr(1, { ciUnknown: true, ciStatus: 'none' });
        const shot = s.toSnapshot({ prs: [p] }, { name: 'n', version: '1' });
        out(m.prPresentation(shot.prs[0]).badges);"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"status","status":"none","text":"CI unknown"}]' ]
}

@test "view: toSnapshot whitelists repoStates and stringifies error" {
  _web "const s = await import(W + '/lib/snapshot.js');
        const shot = s.toSnapshot({
          repoStates: [{ slug: 'acme/x', folder: 'x', on: null, phase: 'failed', partial: false, updatedAt: 1,
            error: { message: 'boom' }, cwd: '/w/x', extra: 'nope' }],
        }, { name: 'n', version: '1' });
        const row = shot.repoStates[0];
        out({ keys: Object.keys(row).sort(), cwd: 'cwd' in row, extra: 'extra' in row, error: row.error });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"keys":["error","folder","on","partial","phase","slug","updatedAt"],"cwd":false,"extra":false,"error":"boom"}' ]
}

@test "view: state tags (Merged, In merge queue, Draft) are presentation tags, not badges" {
  _web "$VIEW_FIX const m = await import(L + '/pr_status.js');
        const p = pr(1, { state: 'MERGED', isDraft: true });
        const pres = m.prPresentation(p);
        out([pres.tags, pres.badges.some((b) => b.text === 'Draft' || b.text === 'Merged')]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[["Merged","Draft"],false]' ]
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

@test "view: https-only PR URL, generated files last" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        out([v.prUrl({ url: 'https://github.com/a/b/pull/1' }), v.prUrl({ url: 'javascript:alert(1)', repoSlug: 'a/b', number: 1 }),
             v.sortedFiles([{ path: 'g', generated: true }, { path: 'a', generated: false }]).map((f) => f.path)]);
"
  [ "$status" -eq 0 ]
  [ "$output" = '["https://github.com/a/b/pull/1","https://github.com/a/b/pull/1",["a","g"]]' ]
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
          prepend(...c) { this.children.unshift(...c); }
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

@test "view: status wide adds class wide; skeletonLanes uses column labels without a count" {
  _web "$FAKE_DOM
        const { status } = await import(W + '/public/dom.js');
        const { skeletonLanes } = await import(W + '/public/load_status.js');
        const { MINE_COLUMNS, TEAM_COLUMNS } = await import(L + '/columns.js');
        const wide = status('running', 'Loading 1 of 3 repos', { wide: true });
        const plain = status('running', 'x');
        const mine = skeletonLanes('mine');
        const team = skeletonLanes('team');
        const titleKids = (col) => col.children[0].children.map((c) => c.className);
        out({
          wide: wide.className, plain: plain.className,
          mineN: mine.length, teamN: team.length,
          mineLabel: mine[0].children[0].children[0].children[0].text,
          teamLabel: team[0].children[0].children[0].children[0].text,
          wantMine: MINE_COLUMNS[0].label, wantTeam: TEAM_COLUMNS[0].label,
          mineKeys: mine.map((c) => c.className),
          skel: [mine[0].children[1].className, mine[0].children[2].className],
          titleKids: titleKids(mine[0]),
        });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"wide":"status running wide","plain":"status running","mineN":9,"teamN":6,"mineLabel":"Has Conflicts","teamLabel":"Open","wantMine":"Has Conflicts","wantTeam":"Open","mineKeys":["col","col","col","col","col","col","col","col","col"],"skel":["skel","skel short"],"titleKids":["label trunc"]}' ]
}

@test "view: renderBoard loading draws skeletons; empty not-loading is No pull requests" {
  _web "$FAKE_DOM
        const { renderBoard, renderSections } = await import(W + '/public/board_view.js');
        const board = document.createElement('div');
        renderBoard(board, { columns: [] }, { loading: true, tab: 'team' });
        const loadingCols = board.children.map((c) => c.className);
        renderBoard(board, { columns: [] }, { loading: false });
        const empty = board.children[0].children[0].text;
        const secs = document.createElement('div');
        renderSections(secs, { sections: [] }, { loading: true, collapsed: new Set() });
        const loadMsg = secs.children[0].children[0].text;
        renderSections(secs, { sections: [] }, { loading: false, collapsed: new Set() });
        const noneMsg = secs.children[0].children[0].text;
        out({ loadingCols, empty, loadMsg, noneMsg });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"loadingCols":["col","col","col","col","col","col"],"empty":"No pull requests","loadMsg":"Loading…","noneMsg":"No pull requests"}' ]
}

@test "view: renderLoad prepends a wide running line, fills progress, and paints the strip" {
  _web "$FAKE_DOM
        const { renderLoad } = await import(W + '/public/load_status.js');
        const head = document.createElement('div');
        const strip = document.createElement('div');
        const bar = { hidden: true, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
        renderLoad({ head, strip, bar }, {
          shown: true, line: 'Loading 1 of 3 repos', progress: 33,
          strip: [{ name: 'box', kind: 'running' }, { name: 'kit', kind: 'none' }],
        });
        const shown = {
          headClass: head.children[0].className, headText: head.children[0].children[1].text,
          bar: { hidden: bar.hidden, max: bar.max, value: bar.value, aria: bar.attrs['aria-label'] },
          strip: strip.children.map((c) => [c.className, c.children[1].text]),
        };
        renderLoad({ head, strip, bar }, { shown: false, line: null, progress: null, strip: [{ name: 'box', kind: 'fail' }] });
        out({ shown, hidden: bar.hidden, stripAfter: strip.children.map((c) => c.className) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"shown":{"headClass":"status running wide","headText":"Loading 1 of 3 repos","bar":{"hidden":false,"max":100,"value":33,"aria":"Loading 1 of 3 repos"},"strip":[["status running wide","box"],["status none wide","kit"]]},"hidden":true,"stripAfter":["status fail wide"]}' ]
}

@test "view: renderSections draws dense rows from row_badges, not cards" {
  _web "$FAKE_DOM $VIEW_FIX
        const { renderSections } = await import(W + '/public/board_view.js');
        const txt = (n) => n.text ?? (n.children ?? []).map(txt).join('');
        const el = document.createElement('div');
        renderSections(el, { sections: [{ key: 'open', label: 'Open', count: 1, items: [snap.prs[2]] }] }, { loading: false, collapsed: new Set(), onOpen: () => {} });
        const row = el.children[0].children[1].children[0];
        out([row.className, txt(row.children[0]), txt(row.children[1]), el.children[0].children[1].className]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["mrow","t3","box#3 · ada×Conflicts","mlist"]' ]
}
