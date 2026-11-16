/**
 * Queue home model: meters, per-machine overview, machine pages, navigation. Pure.
 */
import { liveCounts } from './queue.js';
import { lanesModel, RECENT_TERMINAL, staleness } from './queue_lanes.js';

const DAY_MS = 24 * 60 * 60 * 1000;
const CLASSES = Object.freeze(['heavy', 'light']);
const TERMINAL = new Set(['done', 'failed', 'cancelled', 'tempfail']);
export const METER_SEGMENT_MAX = 8;

function toMs(t) {
  if (t == null || t === '') return null;
  const ms = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * Capacity reading for one class: discrete segments at limit ≤ 8, a continuous
 * bar above that, or text only when the CLI did not send a limit.
 */
export function meterModel(running, limit) {
  const n = Math.max(0, Number(running) || 0);
  if (limit == null || limit === '') {
    return { kind: 'none', filled: n, total: null, text: `${n} running` };
  }
  const lim = Number(limit);
  if (!Number.isFinite(lim)) {
    return { kind: 'none', filled: n, total: null, text: `${n} running` };
  }
  const total = Math.max(0, Math.floor(lim));
  const filled = Math.min(n, total);
  const text = `${n}/${lim}`;
  if (lim > METER_SEGMENT_MAX) return { kind: 'bar', filled, total, text };
  return { kind: 'segments', filled, total, text };
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

function countFailedRows(rows, nowMs) {
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

// One owner for the counts-line failed-today figure: the CLI's uncapped
// failed_24h when present, else the rows, with 20+ at the recent cap.
function failedToday(failed24h, rows, nowMs) {
  if (typeof failed24h === 'number' && Number.isFinite(failed24h)) {
    return { n: failed24h, label: `${failed24h} failed today` };
  }
  const n = countFailedRows(rows, nowMs);
  if (terminalCount(rows) >= RECENT_TERMINAL) {
    return { n, label: `${RECENT_TERMINAL}+ failed today` };
  }
  return { n, label: `${n} failed today` };
}

function hostNoteOf(host) {
  if (host == null || typeof host !== 'object') return null;
  if (host.admit_heavy !== false) return null;
  return `heavy waits: ${host.reason}`;
}

function terminalCount(rows) {
  let n = 0;
  for (const row of rows ?? []) {
    if (TERMINAL.has(row.state)) n += 1;
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
    return { class: cls, ...meterModel(running, limit) };
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
  const today = failedToday(entry?.result?.failed24h, rows, nowMs);
  const truncated = terminalCount(rows) >= RECENT_TERMINAL;
  return {
    title: machineTitle(entry),
    gauges: gaugesOf(rows, entry?.result?.slots ?? null),
    counts: `${running} running · ${queued} queued · ${today.label}`,
    failed: today.n,
    truncated,
    staleness: staleCaption(stale),
    hostNote: hostNoteOf(entry?.result?.host),
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
    failed: summary.failed,
    truncated: summary.truncated,
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
  return [...local, ...rest].map((entry) => cardOf(entry, nowMs));
}

export function machinePage(entry, nowMs, opts = {}) {
  const card = cardOf(entry, nowMs);
  const rows = staleness(entry, nowMs).rows ?? [];
  return {
    summary: card,
    lanes: lanesModel(rows, nowMs, opts),
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
