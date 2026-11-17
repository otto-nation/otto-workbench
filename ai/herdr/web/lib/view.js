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

// The PR's GitHub page is the shared _lib/pr_link.js; re-exported for the page's callers.
export { prUrl } from '../../app/mods/_lib/pr_link.js';

// prs/files_view.js order: people's files first, generated ones after.
export function sortedFiles(files) {
  return [...files.filter((f) => !f.generated), ...files.filter((f) => f.generated)];
}
