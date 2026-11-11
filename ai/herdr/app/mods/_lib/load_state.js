/**
 * Delayed loading line, progress, and per-repo strip from boardState.repoStates.
 * Pure; imports history.js.
 */
import { relativeTime } from './history.js';

export const LOAD_DELAY_MS = 1000;

const BUSY = new Set(['queued', 'loading', 'hydrating']);
const SETTLED = new Set(['loaded', 'failed', 'stale']);
const STRIP_KIND = {
  queued: 'none',
  loading: 'running',
  hydrating: 'running',
  stale: 'warn',
  failed: 'fail',
};

function nameOf(row) {
  const slug = row?.slug;
  if (slug) {
    const last = String(slug).split('/').filter(Boolean).pop();
    if (last) return last;
  }
  return row?.folder ?? '';
}

export function loadState(repoStates, { since = null, now = Date.now(), delayMs = LOAD_DELAY_MS } = {}) {
  const rows = repoStates ?? [];
  const total = rows.length;
  let loading = 0;
  let settled = 0;
  let busy = false;
  let queuedOrLoading = false;
  for (const row of rows) {
    const phase = row?.phase;
    if (BUSY.has(phase)) busy = true;
    if (phase === 'queued' || phase === 'loading') {
      loading++;
      queuedOrLoading = true;
    }
    if (SETTLED.has(phase)) settled++;
  }
  const shown = busy && since != null && now - since >= delayMs;
  const line = shown
    ? (queuedOrLoading
      ? `Loading ${total - loading} of ${total} repos`
      : `Updating CI and review status · ${settled} of ${total} repos`)
    : null;
  const progress = shown ? Math.round((100 * settled) / total) : null;
  const strip = [];
  if (total > 1) {
    for (const row of rows) {
      const phase = row?.phase;
      const kind = STRIP_KIND[phase];
      if (kind == null) continue;
      if (BUSY.has(phase) && !shown) continue;
      strip.push({ name: nameOf(row), kind });
    }
  }
  return { busy, shown, line, progress, strip };
}

// DESIGN.md: stale is warn plus how old the shown data is; with nothing loaded yet it is an error.
export function errorStatus(error, nowMs) {
  if (error?.updatedAt != null) return { kind: 'status', status: 'warn', text: `stale · ${relativeTime(new Date(error.updatedAt).toISOString(), nowMs)}` };
  return { kind: 'status', status: 'fail', text: 'failed' };
}
// The header's status for a board's load errors (scope.js boardState / snapshot errors): one reads as
// errorStatus, several as `N failed`; null with none.
export function errorsStatus(errors, nowMs) {
  const list = errors ?? [];
  if (!list.length) return null;
  return list.length === 1 ? errorStatus(list[0], nowMs) : { kind: 'status', status: 'fail', text: `${list.length} failed` };
}
