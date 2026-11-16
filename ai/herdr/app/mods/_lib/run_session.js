/**
 * Open a batch run's or a PR target's pi session from herdr.
 *
 * `pr batch session RUN_ID --json` / `pr --branch B target session --json`, asked on the
 * machine, says which session it is (`session_id`) and where it runs (`cwd`); the matching
 * `pr` module owns that and the argv and env, so nothing here builds a pi command line.
 * A live hc terminal carrying the `otto.session` label for that id is brought forward
 * (its pane's space, tab and pane, or `terminal.keep` when no pane shows it); otherwise
 * a kind with `adoptPane` takes an unlabelled agent pane in the checkout the same way;
 * otherwise `hc run --bg` starts the kind's open argv (which execs pi) in the checkout
 * and `terminal.keep` gives it a tab. A machine whose `pr board` features lack the
 * kind's feature gets OUTDATED_TEXT and no exec.
 */

import { invoke, readJson } from './board_data.js';
import { LABEL_SESSION, hcSessionArgv, machineOf, parseRunReply } from './launch.js';

export const SESSION_KINDS = {
  batch: {
    feature: 'batch-session',
    outdatedRe: /invalid choice: 'session'/,
    askArgv: (t) => ['batch', 'session', String(t.runId), '--json'],
    openArgv: (t) => ['pr', 'batch', 'session', String(t.runId)],
  },
  target: {
    feature: 'target-session',
    outdatedRe: /invalid choice: 'target'/,
    askArgv: (t) => ['--branch', String(t.branch), 'target', 'session', '--json'],
    openArgv: () => ['pr', 'target', 'session'],
    adoptPane: true,
  },
};

export const OUTDATED_TEXT = 'Update pr on this machine to open sessions (otto-workbench sync)';

export function sessionArgv(kind, t) {
  return SESSION_KINDS[kind].askArgv(t);
}

// 'ready' or 'outdated' from a machine's `pr board` features; 'unknown' when no board document
// from that machine is loaded, in which case the click asks `pr` and reads its refusal.
export function sessionGate(features, kind) {
  if (!Array.isArray(features)) return 'unknown';
  return features.includes(SESSION_KINDS[kind].feature) ? 'ready' : 'outdated';
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

export function parseRecipe(doc, kind) {
  const ok = doc != null && typeof doc === 'object'
    && typeof doc.session_id === 'string' && doc.session_id !== ''
    && typeof doc.cwd === 'string' && doc.cwd !== '';
  if (!ok) {
    return { ok: false, error: { kind: 'bad-json', message: `pr ${kind} session --json gave no session_id and cwd` } };
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

// The pane to bring forward for a PR, or null. Candidates have a non-null agent
// `status` on machine `on` (null/'' = local, same as liveAddress) whose cwd is the
// checkout or under it (path boundary); with no cwd, whose branch equals `branch`.
// Rank: blocked, then working, then the rest; ties: focused, then panes() order.
export function agentPaneFor(panes, { on, cwd, branch } = {}) {
  const want = on == null || on === '' ? null : on;
  const raw = typeof cwd === 'string' && cwd !== '' ? cwd : null;
  const c = raw != null && raw.length > 1 && raw.endsWith('/') ? raw.slice(0, -1) : raw;
  let best = null;
  let bestRank = 3;
  let bestFocused = false;
  for (const p of panes ?? []) {
    if (p?.status == null) continue;
    if (machineOf(p.terminal) !== want) continue;
    if (c != null) {
      const pc = p.cwd;
      if (typeof pc !== 'string' || !(pc === c || pc.startsWith(`${c}/`))) continue;
    } else if (p.branch !== branch) {
      continue;
    }
    const rank = p.status === 'blocked' ? 0 : p.status === 'working' ? 1 : 2;
    const focused = Boolean(p.focused);
    if (best == null || rank < bestRank || (rank === bestRank && focused && !bestFocused)) {
      best = p;
      bestRank = rank;
      bestFocused = focused;
    }
  }
  return best;
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
 * deps: {exec, run, panes, spaces} (the herdr functions).
 * target: batch {on, cwd, runId, repo, features}; target {on, cwd, branch, repo, features}.
 * Resolves {ok:true, opened:'focused'|'adopted'|'started'} or {ok:false, outdated, message}; never rejects.
 */
export async function openSession(deps, kind, target) {
  const spec = SESSION_KINDS[kind];
  const { exec, run, panes, spaces } = deps;
  const { on = null, cwd = null, repo = null, features = null } = target;
  if (sessionGate(features, kind) === 'outdated') return OUTDATED;
  const asked = await invoke(exec, { on, cwd }, 'pr', spec.askArgv(target));
  if (!asked.ok) return spec.outdatedRe.test(asked.error.message ?? '') ? OUTDATED : failed(asked.error.message);
  const body = readJson(asked.stdout);
  const got = body.ok ? parseRecipe(body.value, kind) : body;
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
  if (spec.adoptPane) {
    const pane = agentPaneFor(panes(), { on, cwd: checkout, branch: target.branch });
    if (pane) {
      for (const [name, args] of focusSteps(pane.terminal, panes(), spaces())) run(name, args);
      return { ok: true, opened: 'adopted' };
    }
  }
  let res;
  try {
    res = parseRunReply(await exec('hc', hcSessionArgv({
      repo, argv: spec.openArgv(target), sessionId, cwd: checkout, on,
    }), {}));
  } catch (err) {
    res = { ok: false, error: { message: String(err?.message ?? err) } };
  }
  if (!res.ok) return failed(res.error.message);
  run('terminal.keep', { terminal: res.terminal });
  return { ok: true, opened: 'started' };
}
