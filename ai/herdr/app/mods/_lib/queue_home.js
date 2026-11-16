/**
 * Queue home model: slot gauges, per-machine overview, machine pages, navigation. Pure.
 */
import { liveCounts } from './queue.js';
import { filterRows, lanesModel, staleness, summary as rowsSummary } from './queue_lanes.js';

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

function staleCaption(stale) {
  return { kind: stale.kind, words: stale.words };
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
    staleness: staleCaption(stale),
    hostNote: null,
  };
}

function machineKey(entry) {
  if (entry?.key != null) return String(entry.key);
  if (entry?.on == null || entry.on === '') return '';
  return String(entry.on);
}

function cardOf(entry, nowMs) {
  const key = machineKey(entry);
  const summary = machineSummary({ ...entry, title: machineTitle(entry) }, nowMs);
  return {
    key,
    title: summary.title,
    staleness: summary.staleness,
    gauges: summary.gauges,
    counts: summary.counts,
    hostNote: summary.hostNote,
  };
}

export function homeModel(machines, nowMs) {
  const local = [];
  const rest = [];
  for (const entry of machines ?? []) {
    if (machineKey(entry) === '') local.push(entry);
    else rest.push(entry);
  }
  return [...local, ...rest].map((entry) => {
    const card = cardOf(entry, nowMs);
    // queue/main.js still reads block.summary until the view half; delete with it.
    return { ...card, summary: machineSummary({ ...entry, title: card.title }, nowMs) };
  });
}

const FILTERS = new Set(['all', 'live', 'failed']);

export function machinePage(entry, nowMs, filter) {
  const value = FILTERS.has(filter) ? filter : 'all';
  const card = cardOf(entry, nowMs);
  const rows = staleness(entry, nowMs).rows ?? [];
  return {
    summary: card,
    filter: value,
    filterSummary: rowsSummary(rows),
    lanes: lanesModel(filterRows(rows, value), nowMs),
  };
}

export function selectMachine(state, key) {
  return { ...(state ?? {}), machine: key ?? null };
}

export function clearMachine(state) {
  return { ...(state ?? {}), machine: null };
}

export function pageOf(state, machines) {
  const key = state?.machine;
  if (key == null) return null;
  const want = String(key);
  for (const entry of machines ?? []) {
    if (machineKey(entry) === want) return entry;
  }
  return null;
}

// Compatibility for queue/main.js until the view half; delete with it.
export function toggleCollapsed(map, key, open) {
  const next = { ...(map ?? {}) };
  next[key] = Boolean(open);
  return next;
}
