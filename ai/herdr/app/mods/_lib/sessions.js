/**
 * The Sessions panel's model: pi and Claude Code sessions per machine from `pi-sessions list --json`.
 * Pure. Python owns the facts — title, TL;DR, PRs, worktrees, live RSS and idle, spend, and the
 * close-candidate verdict (ai/lib/pi_sessions/live.py) — this only loads, words, groups and
 * orders them.
 */

import { invoke, readJson } from './board_data.js';

export const SINCE_STEP_DAYS = 9;
export const SESSIONS_SCHEMA = 1;
export const OUTDATED_TEXT = 'Update this machine to list sessions (otto-workbench sync)';

export function listArgv(sinceDays) {
  return ['list', '--json', '--since', String(sinceDays)];
}

export function parseList(value) {
  const ok = value != null && typeof value === 'object'
    && value.schema === SESSIONS_SCHEMA && Array.isArray(value.sessions);
  return ok ? { ok: true, doc: value } : { ok: false, error: { kind: 'outdated', message: OUTDATED_TEXT } };
}

// A machine without pi-sessions has not synced since this landed: say so, not "command not found".
export async function loadSessions(exec, ctx, sinceDays) {
  const ran = await invoke(exec, ctx, 'pi-sessions', listArgv(sinceDays));
  if (!ran.ok) {
    return ran.error?.kind === 'missing-tool'
      ? { ok: false, error: { kind: 'outdated', message: OUTDATED_TEXT } }
      : ran;
  }
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return parsed;
  return parseList(parsed.value);
}

export function durationText(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))} min`;
  if (s < 2 * 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}

export function rssText(mb) {
  const n = Number(mb) || 0;
  return n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${Math.round(n)} MB`;
}

// Breakpoints match ai/pi/extensions/_shared/spend.ts — herdr cannot import that tree.
export function formatCost(cost) {
  const n = Number(cost);
  if (!(n > 0)) return '$0.00';
  if (n < 0.001) return '<$0.001';
  if (n < 0.01) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

export function spendText(spend) {
  if (!Array.isArray(spend) || spend.length === 0) return '';
  return spend.filter((item) => item && item.cost > 0)
    .map((item) => `${item.label} ${formatCost(item.cost)}`).join(' · ');
}

export function titleOf(session) {
  return session.name || session.title_set || `${folderBasename(session.cwd)} · ${String(session.id).slice(0, 8)}`;
}

export function rowModel(session, on, nowMs) {
  const live = session.live ?? null;
  const lastMs = Date.parse(session.last_active);
  const known = Number.isFinite(lastMs);
  const spend = spendText(session.spend);
  const base = live
    ? `live · ${rssText(live.rss_mb)} · idle ${durationText(live.idle_s)}`
    : `closed · ${known ? durationText((nowMs - lastMs) / 1000) : '?'} ago`;
  return {
    key: `${on ?? ''}:${session.id}`,
    id: session.id,
    // A machine on an older pi-sessions sends no harness: every row it lists is pi's.
    harness: session.harness === 'claude' ? 'claude' : 'pi',
    file: session.file,
    cwd: session.cwd,
    on: on ?? null,
    title: titleOf(session),
    tldr: session.tldr || '',
    prs: (session.prs ?? []).map((p) => ({ label: `#${p.number}`, url: p.url, title: p.title || '' })),
    live: live != null,
    pid: live?.pid ?? null,
    candidate: Boolean(live?.close_candidate),
    state: spend ? `${base} · ${spend}` : base,
    lastMs: known ? lastMs : 0,
  };
}

function groupLabel(session) {
  return (session.worktrees ?? [])[0] ?? session.cwd ?? '';
}

// Close candidates first (T3-g), then the most recently active.
function byPriority(a, b) {
  return (Number(b.candidate) - Number(a.candidate)) || (b.lastMs - a.lastMs);
}

export function groupsOf(sessions, on, nowMs) {
  const byKey = new Map();
  for (const session of sessions ?? []) {
    const key = groupLabel(session);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(rowModel(session, on, nowMs));
  }
  const groups = [...byKey.entries()].map(([key, rows]) => ({ key, label: key, rows: rows.sort(byPriority) }));
  return groups.sort((a, b) => byPriority(a.rows[0], b.rows[0]));
}

export function machineSessions({ key, on = null, title, result, since }, nowMs) {
  const base = { key, on, title, since, error: null, groups: [] };
  if (result == null) return { ...base, status: 'loading' };
  if (!result.ok) return { ...base, status: 'error', error: result.error?.message ?? 'failed' };
  return { ...base, status: 'ready', groups: groupsOf(result.doc.sessions, on, nowMs) };
}

export function folderBasename(key) {
  const p = String(key ?? '').replace(/\/+$/, '');
  return p.slice(p.lastIndexOf('/') + 1) || p;
}

export function machineCountsLine(machine) {
  if (machine?.status !== 'ready') return '';
  let live = 0;
  let cand = 0;
  let n = 0;
  for (const g of machine.groups ?? []) {
    for (const r of g.rows ?? []) {
      n += 1;
      if (r.live) live += 1;
      if (r.candidate) cand += 1;
    }
  }
  if (live > 0 || cand > 0) {
    const parts = [];
    if (live > 0) parts.push(`${live} live`);
    if (cand > 0) parts.push(`${cand} close candidate${cand === 1 ? "" : "s"}`);
    return parts.join(" · ");
  }
  return n === 1 ? "1 session" : `${n} sessions`;
}

export function homeCards(machines) {
  return (machines ?? []).map((m) => ({
    key: m.key,
    title: m.title,
    counts: machineCountsLine(m),
    status: m.status === 'error' ? { kind: 'fail', word: 'error' } : undefined,
  }));
}

export function isFolderOpen(open, key, groupCount) {
  if (open == null) return groupCount === 1;
  return (open ?? []).includes(key);
}

export function ensureOpenState(open, keys) {
  if (open != null) return open;
  if (keys.length === 1) return [keys[0]];
  return null;
}

export function toggleFolder(open, key, groupCount, allKeys) {
  const current = open == null
    ? (groupCount === 1 ? [...(allKeys ?? [])] : [])
    : [...open];
  const i = current.indexOf(key);
  if (i >= 0) current.splice(i, 1);
  else current.push(key);
  return current;
}
