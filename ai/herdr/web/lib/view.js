/**
 * The board page's view model: _lib/board_model.js boardModel (shared with the herdr-app mod) for the
 * page's UI state, plus the page-only helpers below. Pure; runs in the browser.
 */
import { DEFAULT_FILTERS } from '../../app/mods/_lib/columns.js';
import { boardModel as sharedModel } from '../../app/mods/_lib/board_model.js';
import { filterAction, normalizeFilters } from '../../app/mods/_lib/filters.js';

// Flag defaults are _lib's DEFAULT_FILTERS (one owner for the mod and the web), never restated here.
// There is no repo scope: the board always covers every repo the server discovers (the mod's
// "All spaces"), and the filter bar is the only repo filter.
export const DEFAULT_UI = Object.freeze({
  tab: 'team',
  theme: 'auto',
  filters: Object.freeze(normalizeFilters(DEFAULT_FILTERS)),
});

export function boardModel(snapshot, ui) {
  const model = sharedModel(snapshot, { tab: ui.tab, filters: ui.filters, scope: 'all' });
  return { ...model, authors: model.options.authors };
}

export function toggleFilter(ui, name) {
  const next = filterAction(ui.filters, name);
  return next === ui.filters ? ui : { ...ui, filters: next };
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
