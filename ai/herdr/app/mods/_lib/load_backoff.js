/**
 * Timer ticks that skip while the machine is busy. A poll of the board costs a process start and
 * GitHub round-trips on the box that is already struggling, so the periodic ticks (never a manual
 * refresh) run every Nth time while load per core stays high: every 2nd at BUSY_PER_CORE, every
 * 4th at twice that, and every tick again once it drops. The load is read on the machine the
 * poller runs on.
 * ceiling: local load only and fixed thresholds; upgrade to the polled target's load, or to board.*
 * config keys, if a poller on one machine drives pulls on another that stays hot.
 */

export const BUSY_PER_CORE = 1.5;
export const STRAINED_PER_CORE = BUSY_PER_CORE * 2;
export const MAX_FACTOR = 4;

/** The 1-minute load average in `/proc/loadavg` text, or null when it does not parse. */
export function parseLoadavg(text) {
  const first = String(text ?? '').trim().split(/\s+/)[0];
  const load = Number.parseFloat(first);
  return Number.isFinite(load) && load >= 0 ? load : null;
}

/** How many ticks apart runs should be at `perCore` load: 1 when unknown or quiet. */
export function factorFor(perCore) {
  if (!Number.isFinite(perCore)) return 1;
  if (perCore >= STRAINED_PER_CORE) return MAX_FACTOR;
  if (perCore >= BUSY_PER_CORE) return 2;
  return 1;
}

/**
 * `read()` returns `{ load, cores }` (or a promise of it, or null when the machine cannot say).
 * `pass()` is synchronous so a timer callback stays so: it answers from the last reading and starts
 * the next one, which means the gate lags the machine by one tick. A failed read lets ticks run. A
 * read still in flight is not started again, so a slow probe never piles up behind the timer. Each
 * timer takes its own gate: one skip counter shared by two periods would skip the wrong ticks.
 */
export function createLoadGate({ read }) {
  let factor = 1;
  let skipped = 0;
  let reading = false;
  async function sample() {
    if (reading) return;
    reading = true;
    try {
      const r = await read();
      factor = r && r.cores > 0 ? factorFor(r.load / r.cores) : 1;
    } catch {
      factor = 1;
    } finally {
      reading = false;
    }
  }
  return {
    pass() {
      void sample();
      if (factor <= 1) { skipped = 0; return true; }
      skipped += 1;
      if (skipped < factor) return false;
      skipped = 0;
      return true;
    },
  };
}

/** A `read` for the herdr mod: `/proc/loadavg` and `nproc` through the mod's exec. Null off Linux. */
export function execLoadReader(exec) {
  let cores = null;
  return async function read() {
    if (cores == null) {
      const n = await exec('nproc', [], {});
      cores = n.code === 0 ? Number.parseInt(n.stdout, 10) : 0;
    }
    if (!(cores > 0)) return null;
    const l = await exec('cat', ['/proc/loadavg'], {});
    const load = l.code === 0 ? parseLoadavg(l.stdout) : null;
    return load == null ? null : { load, cores };
  };
}
