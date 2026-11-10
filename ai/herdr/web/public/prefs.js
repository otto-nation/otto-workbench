// Per-browser UI state (tab, filters, theme). Not shared with the desktop app's shared("prs.*").
import { DEFAULT_UI } from '../lib/view.js';

const KEY = 'pr-board.ui';

export function loadUi() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? '{}');
    // Only the keys DEFAULT_UI names: a key an earlier page stored (and nothing reads) is not saved back.
    const known = Object.fromEntries(Object.keys(DEFAULT_UI).filter((k) => k in raw).map((k) => [k, raw[k]]));
    return { ...DEFAULT_UI, ...known, filters: { ...DEFAULT_UI.filters, ...(raw.filters ?? {}) } };
  } catch {
    return { ...DEFAULT_UI };
  }
}

export function saveUi(ui) {
  localStorage.setItem(KEY, JSON.stringify(ui));
}
