/**
 * Queue panel model: lanes, position in line, timing, failure text, staleness.
 * Pure. Admission order matches otto_queue.ledger._first_waiting_id.
 */
import { LOAD_DELAY_MS } from './load_state.js';

const FINISHED = new Set(['done', 'failed', 'cancelled', 'tempfail']);
const SUCCESS_STATES = new Set(['done', 'cancelled', 'tempfail']);
const SUBJECT_RE = /([A-Za-z0-9._-]+\/[A-Za-z0-9._-]+#\d+)\s*$/;
export const STALE_MS = 60_000;
const DAY_MS = 24 * 60 * 60 * 1000;

export const COALESCE_MIN = 3;
export const FAIL_COLLAPSE_MIN = 2;
export const WAITING_CAP = 8;
export const FINISHED_CAP = 10;
export const FAIL_KEEP_MS = DAY_MS;
export const RECENT_TERMINAL = 20;

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

function hasRetry(row, nowMs) {
  const at = toMs(row?.not_before);
  return row?.state === 'queued' && at != null && at > nowMs;
}

function waitReason(row) {
  if (typeof row?.reason === 'string' && row.reason !== '') return row.reason;
  if (typeof row?.wait_reason === 'string' && row.wait_reason !== '') return row.wait_reason;
  return '';
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

function leadOf(members, field) {
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
  if (laneKey === 'waiting') {
    const sec = longestSpan(members, 'created_at', nowMs);
    return sec == null ? '' : `waited ${spanText(sec)}`;
  }
  const lead = leadOf(members, 'finished_at');
  const finished = toMs(lead?.finished_at);
  if (finished == null) return '';
  return `newest ${spanText(Math.max(0, Math.round((nowMs - finished) / 1000)))} ago`;
}

function ordinal(n) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n}st`;
  if (mod10 === 2 && mod100 !== 12) return `${n}nd`;
  if (mod10 === 3 && mod100 !== 13) return `${n}rd`;
  return `${n}th`;
}

function lineRange(members, eligibleByClass) {
  const list = eligibleByClass.get(members[0]?.entry_class) ?? [];
  const idxs = [];
  for (const member of members) {
    const idx = list.findIndex((r) => r.id === member.id);
    if (idx >= 0) idxs.push(idx);
  }
  if (!idxs.length) return '';
  const from = Math.min(...idxs) + 1;
  const to = Math.max(...idxs) + 1;
  if (from === to) return `${ordinal(from)} in line`;
  return `${ordinal(from)}–${ordinal(to)} in line`;
}

function waitingGroup(members, nowMs, eligibleByClass) {
  const action = actionOf(members[0]);
  const cls = members[0].entry_class ?? '';
  return {
    key: `waiting:${action}:${cls}`,
    action,
    count: members.length,
    members,
    lead: members[0],
    subjects: subjectsOf(members),
    timing: groupTiming(members, 'waiting', nowMs),
    line: lineRange(members, eligibleByClass),
    failure: '',
  };
}

function successGroup(action, state, members, nowMs) {
  return {
    key: `finished:${state}:${action}`,
    action,
    state,
    count: members.length,
    members,
    lead: members[0],
    subjects: subjectsOf(members),
    timing: groupTiming(members, 'finished', nowMs),
    failure: `${members.length} ${state}`,
  };
}

function groupWaiting(rows, nowMs, eligibleByClass) {
  const out = [];
  let i = 0;
  while (i < rows.length) {
    const row = rows[i];
    if (hasRetry(row, nowMs) || actionOf(row) === '') {
      out.push(row);
      i += 1;
      continue;
    }
    const action = actionOf(row);
    const cls = row.entry_class;
    const reason = waitReason(row);
    let j = i + 1;
    while (j < rows.length) {
      const next = rows[j];
      if (hasRetry(next, nowMs)) break;
      if (actionOf(next) !== action) break;
      if (next.entry_class !== cls) break;
      if (waitReason(next) !== reason) break;
      j += 1;
    }
    const run = rows.slice(i, j);
    if (run.length >= COALESCE_MIN) out.push(waitingGroup(run, nowMs, eligibleByClass));
    else out.push(...run);
    i = j;
  }
  return out;
}

function collapseFailed(rows, nowMs) {
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
    if (members.length < FAIL_COLLAPSE_MIN) {
      out.push(members[0]);
      continue;
    }
    const lead = members[0];
    const finished = toMs(lead.finished_at);
    const age = finished == null
      ? ''
      : ` · last ${spanText(Math.max(0, Math.round((nowMs - finished) / 1000)))} ago`;
    out.push({
      ...lead,
      key: `finished:failed:${item.key}`,
      failure: `failed ${members.length}×${age}`,
      members,
    });
  }
  return out;
}

function groupFinished(rows, nowMs) {
  const collapsed = collapseFailed(rows, nowMs);
  const buckets = new Map();
  for (const row of collapsed) {
    if (row.state === 'failed' || row.members) continue;
    const action = actionOf(row);
    if (!action || !SUCCESS_STATES.has(row.state)) continue;
    const key = `finished:${row.state}:${action}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(row);
  }
  const groupedKeys = new Set();
  for (const [key, members] of buckets) {
    if (members.length >= COALESCE_MIN) groupedKeys.add(key);
  }
  const used = new Set();
  const out = [];
  for (const row of collapsed) {
    if (used.has(row.id)) continue;
    if (row.state === 'failed' || row.members) {
      out.push(row);
      continue;
    }
    const action = actionOf(row);
    const key = `finished:${row.state}:${action}`;
    if (action && groupedKeys.has(key)) {
      const members = buckets.get(key);
      out.push(successGroup(action, row.state, members, nowMs));
      for (const member of members) used.add(member.id);
      continue;
    }
    out.push(row);
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

function isKeptFailure(row, nowMs) {
  const newest = row.members?.[0] ?? row;
  if (newest.state !== 'failed' && row.state !== 'failed') return false;
  const at = toMs(newest.finished_at ?? row.finished_at);
  if (at == null) return false;
  const age = nowMs - at;
  return age >= 0 && age <= FAIL_KEEP_MS;
}

function capWaiting(rows) {
  if (rows.length <= WAITING_CAP) return { rows, more: 0 };
  return { rows: rows.slice(0, WAITING_CAP), more: rows.length - WAITING_CAP };
}

function capFinished(rows, nowMs) {
  if (rows.length <= FINISHED_CAP) return { rows, more: 0 };
  let cut = FINISHED_CAP;
  for (let i = FINISHED_CAP; i < rows.length; i += 1) {
    if (isKeptFailure(rows[i], nowMs)) cut = i + 1;
  }
  return { rows: rows.slice(0, cut), more: rows.length - cut };
}

function lane(key, label, rows, count, more = 0) {
  return { key, label, count, rows, card: count > 0, more };
}

export function lanesModel(rows, nowMs, { allWaiting = false, allFinished = false } = {}) {
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
  const waitingRows = groupWaiting(waiting.map(present), nowMs, eligibleByClass);
  const finishedRows = groupFinished(finished.map(present), nowMs);
  const waitingShown = allWaiting ? { rows: waitingRows, more: 0 } : capWaiting(waitingRows);
  const finishedShown = allFinished ? { rows: finishedRows, more: 0 } : capFinished(finishedRows, nowMs);
  return [
    lane('running', 'Running', runningRows, runningRows.length),
    lane('waiting', 'Waiting', waitingShown.rows, waiting.length, waitingShown.more),
    lane('finished', 'Finished', finishedShown.rows, finished.length, finishedShown.more),
  ];
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
