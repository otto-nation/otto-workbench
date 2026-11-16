/**
 * Host health model: severity, gauges, staleness, process list. Pure.
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

const FULL = '█';
const EMPTY = '░';
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

/** A `cells`-wide bar of filled then empty glyphs for `fraction` in 0..1. */
export function bar(fraction, cells = 8) {
  const n = Number(cells);
  if (!Number.isFinite(n) || n <= 0) return '';
  const count = Math.floor(n);
  if (!Number.isFinite(fraction)) return EMPTY.repeat(count);
  const f = Math.min(1, Math.max(0, fraction));
  const filled = Math.round(f * count);
  return FULL.repeat(filled) + EMPTY.repeat(count - filled);
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

function gaugesOf(health) {
  const gauges = [];
  const perCore = loadPerCore(health);
  if (perCore != null) {
    gauges.push({
      key: 'load',
      label: 'load',
      text: `${bar(perCore / STRAINED_PER_CORE)} ${perCore.toFixed(1)}/core`,
      kind: loadKind(perCore),
    });
  }
  const free = memFree(health);
  if (free != null) {
    const used = 1 - free;
    gauges.push({
      key: 'mem',
      label: 'mem',
      text: `${bar(used)} ${pct(used)}% used`,
      kind: memKind(free),
    });
  }
  // The worst of home and the Docker root, the same figure severityOf judges.
  const homeUsed = worstDiskUsed(health);
  if (homeUsed != null) {
    gauges.push({
      key: 'disk',
      label: 'disk',
      text: `${bar(homeUsed)} ${pct(homeUsed)}% disk`,
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

function noteOf(health) {
  const swap = health?.mem?.swap_used_mb;
  if (!Number.isFinite(swap) || !(swap > 1024)) return null;
  return `swap ${swap} MB`;
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

/** Title, status, staleness, gauges, counts and swap note for one host entry. */
export function hostSummary(entry, nowMs) {
  const staleness = hostStaleness(entry, nowMs);
  const health = entry.result?.ok ? entry.result.health : entry.health;
  return {
    title: hostTitle(entry),
    status: statusOf(staleness, health),
    staleness,
    gauges: health != null ? gaugesOf(health) : [],
    counts: health != null ? countsOf(health) : '',
    note: health != null ? noteOf(health) : null,
  };
}

/** `health.procs.top_cpu` or `top_mem` as rows with key, command, meta and right. */
export function topProcesses(health, by) {
  const list = by === 'mem' ? health?.procs?.top_mem : health?.procs?.top_cpu;
  if (!Array.isArray(list) || health?.procs == null) return [];
  return list.filter((p) => Number.isFinite(p?.cpu) && Number.isFinite(p?.mem)).map((p) => ({
    key: `${p.pid}`,
    command: p.command,
    meta: `${p.user} · ${Number.isFinite(p.age_s) ? spanText(p.age_s) : '?'}`,
    right: `${p.cpu.toFixed(0)}% cpu · ${p.mem.toFixed(0)}% mem`,
  }));
}

/** One block per host, in the given order: summary, collapse, last good health. */
export function hostsModel(entries, nowMs, collapsed) {
  return (entries ?? []).map((entry) => {
    const on = entry.on;
    const key = entry.key ?? (on == null || on === '' ? '' : String(on));
    const summary = hostSummary(entry, nowMs);
    const health = entry.result?.ok ? entry.result.health : entry.health;
    const map = collapsed ?? {};
    const forced = Object.prototype.hasOwnProperty.call(map, key) ? Boolean(map[key]) : null;
    const open = forced == null ? summary.status.kind !== 'pass' : !forced;
    return { key, summary, open, health };
  });
}
