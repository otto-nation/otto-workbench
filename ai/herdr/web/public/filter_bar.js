// Compact filter bar: facets, tags, Clear. State lives in _lib/filters.js; this file only draws.
import { h, setChildren, sectionLabel } from './dom.js';
import { searchOptions } from '../../app/mods/_lib/filters.js';

const state = { open: null, query: '', focusSearch: false };
let bound = false;
let last = null;

function bind() {
  if (bound) return;
  bound = true;
  document.addEventListener('click', (e) => {
    if (!state.open) return;
    const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
    if (path.some((n) => n?.classList?.contains('facet'))) return;
    closePopover(false);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !state.open) return;
    e.preventDefault();
    closePopover(true);
  });
}

function closePopover(refocus) {
  const key = state.open;
  state.open = null;
  state.query = '';
  state.focusSearch = false;
  if (!last) return;
  renderFilterBar(last.el, last.model, last.opts);
  if (refocus && key) last.el.querySelector(`[data-facet="${key}"] .facet-btn`)?.focus();
}

function row(onAction, { name, on, action, option = null, hint = null, count = null }) {
  const excluded = option?.state === 'exclude';
  const checked = option ? option.state === 'include' : on;
  const n = count ?? option?.count;
  return h('div', { class: excluded ? 'frow ex' : 'frow' },
    h('label', {},
      h('input', {
        class: 'cb',
        type: 'checkbox',
        checked,
        onchange: () => onAction(option ? option.include : action),
      }),
      h('span', { class: 'name trunc' }, name),
      n != null ? h('span', { class: 'meta-text' }, String(n)) : null,
      hint ? h('span', { class: 'meta-text' }, hint) : null),
    option
      ? h('button', {
        class: 'btn ghost',
        type: 'button',
        'aria-pressed': excluded,
        onclick: () => onAction(option.exclude),
      }, excluded ? 'Unhide' : 'Hide')
      : null);
}

function showBody(f, onAction) {
  const rows = f.rows.map((r) => row(onAction, { name: r.label, on: r.on, action: r.action }));
  if (!f.bots) return rows;
  return [
    ...rows,
    h('div', { class: 'fsep' }),
    row(onAction, {
      name: f.bots.label,
      on: f.bots.on,
      action: f.bots.action,
      hint: f.bots.hint,
    }),
  ];
}

function valueBody(f, onAction) {
  const kids = [];
  if (f.search) {
    kids.push(h('input', {
      class: 'input',
      type: 'search',
      value: state.query,
      placeholder: `Search ${f.label.toLowerCase()}s`,
      oninput: (e) => {
        state.query = e.target.value;
        if (last) renderFilterBar(last.el, last.model, last.opts);
      },
    }));
  }
  const groups = f.groups?.length
    ? f.groups
    : [{ label: null, options: f.options ?? [] }];
  const listKids = [];
  for (const g of groups) {
    const opts = searchOptions(g.options, state.query);
    if (!opts.length) continue;
    if (g.label) {
      listKids.push(h('div', { class: 'fsep' }));
      listKids.push(sectionLabel(g.label));
    }
    for (const o of opts) {
      listKids.push(row(onAction, {
        name: o.label,
        on: o.state === 'include',
        action: o.include,
        option: o,
        count: o.count,
      }));
    }
  }
  kids.push(h('div', { class: 'flist' }, ...listKids));
  return kids;
}

function facet(f, onAction) {
  const open = state.open === f.key;
  const btn = h('button', {
    class: 'btn facet-btn',
    type: 'button',
    'aria-haspopup': 'dialog',
    'aria-expanded': open ? 'true' : 'false',
    'aria-label': f.active ? `${f.label}, filtered` : f.label,
    onclick: () => {
      if (state.open === f.key) {
        state.open = null;
        state.query = '';
        state.focusSearch = false;
      } else {
        state.open = f.key;
        state.query = '';
        state.focusSearch = Boolean(f.search);
      }
      if (last) renderFilterBar(last.el, last.model, last.opts);
    },
  },
  f.label,
  f.active ? h('span', { class: 'dot', 'aria-hidden': 'true' }, '•') : null,
  h('span', { class: 'caret' }, '▾'));
  return h('div', { class: 'facet', 'data-facet': f.key },
    btn,
    open
      ? h('div', { class: 'fpop', role: 'dialog' },
        ...(f.key === 'show' ? showBody(f, onAction) : valueBody(f, onAction)))
      : null);
}

export function renderFilterBar(el, model, { onAction, trailing = null } = {}) {
  bind();
  last = { el, model, opts: { onAction, trailing } };
  const keepSearch = el.contains(document.activeElement)
    && document.activeElement.matches?.('input.input');
  const tags = h('div', { class: 'ftags' },
    (model.tags ?? []).map((t) => h('button', {
      class: t.excluded ? 'ftag ex' : 'ftag',
      type: 'button',
      'aria-label': `Remove ${t.text}`,
      onclick: () => onAction(t.action),
    }, t.text, h('span', { class: 'x' }, '×'))));
  const clear = model.clear
    ? h('button', { class: 'btn ghost', type: 'button', onclick: () => onAction(model.clear) }, 'Clear filters')
    : null;
  setChildren(el,
    (model.facets ?? []).map((f) => facet(f, onAction)),
    tags,
    h('div', { class: 'grow' }),
    trailing,
    clear);
  if (keepSearch || state.focusSearch) {
    el.querySelector('input.input')?.focus();
    state.focusSearch = false;
  }
}
