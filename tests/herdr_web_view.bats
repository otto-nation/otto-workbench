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

VIEW_FIX='const fs = await import("node:fs");
const bd = await import(L + "/board_doc.js");
const doc = JSON.parse(fs.readFileSync(W + "/../../../tests/fixtures/board_document.json", "utf8"));
const rows = doc.rows.map(bd.rowToPr).map((p) => ({ ...p, repoSlug: "acme/box", viewer: doc.viewer }));
const kit = rows.map((p) => ({ ...p, repoSlug: "acme/kit", number: p.number + 100, labels: ["infra"], author: p.number === 8 ? "dependabot[bot]" : p.author }));
const snap = { me: doc.viewer, repoSlugs: ["acme/box", "acme/kit"], prs: [...rows, ...kit], columns: doc.columns };
const pr = (n, o = {}) => ({ number: n, repoSlug: "acme/box", title: "t" + n, author: "bob", viewer: "ada", branch: "b", state: "OPEN",
  isDraft: false, inMergeQueue: false, mergeable: "MERGEABLE", ciStatus: "passing", reviewDecision: null, myReviewState: null,
  isRequestedReviewer: false, hasNewChangesSinceReview: false, reviewing: false, reviewRuns: [], labels: [], busy: false, ...o });'

PRES_FIX='const fs = await import("node:fs");
const fx = JSON.parse(fs.readFileSync(W + "/../../../tests/fixtures/board_presentation.json", "utf8"));
const docRow = (name) => ({ presentation: fx.cases.find((c) => c.name === name).document_presentation });'

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
  [ "$output" = '{"cols":["open:8,108","reviewed:7,107"],"kit":[108,107],"authors":["dependabot[bot]","pat","you"],"repos":["acme/box","acme/kit"],"count":[0,1],"ui":["tab","theme","filters"]}' ]
}

@test "view: mine tab buckets the viewer's PRs from the document; Has Comments is the owed lane" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const off = v.boardModel(snap, { ...v.DEFAULT_UI, tab: 'mine' });
        const on = v.boardModel(snap, v.toggleFilter({ ...v.DEFAULT_UI, tab: 'mine' }, 'mineDrafts'));
        const stored = v.boardModel(snap, { ...v.DEFAULT_UI, tab: 'mine', filters: { ...v.DEFAULT_UI.filters, mineDrafts: true } });
        out([off.columns.map((c) => c.key), off.sections.map((s) => s.key + ':' + s.count), on.columns.map((c) => c.key), on.sections.map((s) => s.key + ':' + s.count), stored.columns.map((c) => c.key)]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[["has-comments"],["has-comments:2"],["has-comments"],["has-comments:2"],["has-comments"]]' ]
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
  [ "$output" = '[[],["bob"],["acme/box"],true,[],2,1,1]' ]
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
  [ "$output" = '[[7,8],[7],["not kit","not bug","not cy","Bots hidden"],["show","repos","labels","authors"],["show","repos","labels"]]' ]
}

@test "view: card badges are text, status or tag (never pills), as prPresentation().badges" {
  _web "$PRES_FIX const m = await import(L + '/pr_status.js');
        out(m.prPresentation(docRow('draft, conflicts, failing CI, changes requested, owed feedback')).badges);"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"status","status":"fail","text":"Conflicts"},{"kind":"status","status":"fail","text":"CI failing"},{"kind":"status","status":"warn","text":"2 unresolved threads"},{"kind":"status","status":"warn","text":"Changes requested"},{"kind":"text","status":null,"text":"2 reviews"},{"kind":"tag","status":null,"text":"infra"},{"kind":"tag","status":null,"text":"ui"}]' ]
}

@test "view: ciUnknown is a CI unknown status badge" {
  _web "$PRES_FIX const m = await import(L + '/pr_status.js');
        out(m.prPresentation(docRow('CI unknown')).badges);"
  [ "$status" -eq 0 ]
  [ "$output" = '[{"kind":"status","status":"none","text":"CI unknown"}]' ]
}

@test "view: toSnapshot keeps post.event and post.submitted on a run" {
  _web "const s = await import(W + '/lib/snapshot.js');
        const post = { event: 'pending', submitted: false };
        const snap = s.toSnapshot({ prs: [{ number: 1, repoSlug: 'o/n', cwd: '/w', reviewRuns: [{ postedEvent: 'pending', run: { stamp: 's', post } }] }] }, { name: 'n', version: '1' });
        const run = snap.prs[0].reviewRuns[0].run;
        out({ event: run.post.event, submitted: run.post.submitted, hasCwd: 'cwd' in snap.prs[0] });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"event":"pending","submitted":false,"hasCwd":false}' ]
}

@test "view: a pending run shows the draft-on-GitHub history label" {
  _web "const { historyRows } = await import(L + '/history.js');
        const rows = historyRows([{ stamp: 's', reviewed_at: '2026-08-18T13:00:00Z', post: { event: 'pending' } }], Date.parse('2026-08-18T14:00:00Z'));
        out(rows[0].post);"
  [ "$status" -eq 0 ]
  [ "$output" = '{"label":"draft on GitHub — only you can see it","status":"warn"}' ]
}

@test "view: toSnapshot whitelists repoStates and stringifies error" {
  _web "const s = await import(W + '/lib/snapshot.js');
        const cols = [{ board: 'team', key: 'open', label: 'Open', order: 0 }];
        const withCols = s.toSnapshot({ columns: cols, repoStates: [{ slug: 'acme/x', folder: 'x', on: null, phase: 'failed', partial: false, updatedAt: 1,
          error: { message: 'boom' }, cwd: '/w/x', extra: 'nope', owed: { state: 'error', error: { kind: 'auth' } } }] }, { name: 'srv', version: '1' });
        const empty = s.toSnapshot(null, { name: 'n', version: '1' });
        const idle = s.toSnapshot({ repoStates: [{ slug: 'acme/x', folder: 'x', on: null, phase: 'loaded', partial: false, updatedAt: 1, error: null }] }, { name: 'n', version: '1' });
        const row = withCols.repoStates[0];
        out({ keys: Object.keys(row).sort(), cwd: 'cwd' in row, extra: 'extra' in row, error: row.error, columns: withCols.columns, empty: empty.columns, owed: row.owed, idle: idle.repoStates[0].owed });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"keys":["error","folder","on","owed","partial","phase","slug","updatedAt"],"cwd":false,"extra":false,"error":"boom","columns":[{"board":"team","key":"open","label":"Open","order":0}],"empty":[],"owed":{"state":"error","error":"run gh auth login on srv"},"idle":{"state":"idle","error":null}}' ]
}

@test "view: state tags (Merged, In merge queue, Draft) are presentation tags, not badges" {
  _web "$PRES_FIX const m = await import(L + '/pr_status.js');
        const names = ['merged', 'in merge queue', 'draft, conflicts, failing CI, changes requested, owed feedback', 'batch busy without a run item'];
        const pres = names.map((n) => m.prPresentation(docRow(n)));
        const state = new Set(['Draft', 'Merged', 'In merge queue']);
        out([pres.map((p) => p.tags), pres.some((p) => p.badges.some((b) => state.has(b.text)))]);"
  [ "$status" -eq 0 ]
  [ "$output" = '[[["Merged"],["In merge queue"],["Draft"],[]],false]' ]
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
  [ "$output" = '["open","reviewed"]' ]
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

@test "view: a live run renders aria-expanded keyed by runKey" {
  _web "$FAKE_DOM
        const { runKey } = await import(L + '/history.js');
        const { renderDetail } = await import(W + '/public/detail_view.js');
        const presentation = {
          ref: 'o/n#1', title: 't', author: 'a', branch: 'b', base_branch: '',
          stats: { additions: '+0', deletions: '-0', files: '0 files' },
          tags: [], badges: [], issues: [], detail_issues: [], row_badges: [],
          ci: null, decision: null, review: null, busy: null,
        };
        const run = { stamp: '', reviewed_at: '2026-08-18T13:00:00Z', verdict: 'approve' };
        const pr = { number: 1, repoSlug: 'o/n', title: 't', author: 'a', branch: 'b', presentation, reviewRuns: [{ run }] };
        document.body = { classList: { toggle() {} } };
        const el = document.createElement('div');
        el.classList = { toggle() {} };
        renderDetail(el, pr, { tab: 'history', openKey: runKey(run), bodies: new Map(), now: Date.parse('2026-08-18T14:00:00Z') }, {
          onClose() {}, onTab() {}, onToggleRun() {}, onPick() {}, onBack() {},
        });
        const walk = (n, acc = []) => {
          if (n == null) return acc;
          if (Array.isArray(n)) { for (const x of n) walk(x, acc); return acc; }
          acc.push(n);
          for (const c of n.children ?? []) walk(c, acc);
          return acc;
        };
        const btn = walk(el).find((n) => n.attrs && n.attrs['aria-expanded'] === 'true');
        out({ key: runKey(run), expanded: btn && btn.attrs['aria-expanded'], empty: runKey(run) === '' });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"key":"live@2026-08-18T13:00:00Z","expanded":"true","empty":false}' ]
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
  [ "$output" = '{"afterErrors":1,"threw":false,"got":[{"ok":1}],"fetches":["/api/board","/api/board"],"url":"/api/events?view=team"}' ]
}

@test "view: status wide adds class wide; skeletonLanes uses column labels without a count" {
  _web "$FAKE_DOM
        const { status } = await import(W + '/public/dom.js');
        const { skeletonLanes } = await import(W + '/public/load_status.js');
        const wide = status('running', 'Loading 1 of 3 repos', { wide: true });
        const plain = status('running', 'x');
        const cols = skeletonLanes([{ key: 'open', label: 'Open' }]);
        const titleKids = (col) => col.children[0].children.map((c) => c.className);
        out({
          wide: wide.className, plain: plain.className,
          n: cols.length,
          label: cols[0].children[0].children[0].children[0].text,
          keys: cols.map((c) => c.className),
          skel: [cols[0].children[1].className, cols[0].children[2].className],
          titleKids: titleKids(cols[0]),
        });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"wide":"status running wide","plain":"status running","n":1,"label":"Open","keys":["col"],"skel":["skel","skel short"],"titleKids":["label trunc"]}' ]
}

@test "view: renderBoard loading draws skeletons; empty not-loading is No pull requests" {
  _web "$FAKE_DOM
        const { renderBoard, renderSections } = await import(W + '/public/board_view.js');
        const board = document.createElement('div');
        renderBoard(board, { columns: [], spec: [{ key: 'open', label: 'Open' }, { key: 'landed', label: 'Landed' }] }, { loading: true });
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
  [ "$output" = '{"loadingCols":["col","col"],"empty":"No pull requests","loadMsg":"Loading…","noneMsg":"No pull requests"}' ]
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
        renderSections(el, { sections: [{ key: 'open', label: 'Open', count: 1, items: [snap.prs[0]] }] }, { loading: false, collapsed: new Set(), onOpen: () => {} });
        const row = el.children[0].children[1].children[0];
        out([row.className, txt(row.children[0]), txt(row.children[1]), el.children[0].children[1].className]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["mrow","Fix comments","box#7 · me!2 unresolved threads","mlist"]' ]
}

@test "view: boardModel is the shared model (lanes carry their Batch preset)" {
  _web "$VIEW_FIX const v = await import(W + '/lib/view.js');
        const ui = { ...v.DEFAULT_UI, tab: 'mine' };
        const ready = v.boardModel({ ...snap, repoStates: [{ slug: 'acme/box', on: null, error: null, owed: { state: 'ready', error: null } }] }, ui);
        const lane = ready.lanes.find((l) => l.key === 'has-comments');
        out({ keys: Object.keys(ready).sort(), authors: ready.authors, batch: lane.batch, status: lane.status });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"keys":["authors","bar","columns","filterCount","lanes","list","options","repoSlugs","sections","spec","tab"],"authors":["dependabot[bot]","pat","you"],"batch":{"steps":["comments"],"ai":true},"status":"ready"}' ]
}

@test "view: a loading lane shows the running glyph in its count slot and Loading comments… instead of None" {
  _web "$FAKE_DOM
        const { renderBoard } = await import(W + '/public/board_view.js');
        const txt = (n) => n.text ?? (n.children ?? []).map(txt).join('');
        const board = document.createElement('div');
        renderBoard(board, { columns: [{ key: 'has-comments', label: 'Has Comments', items: [], status: 'loading', message: 'Loading comments…' }] }, { loading: false });
        const title = board.children[0].children[0];
        const slot = title.children[1];
        const body = board.children[0].children[1];
        out({ slotClass: slot.className, aria: slot.attrs['aria-label'], count: title.children.some((c) => c.className === 'count'), body: txt(body), none: txt(body).includes('None') });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"slotClass":"status running","aria":"Loading comments…","count":false,"body":"Loading comments…","none":false}' ]
}

@test "view: a failed lane shows × and the reason, above any PRs it still has" {
  _web "$FAKE_DOM $VIEW_FIX
        const { renderBoard } = await import(W + '/public/board_view.js');
        const txt = (n) => n.text ?? (n.children ?? []).map(txt).join('');
        const board = document.createElement('div');
        const msg = 'Could not load comments: run gh auth login on srv';
        renderBoard(board, { columns: [{ key: 'has-comments', label: 'Has Comments', items: [snap.prs[0]], status: 'error', message: msg }] }, { loading: false });
        const title = board.children[0].children[0];
        const slot = title.children[1];
        const lane = board.children[0].children[1];
        out({ slotClass: slot.className, aria: slot.attrs['aria-label'], first: lane.children[0].className, firstText: txt(lane.children[0]), second: lane.children[1].className });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"slotClass":"status fail","aria":"Could not load comments: run gh auth login on srv","first":"status fail body","firstText":"×Could not load comments: run gh auth login on srv","second":"card"}' ]
}

@test "view: compact sections show the same state" {
  _web "$FAKE_DOM
        const { renderSections } = await import(W + '/public/board_view.js');
        const txt = (n) => n.text ?? (n.children ?? []).map(txt).join('');
        const secs = document.createElement('div');
        renderSections(secs, { sections: [{ key: 'has-comments', label: 'Has Comments', count: 0, items: [], status: 'loading', message: 'Loading comments…' }] }, { loading: false, collapsed: new Set() });
        const btn = secs.children[0].children[0];
        const slot = btn.children.find((c) => (c.className || '').startsWith('status'));
        const body = secs.children[0].children[1];
        out({ slotClass: slot.className, aria: slot.attrs['aria-label'], body: txt(body), count: btn.children.some((c) => c.className === 'meta-text') });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"slotClass":"status running","aria":"Loading comments…","body":"Loading comments…","count":false}' ]
}

@test "view: filter bar draws author count as meta and a Bots group from barModel" {
  _web "$FAKE_DOM
        globalThis.document.addEventListener = () => {};
        const { renderFilterBar } = await import(W + '/public/filter_bar.js');
        const txt = (n) => n.text ?? (n.children ?? []).map(txt).join('');
        const cam = { value: 'cam', label: 'cam', state: null, include: 'author:cam', exclude: 'not-author:cam', count: 5 };
        const bot = { value: 'dependabot[bot]', label: 'dependabot[bot]', state: null, include: 'author:dependabot[bot]', exclude: 'not-author:dependabot[bot]', count: 2 };
        const model = { facets: [{ key: 'authors', label: 'Author', active: false, search: false,
          options: [cam, bot], groups: [{ label: null, options: [cam] }, { label: 'Bots', options: [bot] }] }], tags: [], clear: null, count: 0 };
        const el = document.createElement('div');
        el.contains = () => false;
        el.querySelector = () => null;
        renderFilterBar(el, model, { onAction: () => {} });
        const open = el.children[0].children[0].listeners.find((l) => l[0] === 'click')[1];
        open();
        const pop = el.children[0].children[1];
        const list = pop.children[0];
        const rows = list.children.filter((c) => c.className === 'frow' || c.className === 'frow ex').map((row) => {
          const label = row.children[0];
          const name = label.children.find((x) => x.className === 'name trunc');
          const meta = label.children.filter((x) => x.className === 'meta-text').map(txt);
          return { name: txt(name), meta, hide: txt(row.children[1]) };
        });
        out({ pop: pop.className, classes: list.children.map((c) => c.className), rows, bots: txt(list.children.find((c) => c.className === 'section-label')) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"pop":"fpop","classes":["frow","fsep","section-label","frow"],"rows":[{"name":"cam","meta":["5"],"hide":"Hide"},{"name":"dependabot[bot]","meta":["2"],"hide":"Hide"}],"bots":"Bots"}' ]
}
