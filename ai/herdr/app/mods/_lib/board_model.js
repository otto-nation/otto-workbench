/**
 * What a PR board view draws for a board state and its UI state, decided once for the herdr-app mod
 * (prs/board_view.js, prs/board.js, prs/compact.js) and the web page (web/lib/view.js): the tab's
 * list, its lanes in the document's column order, the filter bar, facet options, chip count, the
 * Batch preset a Mine lane offers, and each lane's status: a lane whose membership needs on-demand
 * data (LANE_NEEDS) reads loading or failed until that data has landed for every repo the board shows.
 * `view` is scope.js boardState output or the web snapshot ({prs, me, repoSlugs, columns}). Pure.
 */
import {
  MINE_COLUMNS, TEAM_COLUMNS, columnsFor, filterOn, filterPrs, groupColumns, isMine, laneOf,
  visibleColumns,
} from './columns.js';
import { barModel, facetView } from './filters.js';
import { compactSections } from './compact.js';
import { columnPreset } from './batch_launch.js';
import { friendlyError } from './scope.js';

// Mine: the viewer's PRs, drafts only with Mine's Drafts chip. Team: filterPrs (drafts, landed).
export function tabList(prs, me, filters, tab) {
  if (tab === 'mine') return (prs ?? []).filter((p) => isMine(p, me) && (filterOn(filters, 'mineDrafts') || !p.isDraft));
  return filterPrs(prs ?? [], filters, me);
}

// The document's columns for the tab once one has landed; the static list labels lanes before that.
export function laneSpec(columns, tab) {
  const board = tab === 'mine' ? 'mine' : 'team';
  const fromDoc = columnsFor(columns, board);
  return fromDoc.length ? fromDoc : board === 'mine' ? MINE_COLUMNS : TEAM_COLUMNS;
}

// The viewer's open PRs, unfiltered: the Batch form applies the shared chips itself.
export function mineOpen(prs, me) {
  return (prs ?? []).filter((p) => isMine(p, me) && p.state !== 'MERGED' && p.state !== 'CLOSED');
}

// Lanes whose membership needs data read only on demand, and which data. Has Comments needs owed.
export const LANE_NEEDS = Object.freeze({ 'has-comments': 'owed' });
const LOADING = Object.freeze({ owed: 'Loading comments…' });
const FAILED = Object.freeze({ owed: 'Could not load comments' });
const READY = Object.freeze({ status: 'ready', message: null });

// The on-demand data a tab shows: what its lanes need. A host passes this to the store as want().
export function wantFor(tab) {
  const needs = new Set(laneSpec(null, tab).map((l) => LANE_NEEDS[l.key]).filter(Boolean));
  return { owed: needs.has('owed') };
}

// A lane's state from the repos' on-demand state: loading while any repo the board shows has not
// loaded it, else the first failure, else ready. A repo with its own error is the header's to show.
// The web snapshot sends the error already in words (web/lib/snapshot.js); the mod sends the error.
function needStatus(need, repoStates, scope) {
  const live = (repoStates ?? []).filter((r) => r.error == null);
  const states = live.map((r) => r[need] ?? { state: 'idle', error: null });
  if (states.some((s) => s.state === 'idle' || s.state === 'loading')) return { status: 'loading', message: LOADING[need] };
  const i = states.findIndex((s) => s.state === 'error');
  if (i < 0) return READY;
  const e = states[i].error;
  const why = typeof e === 'string' ? e : friendlyError(e, live[i].on, scope);
  const where = live.length > 1 && live[i].slug ? ` for ${live[i].slug}` : '';
  return { status: 'error', message: `${FAILED[need]}${where}: ${why}` };
}

export function boardModel(view, { tab = 'team', filters, scope = 'all' } = {}) {
  const prs = view?.prs ?? [];
  const me = view?.me ?? '';
  const mine = tab === 'mine';
  const fv = facetView(tabList(prs, me, filters, tab), filters, {
    tab, me, loadedRepos: view?.repoSlugs, authorsFrom: prs,
  });
  const spec = laneSpec(view?.columns, tab);
  const lanes = groupColumns(fv.list, (p) => laneOf(p, mine ? 'mine' : 'team'), spec).map((l) => {
    const need = mine ? LANE_NEEDS[l.key] : undefined;
    const state = need ? needStatus(need, view?.repoStates, scope) : READY;
    // A preset over a lane still loading would batch a partial set; it appears once the lane is ready.
    return { ...l, ...state, batch: mine && state.status === 'ready' ? columnPreset(l) : null };
  });
  const visible = new Set(visibleColumns(lanes, filterOn(filters, 'empty')).map((l) => l.key));
  const repoSlugs = [...(view?.repoSlugs ?? [])].sort();
  const options = { repos: scope === 'all' ? repoSlugs : [], labels: fv.options.labels, authors: fv.options.authors };
  const bar = barModel(filters, { tab, options });
  return {
    tab,
    list: fv.list,
    spec,
    lanes,
    columns: lanes.filter((l) => visible.has(l.key) || l.status !== 'ready'),
    sections: compactSections(lanes),
    options,
    bar,
    filterCount: bar.count,
    repoSlugs,
  };
}
