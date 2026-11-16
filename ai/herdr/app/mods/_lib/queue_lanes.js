/**
 * Queue panel model: lanes, position in line, timing, failure text, filters, staleness.
 * Pure. Admission order matches otto_queue.ledger._first_waiting_id.
 */
import { LOAD_DELAY_MS } from './load_state.js';
import { isLive } from './queue.js';
import { GLYPH } from './status.js';

const FINISHED = new Set(['done', 'failed', 'cancelled', 'tempfail']);
const SUBJECT_RE = /([A-Za-z0-9._-]+\/[A-Za-z0-9._-]+#\d+)\s*$/;
const STALE_MS = 60_000;

function toMs(t) {
  if (t == null || t === '') return null;
  const ms = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
}

function spanText(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

function classRank(entryClass) {
  if (entryClass === 'heavy') return 0;
  if (entryClass === 'light') return 1;
  return 2;
}

function isAdmissible(row, jobs, nowMs) {
  if (row?.state !== 'queued') return false;
  if (row.kind === 'ticket') return true;
  if (!jobs || row.kind !== 'job') return false;
  const at = toMs(row.not_before);
  return at == null || at <= nowMs;
}

/**
 * Lowest queued id of `entryClass` that admission would take, matching
 * `_first_waiting_id`: tickets always, jobs only when `jobs` and not_before ≤ now.
 */
export function firstWaitingId(rows, entryClass, { jobs = false, nowMs } = {}) {
  let min = null;
  for (const row of rows ?? []) {
    if (row.entry_class !== entryClass) continue;
    if (!isAdmissible(row, jobs, nowMs)) continue;
    if (min == null || row.id < min) min = row.id;
  }
  return min;
}

export function subjectOf(row) {
  if (typeof row?.subject === 'string' && row.subject.trim() !== '') return row.subject.trim();
  const label = row?.label;
  if (typeof label !== 'string') return null;
  const m = label.trim().match(SUBJECT_RE);
  return m ? m[1] : null;
}

function retryText(row, nowMs) {
  const at = toMs(row?.not_before);
  if (row?.state !== 'queued' || at == null || at <= nowMs) return '';
  return `retry in ${spanText(Math.round((at - nowMs) / 1000))}`;
}

function positionText(row, eligibleByClass) {
  if (row?.state !== 'queued') return '';
  const list = eligibleByClass.get(row.entry_class) ?? [];
  const idx = list.findIndex((r) => r.id === row.id);
  if (idx === 0) return '1st in line';
  if (idx > 0) return `behind #${list[idx - 1].id}`;
  return '';
}

function timingText(row, nowMs) {
  if (row?.state === 'queued') {
    const from = toMs(row.created_at);
    if (from == null) return '';
    return `waited ${spanText(Math.max(0, Math.round((nowMs - from) / 1000)))}`;
  }
  if (row?.state === 'running') {
    const from = toMs(row.started_at);
    if (from == null) return '';
    return `ran ${spanText(Math.max(0, Math.round((nowMs - from) / 1000)))}`;
  }
  if (!FINISHED.has(row?.state)) return '';
  const started = toMs(row.started_at);
  const finished = toMs(row.finished_at);
  const parts = [];
  if (started != null && finished != null) {
    parts.push(`ran ${spanText(Math.max(0, Math.round((finished - started) / 1000)))}`);
  }
  if (finished != null) {
    parts.push(`${spanText(Math.max(0, Math.round((nowMs - finished) / 1000)))} ago`);
  }
  return parts.join(' · ');
}

function failureText(row) {
  if (row?.state === 'cancelled') return 'cancelled';
  if (row?.state === 'tempfail') {
    if (row.started_at != null && row.started_at !== '' && row.exit_status === 75) {
      return 'exited 75 (busy)';
    }
    return 'tempfail · never got a slot';
  }
  if (row?.state !== 'failed') return '';
  if (row.exit_status == null) return 'holder died';
  return `exit ${row.exit_status}`;
}

function presentRow(row, nowMs, eligibleByClass) {
  return {
    ...row,
    subject: subjectOf(row),
    position: positionText(row, eligibleByClass),
    retry: retryText(row, nowMs),
    timing: timingText(row, nowMs),
    failure: failureText(row),
  };
}

function failKey(row) {
  const subject = row.subject || subjectOf(row);
  if (subject) return `s:${subject}\n${row.action ?? ''}`;
  if (row.label) return `l:${row.label}`;
  return `id:${row.id}`;
}

export const COALESCE_MIN = 3;

function actionOf(row) {
  if (typeof row?.action === 'string' && row.action.trim() !== '') return row.action.trim();
  const argv = Array.isArray(row?.argv) ? row.argv : [];
  if (!argv.length) return '';
  const parts = String(argv[0]).split('/');
  return parts[parts.length - 1] || String(argv[0]);
}

function newerMember(a, b, field) {
  const ta = toMs(a?.[field]);
  const tb = toMs(b?.[field]);
  if (ta == null && tb == null) return (b?.id ?? 0) >= (a?.id ?? 0) ? b : a;
  if (ta == null) return b;
  if (tb == null) return a;
  if (tb !== ta) return tb > ta ? b : a;
  return (b?.id ?? 0) >= (a?.id ?? 0) ? b : a;
}

function leadField(laneKey) {
  if (laneKey === 'running') return 'started_at';
  if (laneKey === 'waiting') return 'created_at';
  return 'finished_at';
}

function leadOf(members, laneKey) {
  const field = leadField(laneKey);
  let lead = members[0];
  for (let i = 1; i < members.length; i += 1) {
    lead = newerMember(lead, members[i], field);
  }
  return lead;
}

function subjectsOf(members) {
  const seen = new Set();
  const names = [];
  for (const row of members) {
    const subject = row.subject || subjectOf(row);
    if (subject == null || subject === '' || seen.has(subject)) continue;
    seen.add(subject);
    names.push(subject);
  }
  const extra = names.length - 3;
  return {
    names: names.slice(0, 3),
    more: extra > 0 ? `+${extra} more` : '',
  };
}

function longestSpan(members, field, nowMs) {
  let max = null;
  for (const row of members) {
    const from = toMs(row[field]);
    if (from == null) continue;
    const sec = Math.max(0, Math.round((nowMs - from) / 1000));
    if (max == null || sec > max) max = sec;
  }
  return max;
}

function groupTiming(members, laneKey, nowMs) {
  if (laneKey === 'running') {
    const sec = longestSpan(members, 'started_at', nowMs);
    return sec == null ? '' : `ran ${spanText(sec)}`;
  }
  if (laneKey === 'waiting') {
    const sec = longestSpan(members, 'created_at', nowMs);
    return sec == null ? '' : `waited ${spanText(sec)}`;
  }
  const lead = leadOf(members, 'finished');
  const finished = toMs(lead?.finished_at);
  if (finished == null) return '';
  return `${spanText(Math.max(0, Math.round((nowMs - finished) / 1000)))} ago`;
}

const OUTCOME_ORDER = Object.freeze(['failed', 'done', 'cancelled', 'tempfail']);

function groupFailure(members, laneKey) {
  if (laneKey !== 'finished') return '';
  const counts = Object.create(null);
  for (const row of members) {
    const state = row.state;
    if (state == null || state === '') continue;
    counts[state] = (counts[state] ?? 0) + 1;
  }
  const parts = [];
  const seen = new Set();
  for (const state of OUTCOME_ORDER) {
    if (!counts[state]) continue;
    parts.push(`${counts[state]} ${state}`);
    seen.add(state);
  }
  for (const state of Object.keys(counts)) {
    if (seen.has(state)) continue;
    parts.push(`${counts[state]} ${state}`);
  }
  return parts.join(' · ');
}

// Repeat-failure grouping (same subject+action, failed ≥2) runs first and
// produces one `failed Nx` row. Coalesce then groups remaining rows by action
// (COALESCE_MIN). That order is the contract: coalescing first would hide the
// Nx row inside an action group of the same failures.
function coalesceRows(rows, laneKey, nowMs) {
  const groups = new Map();
  const order = [];
  for (const row of rows) {
    const action = actionOf(row);
    if (!groups.has(action)) {
      groups.set(action, []);
      order.push(action);
    }
    groups.get(action).push(row);
  }
  const out = [];
  for (const action of order) {
    const members = groups.get(action);
    if (members.length < COALESCE_MIN) {
      out.push(...members);
      continue;
    }
    out.push({
      key: `${laneKey}:${action}`,
      action,
      count: members.length,
      members,
      lead: leadOf(members, laneKey),
      subjects: subjectsOf(members),
      timing: groupTiming(members, laneKey, nowMs),
      failure: groupFailure(members, laneKey),
    });
  }
  return out;
}

function collapseFailed(rows) {
  const groups = new Map();
  const order = [];
  for (const row of rows) {
    if (row.state !== 'failed') {
      order.push({ type: 'one', row });
      continue;
    }
    const key = failKey(row);
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push({ type: 'fail', key });
    }
    groups.get(key).push(row);
  }
  const out = [];
  for (const item of order) {
    if (item.type === 'one') {
      out.push(item.row);
      continue;
    }
    const members = groups.get(item.key);
    if (members.length < 2) {
      out.push(members[0]);
      continue;
    }
    out.push({
      ...members[0],
      failure: `failed ${members.length}${GLYPH.fail} today`,
      members,
    });
  }
  return out;
}

function compareFinished(a, b) {
  const fa = toMs(a.finished_at);
  const fb = toMs(b.finished_at);
  if (fa == null && fb == null) return 0;
  if (fa == null) return 1;
  if (fb == null) return -1;
  return fb - fa || b.id - a.id;
}

function lane(key, label, rows, count = rows.length) {
  return { key, label, count, rows };
}

export function lanesModel(rows, nowMs) {
  const list = rows ?? [];
  const running = [];
  const waiting = [];
  const finished = [];
  for (const row of list) {
    if (row.state === 'running') running.push(row);
    else if (row.state === 'queued') waiting.push(row);
    else if (FINISHED.has(row.state)) finished.push(row);
  }
  waiting.sort((a, b) => classRank(a.entry_class) - classRank(b.entry_class) || a.id - b.id);
  running.sort((a, b) => a.id - b.id);
  finished.sort(compareFinished);

  const eligibleByClass = new Map();
  for (const row of waiting) {
    if (!isAdmissible(row, true, nowMs)) continue;
    const entryClass = row.entry_class;
    if (!eligibleByClass.has(entryClass)) eligibleByClass.set(entryClass, []);
    eligibleByClass.get(entryClass).push(row);
  }

  const present = (row) => presentRow(row, nowMs, eligibleByClass);
  const runningRows = running.map(present);
  const waitingRows = waiting.map(present);
  const finishedRows = collapseFailed(finished.map(present));
  return [
    lane('running', 'Running', coalesceRows(runningRows, 'running', nowMs), runningRows.length),
    lane('waiting', 'Waiting', coalesceRows(waitingRows, 'waiting', nowMs), waitingRows.length),
    lane('finished', 'Finished', coalesceRows(finishedRows, 'finished', nowMs), finishedRows.length),
  ];
}

export function filterRows(rows, filter) {
  const list = rows ?? [];
  if (filter === 'live') return list.filter((row) => isLive(row.state));
  if (filter === 'failed') return list.filter((row) => row.state === 'failed');
  return list;
}

export function summary(rows) {
  let live = 0;
  let failed = 0;
  for (const row of rows ?? []) {
    if (isLive(row.state)) live += 1;
    else if (row.state === 'failed') failed += 1;
  }
  return `${live} live · ${failed} failed`;
}

function agoText(at, nowMs) {
  if (at == null) return '';
  return spanText(Math.max(0, Math.round((nowMs - at) / 1000)));
}

export function staleness(entry, nowMs) {
  const result = entry?.result;
  const rows = result?.rows ?? [];
  const at = entry?.at;
  const ago = agoText(at, nowMs);
  if (result && result.ok === false) {
    const kind = result.error?.kind;
    if (kind === 'missing-tool') {
      return { kind: 'notInstalled', words: 'otto-queue not installed - run otto-workbench sync', rows };
    }
    if (kind === 'outdated') {
      return { kind: 'outdated', words: 'otto-queue outdated - run otto-workbench sync', rows };
    }
    return {
      kind: 'unreachable',
      words: ago ? `not reachable - last read ${ago} ago` : 'not reachable',
      rows,
    };
  }
  const words = ago ? `updated ${ago} ago` : 'updated 0s ago';
  const age = at == null ? 0 : nowMs - at;
  return { kind: age >= STALE_MS ? 'stale' : 'fresh', words, rows };
}

export function refreshSpinning(sinceMs, nowMs) {
  if (sinceMs == null || sinceMs === '') return false;
  return nowMs - sinceMs >= LOAD_DELAY_MS;
}
