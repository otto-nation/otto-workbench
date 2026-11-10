/**
 * The web board's scope: the checkouts the agent's live herdr-core terminals sit in. Replaces
 * scope.js spaceTargets (spaces and panes), which only the desktop app has. Pure; no I/O.
 */
import { machineOf } from '../../app/mods/_lib/launch.js';

export function lsAllArgv() {
  return ['ls', '--json'];
}

// One target per distinct (machine, cwd), in `hc ls --json` order. `hc ls` without --all lists live
// terminals only; an explicit non-live state is skipped too.
export function terminalTargets(json) {
  const list = Array.isArray(json) ? json : Array.isArray(json?.terminals) ? json.terminals : [];
  const seen = new Set();
  const out = [];
  for (const t of list) {
    if (!t || typeof t !== 'object' || typeof t.cwd !== 'string' || t.cwd === '') continue;
    if (t.state != null && t.state !== 'live') continue;
    const on = machineOf(t.address ?? t.id);
    const key = `${on ?? ''}\u0000${t.cwd}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ on, cwd: t.cwd });
  }
  return out;
}
