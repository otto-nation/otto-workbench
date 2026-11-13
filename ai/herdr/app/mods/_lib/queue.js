/**
 * otto-queue status, cancel and log tails through `exec`. Pure: argv, parse, labels, loaders.
 * A null `on` is this machine. Remote calls go through `invoke` with no isolate so PATH gets ~/.local/bin.
 *
 * loadStatus shapes:
 *   {ok:true, busy:true, rows:[]}  otto-queue exited 75 (ledger busy; retry next poll)
 *   {ok:true, busy:false, rows}    parsed NDJSON rows
 *   {ok:false, error}              any other failure
 */

import { invoke, readJson } from './board_data.js';

export function statusArgv() {
  return ['status', '--json'];
}

export function cancelArgv(id) {
  return ['cancel', String(id)];
}

export const LIVE_STATES = Object.freeze(['queued', 'running']);

export function isLive(state) {
  return state === 'queued' || state === 'running';
}

const STATE_KIND = Object.freeze({
  queued: 'none',
  running: 'running',
  done: 'pass',
  failed: 'fail',
  cancelled: 'none',
  tempfail: 'warn',
});

// Unknown states read as none, same as statusOf.
export function stateKind(state) {
  return STATE_KIND[state] ?? 'none';
}

export function liveCounts(rows) {
  let queued = 0;
  let running = 0;
  for (const row of rows ?? []) {
    if (row.state === 'queued') queued += 1;
    else if (row.state === 'running') running += 1;
  }
  return { queued, running };
}

// A review key is `review:` plus a SHA-256: opaque, so it is no title.
const OPAQUE_KEY = /^review:[0-9a-f]{64}$/;

function basename(path) {
  const parts = String(path).split('/');
  return parts[parts.length - 1] || String(path);
}

/**
 * The row's title: its label when the ledger carries one, else a readable queue key, else the
 * command with argv[0] cut to its basename. Never truncated here; the view's ellipsis is the
 * only cut.
 */
export function rowTitle(row) {
  if (typeof row?.label === 'string' && row.label.trim() !== '') return row.label;
  const key = row?.idem_key;
  if (typeof key === 'string' && key !== '' && !OPAQUE_KEY.test(key)) return key;
  const argv = Array.isArray(row?.argv) ? row.argv : [];
  if (!argv.length) return `#${row?.id ?? ''}`;
  return [basename(argv[0]), ...argv.slice(1)].join(' ');
}

// The full command, for the expanded row.
export function commandText(argv) {
  return Array.isArray(argv) ? argv.join(' ') : '';
}

function spanText(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

function toMs(t) {
  if (t == null || t === '') return null;
  const ms = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * The row's second line: `#id · class · key`, then why it ended or when it retries, the way
 * `otto-queue status` prints it. The key is left out when it is already the title.
 */
export function rowMeta(row, nowMs) {
  const parts = [`#${row.id}`];
  if (row.entry_class) parts.push(row.entry_class);
  const key = row.idem_key;
  if (key && rowTitle(row) !== key) parts.push(OPAQUE_KEY.test(key) ? 'review' : key);
  if (row.exit_status != null && row.exit_status !== 0) parts.push(`exit ${row.exit_status}`);
  const at = toMs(row.not_before);
  if (row.state === 'queued' && at != null && at > nowMs) {
    parts.push(`retry in ${spanText(Math.round((at - nowMs) / 1000))}`, `attempt ${(row.attempts ?? 0) + 1}`);
  }
  return parts.join(' · ');
}

// When the entry was made, started and finished, as clock times; absent ones are left out.
export function timesText(row) {
  const out = [];
  for (const [name, t] of [['created', row.created_at], ['started', row.started_at], ['finished', row.finished_at]]) {
    const ms = toMs(t);
    if (ms != null) out.push(`${name} ${new Date(ms).toLocaleTimeString()}`);
  }
  return out.join(' · ');
}

export function logTailArgv(path, n = 80) {
  return ['tail', '-n', String(n), path];
}

// The last lines of an entry's log on its machine; {ok:true, text} or {ok:false, error}.
export async function loadLog(exec, ctx, path) {
  const argv = logTailArgv(path);
  const ran = await invoke(exec, ctx, argv[0], argv.slice(1));
  return ran.ok ? { ok: true, text: ran.stdout } : ran;
}

export function ageText(row, nowMs) {
  const thenMs = toMs(row?.started_at ?? row?.created_at);
  if (thenMs == null) return '';
  return spanText(Math.max(0, Math.round((nowMs - thenMs) / 1000)));
}

function rowOf(raw) {
  if (raw == null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  if (!Number.isInteger(raw.id)) return null;
  return {
    id: raw.id,
    kind: raw.kind ?? null,
    entry_class: raw.entry_class ?? null,
    idem_key: raw.idem_key ?? null,
    label: raw.label ?? null,
    argv: Array.isArray(raw.argv) ? raw.argv.map(String) : [],
    cwd: raw.cwd ?? null,
    env_allow: Array.isArray(raw.env_allow) ? raw.env_allow : [],
    state: raw.state ?? null,
    attempts: raw.attempts ?? 0,
    exit_status: raw.exit_status ?? null,
    log_path: raw.log_path ?? null,
    result: raw.result ?? null,
    pid: raw.pid ?? null,
    created_at: raw.created_at ?? null,
    started_at: raw.started_at ?? null,
    finished_at: raw.finished_at ?? null,
    not_before: raw.not_before ?? null,
  };
}

// One JSON object per line, like readPages, but a bad line or non-integer id is skipped.
export function parseStatus(stdout) {
  const rows = [];
  for (const line of String(stdout ?? '').split('\n')) {
    if (line.trim() === '') continue;
    const parsed = readJson(line);
    if (!parsed.ok) continue;
    const row = rowOf(parsed.value);
    if (row == null) continue;
    rows.push(row);
  }
  return rows;
}

// invoke maps a 75 with empty stderr to kind failed, message "exit 75".
function isBusyError(error) {
  if (error == null || error.kind !== 'failed') return false;
  const msg = String(error.message ?? '');
  return msg === 'exit 75' || /(^|\s)exit 75(\s|$)/.test(msg);
}

export async function loadStatus(exec, ctx) {
  const ran = await invoke(exec, ctx, 'otto-queue', statusArgv());
  if (!ran.ok) {
    if (isBusyError(ran.error)) return { ok: true, busy: true, rows: [] };
    return ran;
  }
  return { ok: true, busy: false, rows: parseStatus(ran.stdout) };
}

export async function cancelEntry(exec, ctx, id) {
  return invoke(exec, ctx, 'otto-queue', cancelArgv(id));
}
