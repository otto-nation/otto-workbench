// The board page: state, the event stream, and one render() for everything.
import { events, getJson, postJson, q } from './api.js';
import { h, keepScroll, status } from './dom.js';
import { loadUi, saveUi } from './prefs.js';
import { renderBoard, renderSections } from './board_view.js';
import { renderFilterBar } from './filter_bar.js';
import { renderLoad } from './load_status.js';
import { renderDetail } from './detail_view.js';
import { boardModel, errorStatus, GLYPH, toggleFilter } from '../lib/view.js';
import { loadState, LOAD_DELAY_MS } from '../../app/mods/_lib/load_state.js';
import { viewOf } from '../../app/mods/_lib/compact.js';
import { cardId } from '../../app/mods/_lib/scope.js';
import { patchRows } from '../../app/mods/_lib/diff.js';
import { MONO } from '../../app/mods/_lib/tokens.js';
import { relativeTime } from '../../app/mods/_lib/history.js';

const $ = (s) => document.querySelector(s);
const freshDetail = () => ({ tab: 'history', openStamp: null, bodies: new Map(), files: null, openFile: null, diff: null, slots: null });
const S = { ui: loadUi(), snap: null, sel: null, collapsed: new Set(['landed', 'merged']), refreshing: false, loadSince: null, detail: freshDetail() };
// Later phases register here: detailSlots (Phase 2/3/4), onBoard, onRun (Phase 2), headExtra, cardLine and
// boardOverride (Phase 4: the Batch page replaces the Mine board).
export const hooks = { detailSlots: null, onBoard: [], headExtra: null, cardLine: null, boardOverride: null };

const selectedPr = () => (S.sel ? S.snap?.prs.find((p) => cardId(p) === S.sel) ?? null : null);

function setUi(ui) { S.ui = ui; saveUi(ui); render(); }
export const ui = () => S.ui;

function applyTheme() {
  const dark = S.ui.theme === 'dark' || (S.ui.theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  if (S.ui.theme === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = S.ui.theme;
  const canvas = getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim();
  document.querySelector('meta[name="theme-color"]').content = canvas;
  const label = `Theme: ${S.ui.theme}${S.ui.theme === 'auto' ? (dark ? ' (dark)' : ' (light)') : ''}`;
  $('#themeBtn').title = label;
  $('#themeBtn').setAttribute('aria-label', label);
}

export function openPr(pr, tab = null) {
  S.pendingTab = tab;
  location.hash = `#/pr/${pr.repoSlug}/${pr.number}`;
}

function routeFromHash() {
  const m = /^#\/pr\/([^/]+\/[^/]+)\/(\d+)$/.exec(location.hash);
  const next = m ? `pr-${m[1]}#${m[2]}` : null;
  if (next !== S.sel) {
    S.sel = next;
    S.detail = freshDetail();
  }
  if (S.pendingTab) { S.detail.tab = S.pendingTab; S.pendingTab = null; }
  render();
}

async function loadFiles(pr) {
  const key = `${cardId(pr)}@${pr.headSha}`;
  if (S.detail.filesKey === key) return;
  S.detail.filesKey = key;
  S.detail.files = null;
  try { S.detail.files = await getJson(`/api/files?${q({ repo: pr.repoSlug, number: pr.number })}`); }
  catch (err) { S.detail.files = { ok: false, error: { message: err.message } }; }
  render();
}

async function pickFile(pr, path) {
  S.detail.openFile = path;
  S.detail.diff = null;
  render();
  try {
    const r = await getJson(`/api/patch?${q({ repo: pr.repoSlug, number: pr.number, path })}`);
    S.detail.diff = r.ok === false ? { error: r.error?.message ?? 'Could not load the diff' }
      : r.patch == null ? { rows: null } : patchRows(r.patch, MONO.charPx);
  } catch (err) { S.detail.diff = { error: err.message }; }
  if (S.detail.openFile === path) render();
}

async function toggleRun(pr, stamp) {
  S.detail.openStamp = S.detail.openStamp === stamp ? null : stamp;
  render();
  if (!S.detail.openStamp || S.detail.bodies.get(stamp)?.ok) return;
  S.detail.bodies.delete(stamp);
  try { S.detail.bodies.set(stamp, await getJson(`/api/review?${q({ repo: pr.repoSlug, number: pr.number, stamp })}`)); }
  catch (err) { S.detail.bodies.set(stamp, { ok: false, error: { message: err.message } }); }
  render();
}

// DESIGN.md toast: popover treatment, glyph plus text, no timer. It stays until the next message
// replaces it or the reader closes it; a failure is announced (role="alert").
export function toast(msg, kind = 'none') {
  const t = $('#toast');
  t.setAttribute('role', kind === 'fail' ? 'alert' : 'status');
  t.replaceChildren(status(kind, msg, { role: 'body' }),
    h('button', { class: 'icon-btn', title: 'Dismiss', 'aria-label': 'Dismiss', onclick: () => { t.hidden = true; } }, GLYPH.fail));
  t.hidden = false;
}

function refreshNow() {
  S.refreshing = true;
  render();
  postJson('/api/refresh').catch((err) => { S.refreshing = false; toast(err.message, 'fail'); render(); });
}

function renderErrors(errors, now) {
  // The header names the first failure as the mod's does; the strip lists up to three with Retry.
  const first = errors[0];
  $('#headStatus').replaceChildren(...(first ? [
    status(errors.length > 1 ? 'fail' : errorStatus(first, now).status, errors.length > 1 ? `${errors.length} failed` : errorStatus(first, now).text),
    h('span', { class: 'why trunc' }, first.message),
  ] : []));
  $('#errors').replaceChildren(...errors.slice(0, 3).map((e) => h('div', { class: 'row' },
    status(errorStatus(e, now).status, errorStatus(e, now).text), h('span', { class: 'trunc' }, `${e.folder ? `${e.folder} on ` : ''}${e.where}: ${e.message}`))),
    ...(errors.length && !(S.snap?.prs ?? []).length ? [h('button', { class: 'btn', onclick: refreshNow }, 'Retry')] : []));
}

export function render() {
  const now = Date.now();
  const model = boardModel(S.snap, S.ui);
  for (const b of document.querySelectorAll('#tabSeg button')) {
    b.classList.toggle('on', b.dataset.tab === S.ui.tab);
    b.setAttribute('aria-selected', b.dataset.tab === S.ui.tab ? 'true' : 'false');
  }
  renderFilterBar($('#filters'), model.bar, { onAction: (a) => setUi(toggleFilter(S.ui, a)) });
  $('#mfilters').textContent = 'Filters';
  $('#mfilters').title = model.filterCount ? `${model.filterCount} filters on` : 'No filters on';
  $('#mfstate').textContent = model.filterCount ? `${model.filterCount} on` : '';
  renderErrors(S.snap?.errors ?? [], now);
  renderLoad({ head: $('#headStatus'), strip: $('#loadStrip'), bar: $('#loadProgress') },
    loadState(S.snap?.repoStates, { since: S.loadSince, now }));
  $('#headExtra').replaceChildren(...(hooks.headExtra ? [hooks.headExtra(S)].flat().filter(Boolean) : []));
  const refreshing = S.refreshing || Boolean(S.snap?.refreshing);
  $('#refresh').classList.toggle('spinning', refreshing);
  $('#refresh').disabled = refreshing;
  $('#refresh').title = refreshing ? 'Refreshing…' : 'Refresh';
  $('#updated').textContent = S.snap?.updatedAt ? `updated ${relativeTime(new Date(S.snap.updatedAt).toISOString(), now)}` : '';
  const compact = viewOf(window.innerWidth) === 'compact';
  keepScroll(document.body, () => {
    // Phase 4's Batch page replaces the Mine board in whichever container this width shows.
    const override = hooks.boardOverride?.(S, model);
    const loading = S.snap == null || Boolean(S.snap.loading && !S.snap.repos);
    if (override) {
      (compact ? $('#msections') : $('#board')).replaceChildren(override);
    } else if (compact) {
      renderSections($('#msections'), model, { loading, collapsed: S.collapsed, selectedId: S.sel, onOpen: (pr) => openPr(pr), extraLine: hooks.cardLine,
        onToggleSection: (key) => { S.collapsed.has(key) ? S.collapsed.delete(key) : S.collapsed.add(key); render(); } });
    } else {
      renderBoard($('#board'), model, { loading, tab: S.ui.tab, selectedId: S.sel, onOpen: (pr) => openPr(pr), extraLine: hooks.cardLine });
    }
    const pr = selectedPr();
    if (pr && S.detail.tab === 'files') loadFiles(pr);
    S.detail.slots = pr && hooks.detailSlots ? hooks.detailSlots(pr, S) : null;
    renderDetail($('#detail'), pr, { ...S.detail, now }, {
      onClose: () => { location.hash = ''; },
      onTab: (tab) => { S.detail.tab = tab; S.detail.openFile = null; S.detail.diff = null; render(); },
      onToggleRun: (stamp) => toggleRun(pr, stamp),
      onPick: (path) => pickFile(pr, path),
      onBack: () => { S.detail.openFile = null; S.detail.diff = null; render(); },
    });
  });
}

function boot() {
  $('#tabSeg').addEventListener('click', (e) => { const t = e.target.closest('[data-tab]'); if (t) setUi({ ...S.ui, tab: t.dataset.tab }); });
  $('#mfilters').addEventListener('click', () => $('#filters').classList.toggle('open'));
  $('#refresh').addEventListener('click', refreshNow);
  $('#themeBtn').addEventListener('click', () => { setUi({ ...S.ui, theme: { auto: 'light', light: 'dark', dark: 'auto' }[S.ui.theme] }); applyTheme(); });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
  window.addEventListener('hashchange', routeFromHash);
  window.addEventListener('resize', () => render());
  setInterval(render, 30000);
  applyTheme();
  events({
    board: (snap) => {
      S.snap = snap; if (!snap.refreshing) S.refreshing = false;
      const busy = loadState(snap.repoStates).busy;
      if (busy && S.loadSince == null) { S.loadSince = Date.now(); setTimeout(render, LOAD_DELAY_MS); }
      else if (!busy) S.loadSince = null;
      for (const fn of hooks.onBoard) fn(snap, S); render();
    },
    status: (st) => { if (S.snap) Object.assign(S.snap, st); if (!st.refreshing) S.refreshing = false; render(); },
  });
  routeFromHash();
}

boot();
