// Header status, filters, lanes and the compact list: the mockup's layout with prs/board.js's look; the compact list draws prs/compact.js rows.
import { badge, h, laneTitle, tag } from './dom.js';
import { skeletonLanes } from './load_status.js';
import { cardId } from '../../app/mods/_lib/scope.js';
import { prPresentation } from '../../app/mods/_lib/pr_status.js';

// prs/board.js renderCard's slots. `itemOf(pr)` is the PR's batch run item, or null (hooks.cardItem).
export function card(pr, { selected, onOpen, itemOf }) {
  const p = prPresentation(pr, { item: itemOf?.(pr) ?? null });
  return h('button', { class: `card${selected ? ' sel' : ''}`, id: cardId(pr), 'aria-current': selected ? 'true' : null, onclick: () => onOpen(pr) },
    h('div', { class: 'title-row' }, h('div', { class: 'title' }, p.title), ...p.tags.map((name) => tag(name))),
    h('div', { class: 'row' }, h('span', {}, p.ref), h('span', { class: 'trunc' }, p.author),
      h('span', { class: 'trunc grow branch' }, p.branch)),
    h('div', { class: 'row' }, h('span', { class: 'add' }, p.stats.additions), h('span', { class: 'del' }, p.stats.deletions),
      h('span', {}, p.stats.files)),
    p.badges.length ? h('div', { class: 'badges' }, p.badges.map(badge)) : null);
}

// prs/compact.js renderCompact's row: title and tags, then ref · author and the row badges.
export function compactRow(pr, { selected, onOpen, itemOf }) {
  const p = prPresentation(pr, { item: itemOf?.(pr) ?? null });
  return h('button', { class: `mrow${selected ? ' sel' : ''}`, id: cardId(pr), 'aria-current': selected ? 'true' : null, onclick: () => onOpen(pr) },
    h('div', { class: 'title-row' }, h('span', { class: 'title trunc' }, p.title), ...p.tags.map((name) => tag(name))),
    h('div', { class: 'meta-row' }, h('span', { class: 'trunc grow' }, `${p.ref} · ${p.author}`), ...p.row_badges.map(badge)));
}

export function renderBoard(el, model, { loading, tab, selectedId, onOpen, itemOf }) {
  if (loading) {
    el.replaceChildren(...skeletonLanes(tab));
    return;
  }
  if (!model.columns.length) {
    el.replaceChildren(h('div', { class: 'none pad' }, 'No pull requests'));
    return;
  }
  el.replaceChildren(...model.columns.map((c) => h('section', { class: 'col' },
    laneTitle(c.label, c.items.length),
    h('div', { class: 'lane', dataset: { scroll: `lane-${c.key}` } },
      c.items.length ? c.items.map((pr) => card(pr, { selected: cardId(pr) === selectedId, onOpen, itemOf }))
        : h('div', { class: 'none' }, 'None')))));
}

export function renderSections(el, model, { loading, collapsed, onToggleSection, selectedId, onOpen, itemOf }) {
  if (!model.sections.length) {
    el.replaceChildren(h('div', { class: 'none pad' }, loading ? 'Loading…' : 'No pull requests'));
    return;
  }
  el.replaceChildren(...model.sections.map((s) => h('section', { class: 'msec' },
    h('button', { 'aria-expanded': collapsed.has(s.key) ? 'false' : 'true', onclick: () => onToggleSection(s.key) },
      `${collapsed.has(s.key) ? '▸' : '▾'} ${s.label}`, h('span', { class: 'meta-text' }, String(s.count))),
    collapsed.has(s.key) ? null
      : h('div', { class: 'mlist' }, s.items.map((pr) => compactRow(pr, { selected: cardId(pr) === selectedId, onOpen, itemOf }))))));
}
