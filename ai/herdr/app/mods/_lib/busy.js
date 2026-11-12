/**
 * Liveness of the labelled PR terminals this app started (see launch.js for the labels).
 */

import { parsePrKey, machineOf, LABEL_APP, APP_NAME } from './launch.js';

// `hc ls --json` omits labels, so the live terminals this app started are found by the
// constant app label and each is then read with `hc get --json`, which carries labels,
// state and exit_code. Without --all only live terminals are listed.
export function lsArgv() {
  return ['ls', '--json', '--label', `${LABEL_APP}=${APP_NAME}`];
}

export function getArgv(id) {
  return ['get', id, '--json'];
}

function labelsOf(raw) {
  if (Array.isArray(raw)) {
    const out = {};
    for (const item of raw) {
      if (typeof item !== 'string') continue;
      const eq = item.indexOf('=');
      if (eq <= 0) continue;
      out[item.slice(0, eq)] = item.slice(eq + 1);
    }
    return out;
  }
  if (raw && typeof raw === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(raw)) {
      out[k] = v;
    }
    return out;
  }
  return {};
}

function oneTerminal(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const id = raw.address ?? raw.id;
  if (typeof id !== 'string') return null;
  return {
    id,
    machine: machineOf(id),
    state: raw.state ?? null,
    exitCode: raw.exit_code ?? null,
    finishedMs: raw.finished_ms ?? null,
    labels: labelsOf(raw.labels),
  };
}

function listOf(json) {
  if (Array.isArray(json)) return json;
  if (json && typeof json === 'object' && Array.isArray(json.terminals)) {
    return json.terminals;
  }
  return [];
}

export function terminalsOf(json) {
  const out = [];
  for (const item of listOf(json)) {
    const entry = oneTerminal(item);
    if (entry) out.push(entry);
  }
  return out;
}

function actionOf(labels) {
  const action = labels?.['otto.action'];
  return typeof action === 'string' ? action : null;
}

function matchRepo(labels, repo) {
  const parsed = parsePrKey(labels?.['otto.pr']);
  if (!parsed || parsed.repo !== repo) return null;
  return parsed;
}

// withRunning treats every busyMap entry as a live review. Session commands
// share otto.pr but must not flip the PR into "Review running".
const NOT_REVIEWING = new Set(['batch-fix']);

export function busyMap(entries, repo) {
  const out = new Map();
  for (const e of entries) {
    if (e.state !== 'live') continue;
    // ceiling: slug-only match, add host to the key if one machine reviews the same slug on two GitHub hosts
    const parsed = matchRepo(e.labels, repo);
    if (!parsed) continue;
    const action = actionOf(e.labels);
    if (NOT_REVIEWING.has(action)) continue;
    out.set(parsed.number, { terminal: e.id, action });
  }
  return out;
}

export function latestFinished(entries, repo, n) {
  let best = null;
  for (const e of entries) {
    const parsed = matchRepo(e.labels, repo);
    if (!parsed || parsed.number !== n) continue;
    if (e.finishedMs == null) continue;
    if (best == null || e.finishedMs > best.finishedMs) best = e;
  }
  if (!best) return null;
  return {
    terminal: best.id,
    exitCode: best.exitCode,
    action: actionOf(best.labels),
  };
}

export function mergePending(busy, pending, entries) {
  const out = new Map(busy);
  const finished = new Set();
  for (const e of entries) {
    if (e.state === 'finished') finished.add(e.id);
  }
  for (const [key, id] of Object.entries(pending)) {
    if (finished.has(id)) continue;
    const number = Number(key);
    const prev = out.get(number);
    out.set(number, { terminal: id, action: prev?.action ?? null });
  }
  return out;
}
