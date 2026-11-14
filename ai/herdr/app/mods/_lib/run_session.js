/**
 * Open a batch run's pi session from herdr.
 *
 * `pr batch session RUN_ID --json`, asked on the run's machine, says which session it is
 * (`session_id`) and where it runs (`cwd`); `ai/lib/batch/run_session.py` owns that and the
 * argv and env, so nothing here builds a pi command line. A live hc terminal carrying the
 * `otto.session` label for that id is brought forward (its pane's space, tab and pane, or
 * `terminal.keep` when no pane shows it); otherwise `hc run --bg` starts
 * `pr batch session RUN_ID` (which execs pi) in the checkout and `terminal.keep` gives it a tab.
 * A machine whose `pr board` features lack `batch-session` gets OUTDATED_TEXT and no exec.
 */

import { invoke, readJson } from './board_data.js';
import { LABEL_SESSION, hcSessionArgv, machineOf, parseRunReply } from './launch.js';

export const SESSION_FEATURE = 'batch-session';
export const OUTDATED_TEXT = 'Update pr on this machine to open sessions (otto-workbench sync)';

// What argparse prints for a `pr` that predates `pr batch session`.
const OUTDATED_RE = /invalid choice: 'session'/;

export function sessionArgv(runId) {
  return ['batch', 'session', String(runId), '--json'];
}

// 'ready' or 'outdated' from a machine's `pr board` features; 'unknown' when no board document
// from that machine is loaded, in which case the click asks `pr` and reads its refusal.
export function sessionGate(features) {
  if (!Array.isArray(features)) return 'unknown';
  return features.includes(SESSION_FEATURE) ? 'ready' : 'outdated';
}

// The features of the first board PR read from machine `on` (null: this computer), or null.
export function machineFeatures(prs, on) {
  const want = String(on ?? '');
  const pr = (prs ?? []).find((p) => String(p?.on ?? '') === want);
  return pr ? (Array.isArray(pr.features) ? pr.features : []) : null;
}

// State shown beside Open session: how many PRs the report has a session entry for.
export function waitingText(n) {
  if (!Number.isInteger(n) || n <= 0) return null;
  return n === 1 ? 'waiting on you: 1 PR' : `waiting on you: ${n} PRs`;
}

export function parseRecipe(doc) {
  const ok = doc != null && typeof doc === 'object'
    && typeof doc.session_id === 'string' && doc.session_id !== ''
    && typeof doc.cwd === 'string' && doc.cwd !== '';
  if (!ok) {
    return { ok: false, error: { kind: 'bad-json', message: 'pr batch session --json gave no session_id and cwd' } };
  }
  return { ok: true, recipe: { sessionId: doc.session_id, name: String(doc.name ?? ''), cwd: doc.cwd } };
}

export function findArgv(sessionId) {
  return ['ls', '--json', '--label', `${LABEL_SESSION}=${sessionId}`];
}

// The first live terminal on machine `on` in `hc ls --json --label` output, or null.
export function liveAddress(stdout, on) {
  const parsed = readJson(stdout ?? '');
  const value = parsed.ok ? parsed.value : null;
  const list = Array.isArray(value) ? value : value?.terminals;
  const want = on == null || on === '' ? null : on;
  for (const t of Array.isArray(list) ? list : []) {
    const address = t?.address;
    if (typeof address !== 'string') continue;
    if (t.state != null && t.state !== 'live') continue;
    if (machineOf(address) === want) return address;
  }
  return null;
}

// The herdr commands that bring `address` forward, as [name, args] pairs.
export function focusSteps(address, panes, spaces) {
  const pane = (panes ?? []).find((p) => p?.terminal === address);
  if (!pane) return [['terminal.keep', { terminal: address }]];
  const space = (spaces ?? []).find((s) => (s?.tabs ?? []).some((t) => t?.id === pane.tab));
  return [
    ...(space ? [['space.select', { id: space.id }]] : []),
    ['tab.select', { id: pane.tab }],
    ['pane.focus', { pane: pane.pane }],
  ];
}

function failed(message) {
  return { ok: false, outdated: false, message: String(message ?? 'failed') };
}

const OUTDATED = Object.freeze({ ok: false, outdated: true, message: OUTDATED_TEXT });

/**
 * deps: {exec, run, panes, spaces} (the herdr functions). target: {on, cwd, runId, repo, features}.
 * Resolves {ok:true, opened:'focused'|'started'} or {ok:false, outdated, message}; never rejects.
 */
export async function openRunSession(deps, target) {
  const { exec, run, panes, spaces } = deps;
  const { on = null, cwd = null, runId, repo = null, features = null } = target;
  if (sessionGate(features) === 'outdated') return OUTDATED;
  const asked = await invoke(exec, { on, cwd }, 'pr', sessionArgv(runId));
  if (!asked.ok) return OUTDATED_RE.test(asked.error.message ?? '') ? OUTDATED : failed(asked.error.message);
  const body = readJson(asked.stdout);
  const got = body.ok ? parseRecipe(body.value) : body;
  if (!got.ok) return failed(got.error.message);
  const { sessionId, cwd: checkout } = got.recipe;
  let ls;
  try {
    ls = await exec('hc', findArgv(sessionId), {});
  } catch (err) {
    return failed(`could not look for an open session: ${err?.message ?? err}`);
  }
  // Refuse rather than start a second pi on the same session file when the lookup fails.
  if (ls.code !== 0) return failed(`could not look for an open session: ${ls.stderr || `exit ${ls.code}`}`);
  const address = liveAddress(ls.stdout, on);
  if (address) {
    for (const [name, args] of focusSteps(address, panes(), spaces())) run(name, args);
    return { ok: true, opened: 'focused' };
  }
  let res;
  try {
    res = parseRunReply(await exec('hc', hcSessionArgv({ repo, runId, sessionId, cwd: checkout, on }), {}));
  } catch (err) {
    res = { ok: false, error: { message: String(err?.message ?? err) } };
  }
  if (!res.ok) return failed(res.error.message);
  run('terminal.keep', { terminal: res.terminal });
  return { ok: true, opened: 'started' };
}
