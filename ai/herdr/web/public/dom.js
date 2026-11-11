// The page's DOM builder: text always goes in as text. The one exception is trustedMarkdown.
import { GLYPH } from '../lib/view.js';

// replaceChildren stringifies null/false to "null"/"false"; drop them like h() does.
export function setChildren(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((c) => c != null && c !== false));
  return el;
}

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props ?? {})) {
    if (v == null || v === false) continue;
    if (k === 'style') continue;
    if (k.startsWith('on')) {
      if (typeof v === 'function') el.addEventListener(k.slice(2), v);
      continue;
    }
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// DESIGN.md status: coloured text plus a glyph; without a label the glyph stands alone and aria names it.
export function status(kind, label, { aria, role, wide } = {}) {
  const k = GLYPH[kind] ? kind : 'none';
  return h('span', { class: `status ${k}${role === 'body' ? ' body' : ''}${wide ? ' wide' : ''}`, 'aria-label': label ? null : (aria ?? k) },
    h('span', { 'aria-hidden': 'true' }, GLYPH[k]), label ?? null);
}

export function tag(text) {
  return h('span', { class: 'tag' }, text);
}

export function sectionLabel(text, count = null) {
  return h('div', { class: 'section-label' }, text, count == null ? null : h('span', { class: 'count' }, String(count)));
}

// lib/markdown.js escapes its whole input before any rule runs; nothing else may reach innerHTML.
export function trustedMarkdown(el, html) {
  el.innerHTML = html;
  return el;
}

// Re-render without losing where the reader had scrolled: elements carrying data-scroll keep it.
export function keepScroll(root, render) {
  const at = new Map([...root.querySelectorAll('[data-scroll]')].map((e) => [e.dataset.scroll, e.scrollTop]));
  render();
  for (const e of root.querySelectorAll('[data-scroll]')) if (at.has(e.dataset.scroll)) e.scrollTop = at.get(e.dataset.scroll);
}
