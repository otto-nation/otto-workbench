/**
 * Host health model: severity, meters, staleness, process list.
 * Neutral machine model for the shared UI, home cards and the host page. Pure.
 *
 * ceiling: fixed thresholds (LOADED_MEM_FREE, STRAINED_MEM_FREE, LOADED_DISK_USED,
 * STRAINED_DISK_USED); upgrade if a host with a different size profile needs its own.
 */

import { BUSY_PER_CORE, STRAINED_PER_CORE } from './load_backoff.js';
import { STALE_MS } from './queue_lanes.js';

/** Free memory below this fraction is loaded. */
export const LOADED_MEM_FREE = 0.10;
/** Free memory below this fraction is strained. */
export const STRAINED_MEM_FREE = 0.05;
/** Disk used at or above this fraction is loaded. */
export const LOADED_DISK_USED = 0.90;
/** Disk used at or above this fraction is strained. */
export const STRAINED_DISK_USED = 0.95;

const WORD = Object.freeze({ pass: 'ok', warn: 'loaded', fail: 'strained' });
const RANK = Object.freeze({ pass: 0, warn: 1, fail: 2 });
const HEAVY_NAMES = Object.freeze(['golangci-lint', 'go', 'gh', 'tasks']);

function spanText(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function agoText(at, nowMs) {
  if (at == null) return '';
  return spanText(Math.max(0, Math.round((nowMs - at) / 1000)));
}

function raise(kind, next) {
  return (RANK[next] ?? 0) > (RANK[kind] ?? 0) ? next : kind;
}

function loadPerCore(health) {
  const cores = health?.cores;
  const m1 = health?.load?.m1;
  if (!(cores > 0) || !Number.isFinite(m1)) return null;
  return m1 / cores;
}

function memFree(health) {
  const mem = health?.mem;
  if (mem == null || !(mem.total_mb > 0) || !Number.isFinite(mem.available_mb)) return null;
  return mem.available_mb / mem.total_mb;
}

function diskUsedOf(part) {
  if (part == null || !(part.size_mb > 0) || !Number.isFinite(part.used_mb)) return null;
  return part.used_mb / part.size_mb;
}

function worstDiskUsed(health) {
  const home = diskUsedOf(health?.disk?.home);
  const docker = diskUsedOf(health?.disk?.docker);
  if (home == null) return docker;
  if (docker == null) return home;
  return Math.max(home, docker);
}

function loadKind(perCore) {
  if (perCore >= STRAINED_PER_CORE) return 'fail';
  if (perCore >= BUSY_PER_CORE) return 'warn';
  return 'pass';
}

function memKind(free) {
  if (free < STRAINED_MEM_FREE) return 'fail';
  if (free < LOADED_MEM_FREE) return 'warn';
  return 'pass';
}

function diskKind(used) {
  if (used >= STRAINED_DISK_USED) return 'fail';
  if (used >= LOADED_DISK_USED) return 'warn';
  return 'pass';
}

function pct(fraction) {
  return Math.round(fraction * 100);
}

/** Worst of load, memory and disk: pass/ok, warn/loaded or fail/strained, with reasons. */
export function severityOf(health) {
  let kind = 'pass';
  const reasons = [];
  const perCore = loadPerCore(health);
  if (perCore != null) {
    const next = loadKind(perCore);
    kind = raise(kind, next);
    if (next !== 'pass') reasons.push(`load ${perCore.toFixed(1)} per core`);
  }
  const free = memFree(health);
  if (free != null) {
    const next = memKind(free);
    kind = raise(kind, next);
    if (next !== 'pass') reasons.push(`memory ${pct(free)}% free`);
  }
  const used = worstDiskUsed(health);
  if (used != null) {
    const next = diskKind(used);
    kind = raise(kind, next);
    if (next !== 'pass') reasons.push(`disk ${pct(used)}% used`);
  }
  return { kind, word: WORD[kind], reasons };
}

/** Fresh/stale/loading/unreachable/outdated words for a host entry's last read. */
export function hostStaleness(entry, nowMs) {
  const result = entry?.result;
  if (result == null) return { kind: 'loading', words: 'Loading…' };
  const ago = agoText(entry?.at, nowMs);
  if (result.ok === false) {
    const errKind = result.error?.kind;
    if (errKind === 'missing-tool' || errKind === 'outdated') {
      return {
        kind: 'outdated',
        words: 'host-health missing - run otto-workbench sync on this machine',
      };
    }
    return {
      kind: 'unreachable',
      words: ago ? `not reachable - last read ${ago} ago` : 'not reachable',
    };
  }
  const at = entry?.at;
  const age = at == null ? 0 : nowMs - at;
  return { kind: age >= STALE_MS ? 'stale' : 'fresh', words: `updated ${ago || '0s'} ago` };
}

function hostTitle(entry) {
  if (typeof entry?.title === 'string' && entry.title !== '') return entry.title;
  if (entry?.on == null || entry.on === '') return 'This computer';
  return String(entry.on);
}

// A model for _ui/ui.js meter(): a continuous bar of `fraction` (clamped to 0..1) and its reading.
function meterOf(fraction, text) {
  const f = Number.isFinite(fraction) ? Math.min(1, Math.max(0, fraction)) : 0;
  return { kind: 'bar', total: 100, filled: Math.round(f * 100), text };
}

function gaugesOf(health) {
  const gauges = [];
  const perCore = loadPerCore(health);
  if (perCore != null) {
    gauges.push({
      key: 'load',
      label: 'load',
      meter: meterOf(perCore / STRAINED_PER_CORE, `${perCore.toFixed(1)}/core`),
      kind: loadKind(perCore),
    });
  }
  const free = memFree(health);
  if (free != null) {
    const used = 1 - free;
    gauges.push({
      key: 'mem',
      label: 'mem',
      meter: meterOf(used, `${pct(used)}% used`),
      kind: memKind(free),
    });
  }
  // The worst of home and the Docker root, the same figure severityOf judges.
  const homeUsed = worstDiskUsed(health);
  if (homeUsed != null) {
    gauges.push({
      key: 'disk',
      label: 'disk',
      meter: meterOf(homeUsed, `${pct(homeUsed)}% disk`),
      kind: diskKind(homeUsed),
    });
  }
  return gauges;
}

function countsOf(health) {
  const parts = [];
  const procs = health?.procs;
  if (procs != null) {
    let text = `${procs.count} processes`;
    if ((procs.zombies ?? 0) > 0) text += `, ${procs.zombies} zombies`;
    parts.push(text);
  }
  const heavy = health?.heavy;
  if (heavy != null && typeof heavy === 'object') {
    const bits = [];
    for (const name of HEAVY_NAMES) {
      const n = heavy[name];
      if (n > 0) bits.push(`${name} ${n}`);
    }
    if (bits.length) parts.push(bits.join(', '));
  }
  const queue = health?.queue;
  if (queue != null && typeof queue === 'object') {
    if (queue.busy === true) parts.push('queue busy');
    else parts.push(`queue ${queue.running ?? 0} running, ${queue.queued ?? 0} waiting`);
  }
  return parts.join(' · ');
}

function statusOf(staleness, health) {
  if (staleness.kind === 'loading') return { kind: 'running', word: 'loading' };
  if (staleness.kind === 'unreachable') return { kind: 'warn', word: 'unreachable' };
  if (staleness.kind === 'outdated') return { kind: 'warn', word: 'outdated' };
  if (health != null) {
    const s = severityOf(health);
    return { kind: s.kind, word: s.word };
  }
  return { kind: 'pass', word: 'ok' };
}

function lastHealth(entry) {
  return entry.result?.ok ? entry.result.health : entry.health;
}

function gb(mb) {
  return (mb / 1024).toFixed(1);
}

function detailRows(health) {
  const rows = [];
  if (health.cores > 0) rows.push({ key: 'cores', value: String(health.cores) });
  const load = health.load;
  if (load != null) {
    const parts = ['m1', 'm5', 'm15'].filter((k) => Number.isFinite(load[k])).map((k) => load[k].toFixed(1));
    if (parts.length) rows.push({ key: 'load', value: parts.join(' · ') });
  }
  const mem = health.mem;
  if (mem != null && Number.isFinite(mem.total_mb) && Number.isFinite(mem.available_mb)) {
    rows.push({
      key: 'memory',
      value: `${gb(mem.total_mb - mem.available_mb)} of ${gb(mem.total_mb)} GB used`,
    });
  }
  if (mem != null && Number.isFinite(mem.swap_used_mb)) {
    rows.push({ key: 'swap', value: `${gb(mem.swap_used_mb)} GB` });
  }
  // The ZFS ARC sits inside memory's used figure; naming it says how much of that no process owns.
  const arc = health.arc;
  if (arc != null && Number.isFinite(arc.size_mb) && Number.isFinite(arc.max_mb)) {
    rows.push({ key: 'zfs cache', value: `${gb(arc.size_mb)} of ${gb(arc.max_mb)} GB` });
  }
  for (const [key, part] of [['disk home', health.disk?.home], ['disk docker', health.disk?.docker]]) {
    const usedFrac = diskUsedOf(part);
    if (usedFrac == null) continue;
    rows.push({
      key,
      value: `${gb(part.used_mb)} of ${gb(part.size_mb)} GB (${Math.round(usedFrac * 100)}%)`,
    });
  }
  if (health.procs != null) rows.push({ key: 'zombies', value: String(health.procs.zombies ?? 0) });
  const heavy = health.heavy;
  if (heavy != null) {
    const bits = HEAVY_NAMES.filter((name) => heavy[name] > 0).map((name) => `${name} ${heavy[name]}`);
    rows.push({ key: 'heavy', value: bits.length ? bits.join(', ') : 'none' });
  }
  const queue = health.queue;
  if (queue != null) {
    rows.push({
      key: 'queue',
      value: queue.busy === true ? 'busy' : `${queue.running ?? 0} running, ${queue.queued ?? 0} waiting`,
    });
  }
  return rows;
}

/** Title, status, staleness, gauges and counts for one host entry. */
export function hostSummary(entry, nowMs) {
  const staleness = hostStaleness(entry, nowMs);
  const health = lastHealth(entry);
  return {
    title: hostTitle(entry),
    status: statusOf(staleness, health),
    staleness,
    gauges: health != null ? gaugesOf(health) : [],
    counts: health != null ? countsOf(health) : '',
  };
}

/** Neutral machine model for the shared card and header. No hostNote. */
export function machineModel(summary) {
  const kind = summary.staleness?.kind;
  const model = {
    title: summary.title,
    status: summary.status,
    meters: (summary.gauges ?? []).map((g) => ({ label: g.label, model: g.meter, glyph: g.kind })),
    counts: summary.counts,
    muted: ['stale', 'unreachable', 'outdated'].includes(kind),
  };
  if (summary.staleness != null) {
    model.staleness = {
      words: summary.staleness.words,
      warn: !['fresh', 'loading'].includes(kind),
    };
  }
  return model;
}

/** `health.procs.top_cpu` or `top_mem` as rows with key, command, meta and right, capped at `limit`. */
export function topProcesses(health, by, limit = 5) {
  const list = by === 'mem' ? health?.procs?.top_mem : health?.procs?.top_cpu;
  if (!Array.isArray(list) || health?.procs == null) return [];
  if (!Number.isFinite(limit) || limit <= 0) return [];
  return list.filter((p) => Number.isFinite(p?.cpu) && Number.isFinite(p?.mem)).map((p) => ({
    key: `${p.pid}`,
    command: p.command,
    meta: `${p.user} · ${Number.isFinite(p.age_s) ? spanText(p.age_s) : '?'}`,
    right: `${p.cpu.toFixed(0)}% cpu · ${p.mem.toFixed(0)}% mem`,
  })).slice(0, limit);
}

/** Home cards in the given order: key, neutral model. */
export function hostsModel(entries, nowMs) {
  return (entries ?? []).map((entry) => {
    const on = entry.on;
    const key = entry.key ?? (on == null || on === '' ? '' : String(on));
    return {
      key,
      model: machineModel(hostSummary(entry, nowMs)),
    };
  });
}

/** Machine page: header model, last good health, process rows and detail rows. */
export function hostPage(entry, nowMs, by) {
  const health = lastHealth(entry) ?? null;
  return {
    model: machineModel(hostSummary(entry, nowMs)),
    health,
    processes: topProcesses(health, by),
    details: health == null ? [] : detailRows(health),
  };
}
