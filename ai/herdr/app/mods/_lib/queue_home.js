/**
 * Queue home model: slot gauges, per-machine summary, collapse. Pure.
 */
import { liveCounts } from './queue.js';
import { staleness } from './queue_lanes.js';

const DAY_MS = 24 * 60 * 60 * 1000;
const CLASSES = Object.freeze(['heavy', 'light']);
const FULL = '█';
const EMPTY = '░';
const GAUGE_CAP = 8;

function toMs(t) {
  if (t == null || t === '') return null;
  const ms = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * Static bar of one glyph per slot, then `n/limit`. A null limit is `n running`.
 * More than GAUGE_CAP slots drops the bar and keeps `n/limit`.
 */
export function slotGauge(running, limit) {
  const n = Math.max(0, Number(running) || 0);
  if (limit == null || limit === '') return `${n} running`;
  const lim = Number(limit);
  if (!Number.isFinite(lim)) return `${n} running`;
  if (lim > GAUGE_CAP) return `${n}/${lim}`;
  const cells = Math.max(0, Math.floor(lim));
  const filled = Math.min(n, cells);
  const bar = FULL.repeat(filled) + EMPTY.repeat(cells - filled);
  const text = `${n}/${lim}`;
  return bar === '' ? text : `${bar} ${text}`;
}

function runningByClass(rows) {
  const counts = Object.create(null);
  for (const cls of CLASSES) counts[cls] = 0;
  for (const row of rows ?? []) {
    if (row.state !== 'running') continue;
    const cls = row.entry_class;
    if (cls == null || cls === '') continue;
    counts[cls] = (counts[cls] ?? 0) + 1;
  }
  return counts;
}

function failedToday(rows, nowMs) {
  let n = 0;
  for (const row of rows ?? []) {
    if (row.state !== 'failed') continue;
    const at = toMs(row.finished_at);
    if (at == null) continue;
    const age = nowMs - at;
    if (age >= 0 && age <= DAY_MS) n += 1;
  }
  return n;
}

function classNames(slots) {
  const names = [...CLASSES];
  if (slots == null || typeof slots !== 'object') return names;
  for (const key of Object.keys(slots)) {
    if (!names.includes(key)) names.push(key);
  }
  return names;
}

function gaugesOf(rows, slots) {
  const counted = runningByClass(rows);
  return classNames(slots).map((cls) => {
    const rec = slots?.[cls];
    const running = rec != null && rec.running != null ? rec.running : (counted[cls] ?? 0);
    const limit = rec != null && rec.limit != null ? rec.limit : null;
    return { class: cls, text: slotGauge(running, limit) };
  });
}

function machineTitle(entry) {
  if (typeof entry?.title === 'string' && entry.title !== '') return entry.title;
  if (entry?.on == null || entry.on === '') return 'This computer';
  return String(entry.on);
}

export function machineSummary(entry, nowMs) {
  const stale = staleness(entry, nowMs);
  const rows = stale.rows ?? [];
  const { queued, running } = liveCounts(rows);
  const failed = failedToday(rows, nowMs);
  return {
    title: machineTitle(entry),
    gauges: gaugesOf(rows, entry?.result?.slots ?? null),
    counts: `${running} running · ${queued} queued · ${failed} failed today`,
    staleness: stale,
    hostNote: null,
  };
}

function machineKey(entry) {
  if (entry?.key != null) return String(entry.key);
  if (entry?.on == null || entry.on === '') return '';
  return String(entry.on);
}

function overrideCollapsed(collapsed, key) {
  if (collapsed == null || typeof collapsed !== 'object') return null;
  if (!Object.prototype.hasOwnProperty.call(collapsed, key)) return null;
  return Boolean(collapsed[key]);
}

function defaultOpen(rows, nowMs) {
  const { queued, running } = liveCounts(rows);
  if (queued > 0 || running > 0) return true;
  return failedToday(rows, nowMs) > 0;
}

export function homeModel(machines, nowMs, collapsed) {
  const local = [];
  const rest = [];
  for (const entry of machines ?? []) {
    if (machineKey(entry) === '') local.push(entry);
    else rest.push(entry);
  }
  return [...local, ...rest].map((entry) => {
    const key = machineKey(entry);
    const summary = machineSummary({ ...entry, title: machineTitle(entry) }, nowMs);
    const forced = overrideCollapsed(collapsed, key);
    const open = forced == null ? defaultOpen(summary.staleness.rows, nowMs) : !forced;
    return { key, summary, open };
  });
}

// toggleCollapsed flips what the reader sees: `open` is the block's visible state,
// so the stored override (true = collapsed) is right even for a machine whose
// default was decided by homeModel and has no entry in the map yet.
export function toggleCollapsed(map, key, open) {
  const next = { ...(map ?? {}) };
  next[key] = Boolean(open);
  return next;
}
