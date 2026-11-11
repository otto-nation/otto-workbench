// Header status, filters, lanes and the compact list: the mockup's layout with prs/board.js's look.
import { h, sectionLabel, setChildren, status, tag } from './dom.js';
import { skeletonLanes } from './load_status.js';
import { cardId } from '../../app/mods/_lib/scope.js';
import { filterOn } from '../../app/mods/_lib/columns.js';
import { cardBadges, cardNumber, cardTags } from '../lib/view.js';

const CI = { passing: 'pass', failing: 'fail', pending: 'running' };

// _ui/ui.js ciMark: a PR's CI as a status glyph alone; no CI is nothing.
export function ciMark(ciStatus, role) {
  const kind = CI[ciStatus];
  return kind ? status(kind, null, { aria: `CI ${ciStatus}`, role }) : null;
}

function badge(b) {
  if (b.kind === 'status') return status(b.status, b.text);
  if (b.kind === 'tag') return tag(b.text);
  return h('span', { class: 'meta-text' }, b.text);
}

// `extraLine(pr)` is the slot Phase 4 fills with the batch state line; when it returns one, the
// "Batch working" badge goes (prs/board.js hideBusy).
export function card(pr, { selected, onOpen, extraLine }) {
  const line = extraLine?.(pr) ?? null;
  const badges = cardBadges(pr, { hideBusy: Boolean(line) });
  return h('button', { class: `card${selected ? ' sel' : ''}`, id: cardId(pr), 'aria-current': selected ? 'true' : null, onclick: () => onOpen(pr) },
    h('div', { class: 'title-row' }, h('div', { class: 'title' }, pr.title ?? ''), ...cardTags(pr).map((name) => tag(name))),
    h('div', { class: 'row' }, h('span', {}, cardNumber(pr)), h('span', { class: 'trunc' }, pr.author ?? ''),
      h('span', { class: 'trunc grow branch' }, pr.branch ?? '')),
    h('div', { class: 'row' }, h('span', { class: 'add' }, `+${pr.additions ?? 0}`), h('span', { class: 'del' }, `-${pr.deletions ?? 0}`),
      h('span', {}, `${pr.changedFiles ?? 0} files`),
      pr.reviewing ? h('span', { class: 'spin', title: 'Review running', 'aria-label': 'Review running' }, '⠋') : null),
    badges.length ? h('div', { class: 'badges' }, badges.map(badge)) : null,
    line);
}

export function renderFilters(el, model, ui, { onToggle }) {
  const f = ui.filters;
  const chip = (label, name, on) => h('button', { class: `chip${on ? ' on' : ''}`, 'aria-pressed': on ? 'true' : 'false', onclick: () => onToggle(name) }, label);
  const group = (label, items) => h('div', { class: 'fgroup' }, sectionLabel(label), h('div', { class: 'chips' }, items));
  const drafts = ui.tab === 'mine' ? chip('Drafts', 'mineDrafts', filterOn(f, 'mineDrafts')) : chip('Drafts', 'drafts', filterOn(f, 'drafts'));
  setChildren(el,
    group('Show', [drafts, chip('Landed', 'landed', filterOn(f, 'landed')), chip('Empty lanes', 'empty', filterOn(f, 'empty'))]),
    // The only repo filter: the board always spans every discovered repo, so the chips show whenever there is a choice.
    model.repoSlugs.length > 1
      ? group('Repo', model.repoSlugs.map((s) => chip(s.split('/').pop(), `repo:${s}`, f.repos.includes(s.toLowerCase())))) : null,
    ui.tab !== 'mine' && model.authors.length
      ? group('Author', model.authors.map((a) => chip(a, `author:${a}`, f.authors.includes(a)))) : null,
  );
}

export function renderBoard(el, model, { loading, tab, selectedId, onOpen, extraLine }) {
  if (loading) {
    el.replaceChildren(...skeletonLanes(tab));
    return;
  }
  if (!model.columns.length) {
    el.replaceChildren(h('div', { class: 'none pad' }, 'No pull requests'));
    return;
  }
  el.replaceChildren(...model.columns.map((c) => h('section', { class: 'col' },
    h('div', { class: 'lane-title' }, h('span', { class: 'label trunc' }, c.label), h('span', { class: 'count' }, String(c.items.length))),
    h('div', { class: 'lane', dataset: { scroll: `lane-${c.key}` } },
      c.items.length ? c.items.map((pr) => card(pr, { selected: cardId(pr) === selectedId, onOpen, extraLine }))
        : h('div', { class: 'none' }, 'None')))));
}

export function renderSections(el, model, { loading, collapsed, onToggleSection, selectedId, onOpen, extraLine }) {
  if (!model.sections.length) {
    el.replaceChildren(h('div', { class: 'none pad' }, loading ? 'Loading…' : 'No pull requests'));
    return;
  }
  el.replaceChildren(...model.sections.map((s) => h('section', { class: 'msec' },
    h('button', { 'aria-expanded': collapsed.has(s.key) ? 'false' : 'true', onclick: () => onToggleSection(s.key) },
      `${collapsed.has(s.key) ? '▸' : '▾'} ${s.label}`, h('span', { class: 'meta-text' }, String(s.count))),
    collapsed.has(s.key) ? null
      : h('div', { class: 'lane' }, s.items.map((pr) => card(pr, { selected: cardId(pr) === selectedId, onOpen, extraLine }))))));
}
