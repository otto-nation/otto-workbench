/**
 * The board page's view model: which lanes, cards and states to draw for a snapshot and the page's UI
 * state, through the same _lib/columns.js functions prs/board.js uses, with the mod's DESIGN.md
 * readings (prs/board.js badges, prs/detail.js postKind, prs/files_view.js fileMarkColor, the header's
 * stale/failed). Pure; runs in the browser.
 */
import {
  TEAM_COLUMNS, MINE_COLUMNS, teamColumn, mineColumn, filterPrs, filterRepos, isMine, groupColumns, visibleColumns,
  DEFAULT_FILTERS, filterOn,
} from '../../app/mods/_lib/columns.js';
import { compactSections } from '../../app/mods/_lib/compact.js';
import { relativeTime } from '../../app/mods/_lib/history.js';

// DESIGN.md status glyphs, as _ui/ui.js STATUS. Fail is × (U+00D7): no brand font draws U+2717.
export const GLYPH = Object.freeze({ pass: '✓', fail: '×', running: '…', warn: '!', none: '·' });

// Flag defaults are _lib's DEFAULT_FILTERS (one owner for the mod and the web; drafts hidden on Team,
// Mine and the Batch form), never restated here. There is no repo scope: the board always covers every
// repo the server discovers (the mod's "All spaces"), and the Repo chips are the only repo filter.
export const DEFAULT_UI = Object.freeze({
  tab: 'team',
  theme: 'auto',
  filters: Object.freeze({ ...DEFAULT_FILTERS, authors: Object.freeze([]), repos: Object.freeze([]) }),
});

const FLAGS = new Set(Object.keys(DEFAULT_FILTERS));

// Chips off default (filterOn), plus selected authors/repos. Drafts is per-tab: Team counts
// `drafts`, Mine counts `mineDrafts`. The other tab's chip is not on the page.
function activeFilterCount(filters, tab) {
  const skip = tab === 'mine' ? 'drafts' : 'mineDrafts';
  let n = filters.authors.length + filters.repos.length;
  for (const key of FLAGS) {
    if (key !== skip && filterOn(filters, key)) n += 1;
  }
  return n;
}

// The viewer's PRs on Mine, as prs/board.js renderBoard: isMine, and drafts only with the Mine Drafts chip.
// With drafts hidden (the default) Mine's Draft lane is empty, so hidden unless Empty lanes; intended.
export function mineList(prs, me, filters) {
  return prs.filter((p) => isMine(p, me) && (filterOn(filters, 'mineDrafts') || !p.isDraft));
}

export function boardModel(snapshot, ui) {
  const prs = snapshot?.prs ?? [];
  const me = snapshot?.me ?? '';
  const f = ui.filters;
  const base = filterRepos(prs, f.repos, snapshot?.repoSlugs);
  const mine = ui.tab === 'mine';
  const list = mine ? mineList(base, me, f) : filterPrs(base, f, me);
  const grouped = groupColumns(list, mine ? mineColumn : teamColumn, mine ? MINE_COLUMNS : TEAM_COLUMNS);
  const meLower = me.toLowerCase();
  return {
    columns: visibleColumns(grouped, filterOn(f, 'empty')),
    sections: compactSections(grouped),
    authors: [...new Set(prs.map((p) => String(p.author ?? '').toLowerCase()).filter((a) => a && a !== meLower))].sort(),
    repoSlugs: [...(snapshot?.repoSlugs ?? [])].sort(),
    filterCount: activeFilterCount(f, ui.tab),
  };
}

const toggleIn = (list, value) => (list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);

export function toggleFilter(ui, name) {
  const f = ui.filters;
  if (FLAGS.has(name)) {
    // A stored boolean wins, else _lib's default (filterOn), as the desktop board reads it.
    const now = filterOn(f, name);
    return { ...ui, filters: { ...f, [name]: !now } };
  }
  if (name.startsWith('repo:')) return { ...ui, filters: { ...f, repos: toggleIn(f.repos, name.slice(5).toLowerCase()) } };
  if (name.startsWith('author:')) return { ...ui, filters: { ...f, authors: toggleIn(f.authors, name.slice(7).toLowerCase()) } };
  return ui;
}

// prs/board.js badges(): states are coloured text with a glyph, labels are tags, nothing is a pill.
// hideBusy: the card shows the batch state line instead (phase 4).
export function cardBadges(pr, { hideBusy = false } = {}) {
  const out = [];
  if (pr.isDraft) out.push({ kind: 'text', text: 'Draft' });
  if (pr.state === 'MERGED') out.push({ kind: 'status', status: 'pass', text: 'Merged' });
  if (pr.inMergeQueue) out.push({ kind: 'status', status: 'running', text: 'In merge queue' });
  if (pr.busy && !hideBusy) out.push({ kind: 'status', status: 'running', text: 'Batch working' });
  const runs = (pr.reviewRuns ?? []).length;
  if (runs > 0) out.push({ kind: 'text', text: `${runs} reviews` });
  for (const name of (pr.labels ?? []).slice(0, 2)) out.push({ kind: 'tag', text: name });
  return out;
}

// Every card names its repo, since the board always spans every discovered repo: "kit #4". (The
// desktop card writes "kit#4", and only under All spaces.)
export function cardNumber(pr) {
  return pr.repoSlug ? `${pr.repoSlug.split('/').pop()} #${pr.number}` : `#${pr.number}`;
}

export function decisionText(decision) {
  return decision ? String(decision).toLowerCase().replace(/_/g, ' ') : '';
}

// prs/detail.js postKind: a history row's post state as a status kind.
export function postKind(tone) {
  if (tone === 'success') return 'pass';
  if (tone === 'warning') return 'warn';
  return 'none';
}

// historyRows health: 'ok' shows nothing.
export function healthKind(health) {
  if (health === 'Partial') return 'warn';
  if (health === 'Error') return 'fail';
  return null;
}

// prs/files_view.js fileMarkColor: A in done, D in danger, the rest muted (CSS classes add/del/mod).
export function fileMark(status) {
  if (status === 'A') return 'add';
  if (status === 'D') return 'del';
  return 'mod';
}

// DESIGN.md: stale is warn plus how old the shown data is; with nothing loaded yet it is an error.
export function errorStatus(error, nowMs) {
  if (error?.updatedAt != null) {
    return { status: 'warn', text: `stale · ${relativeTime(new Date(error.updatedAt).toISOString(), nowMs)}` };
  }
  return { status: 'fail', text: 'failed' };
}

// The PR's GitHub page; only a credential-free https URL is ever put in an href.
function httpsUrl(raw) {
  if (typeof raw !== 'string' || /\s/.test(raw)) return '';
  try {
    const u = new URL(raw);
    if (u.protocol !== 'https:' || u.username || u.password) return '';
    return raw;
  } catch {
    return '';
  }
}

function hostName(host) {
  const raw = String(host || 'github.com');
  try {
    const u = new URL(`https://${raw}`);
    if (u.username || u.password || u.port || u.pathname !== '/' || u.search || u.hash) return 'github.com';
    if (u.hostname !== raw.toLowerCase()) return 'github.com';
    return u.hostname;
  } catch {
    return 'github.com';
  }
}

export function prUrl(pr) {
  if (typeof pr.url === 'string') {
    const ok = httpsUrl(pr.url);
    if (ok) return ok;
  }
  if (!pr.repoSlug || !pr.number) return '';
  return `https://${hostName(pr.host)}/${pr.repoSlug}/pull/${pr.number}`;
}

// prs/files_view.js order: people's files first, generated ones after.
export function sortedFiles(files) {
  return [...files.filter((f) => !f.generated), ...files.filter((f) => f.generated)];
}
