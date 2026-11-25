/**
 * Open, rename and close a pi or Claude Code session from the Sessions panel.
 *
 * A live hc terminal labelled `otto.session=<id>` (written by `pi-sessions live` from the
 * session-brief extension, and by `hcSessionArgv` for a terminal started here) is brought
 * forward. A closed session starts as `hc run --bg pi --session <file>` (Claude Code:
 * `claude --resume <id>`) in its cwd — pi with `--name` to rename it — and is kept as a tab. A live session with no labelled terminal runs
 * outside herdr and is never started a second time, and a live session is renamed from inside
 * (`/name`): nothing else may write its session file while pi holds it. A Claude Code session has no rename here. Close asks
 * `pi-sessions close <id>` on the session's machine. Pure over deps
 * {exec, run, panes, spaces, terminals, sleep}; never rejects.
 */

import { invoke } from './board_data.js';
import { hcSessionArgv, keepStarted, notShownText, parseRunReply } from './launch.js';
import { findArgv, focusSteps, liveAddress } from './run_session.js';

export const RENAME_LIVE_TEXT = 'Rename it in the session with /name';

export function outsideText(pid) {
  return `running outside herdr (pid ${pid}) — open it where it runs`;
}

export function openArgv(row, name = null) {
  if (row.harness === 'claude') return ['claude', '--resume', row.id];
  return name ? ['pi', '--session', row.file, '--name', name] : ['pi', '--session', row.file];
}

function failed(message) {
  return { ok: false, message: String(message ?? 'failed') };
}

// Session files being opened: lookup-then-start is not atomic, so a second open of the same
// file while one is pending would start a second pi on it.
const opening = new Set();

export async function openPiSession(deps, row, opts = {}) {
  if (opening.has(row.file)) return failed('already opening this session');
  opening.add(row.file);
  try {
    return await openOnce(deps, row, opts);
  } finally {
    opening.delete(row.file);
  }
}

async function openOnce(deps, row, { name = null } = {}) {
  const { exec, run, panes, spaces } = deps;
  let ls;
  try {
    ls = await exec('hc', findArgv(row.id), {});
  } catch (err) {
    return failed(`could not look for an open session: ${err?.message ?? err}`);
  }
  // Refuse rather than start a second pi on the same session file when the lookup fails.
  if (ls.code !== 0) return failed(`could not look for an open session: ${ls.stderr || `exit ${ls.code}`}`);
  const address = liveAddress(ls.stdout, row.on);
  if (address) {
    if (name) return failed(RENAME_LIVE_TEXT);
    for (const [cmd, args] of focusSteps(address, panes(), spaces())) run(cmd, args);
    return { ok: true, opened: 'focused' };
  }
  if (row.live) return failed(name ? RENAME_LIVE_TEXT : outsideText(row.pid));
  let res;
  try {
    res = parseRunReply(await exec('hc', hcSessionArgv({
      repo: null, argv: openArgv(row, name), sessionId: row.id, cwd: row.cwd, on: row.on,
    }), {}));
  } catch (err) {
    res = { ok: false, error: { message: String(err?.message ?? err) } };
  }
  if (!res.ok) return failed(res.error.message);
  if (!(await keepStarted(deps, res.terminal))) return failed(notShownText(res.terminal));
  return { ok: true, opened: 'started' };
}

export async function closePiSession(exec, row) {
  const ran = await invoke(exec, { on: row.on, cwd: null }, 'pi-sessions', ['close', row.id]);
  return ran.ok ? { ok: true } : failed(ran.error?.message);
}
