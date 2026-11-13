/**
 * otto-queue status and cancel through `exec`. Pure: argv, parse, loaders.
 * Remote calls go through `invoke` with no isolate so PATH gets ~/.local/bin.
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

const SHORT_ARGV = 64;

export function shortArgv(argv) {
  const text = Array.isArray(argv) ? argv.join(' ') : '';
  if (text.length <= SHORT_ARGV) return text;
  return `${text.slice(0, SHORT_ARGV - 1)}…`;
}

function ageSeconds(row, nowMs) {
  const t = row?.started_at ?? row?.created_at;
  if (t == null || t === '') return null;
  let thenMs;
  if (typeof t === 'number') thenMs = t < 1e12 ? t * 1000 : t;
  else thenMs = Date.parse(t);
  if (!Number.isFinite(thenMs)) return null;
  return Math.max(0, Math.round((nowMs - thenMs) / 1000));
}

export function ageText(row, nowMs) {
  const seconds = ageSeconds(row, nowMs);
  if (seconds == null) return '';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

function rowOf(raw) {
  if (raw == null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  if (!Number.isInteger(raw.id)) return null;
  return {
    id: raw.id,
    kind: raw.kind ?? null,
    entry_class: raw.entry_class ?? null,
    idem_key: raw.idem_key ?? null,
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
