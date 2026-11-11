/**
 * Delayed loading line, progress, and per-repo strip from boardState.repoStates.
 * Pure; no imports.
 */

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
