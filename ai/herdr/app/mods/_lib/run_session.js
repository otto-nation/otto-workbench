/**
 * Open a PR target's pi session from herdr.
 *
 * `pr --branch B target session --json`, asked on the machine, says which session it is
 * (`session_id`) and where it runs (`cwd`); the matching `pr` module owns that and the
 * argv and env, so nothing here builds a pi command line.
 * A live hc terminal carrying the `otto.session` label for that id is brought forward
 * (its pane's space, tab and pane, or `terminal.keep` when no pane shows it); otherwise
 * a kind with `adoptPane` takes an agent pane in the checkout (`otto.worktree` label
 * over cwd) the same way; otherwise `hc run --bg` starts the kind's open argv (which
 * execs pi) in the checkout and `keepStarted` gives it a tab once herdr lists it. A machine whose
 * `pr board` features lack the kind's feature gets OUTDATED_TEXT and no exec.
 */

import { invoke, readJson } from './board_data.js';
import {
  LABEL_SESSION, LABEL_WORKTREE, hcSessionArgv, keepStarted, machineOf, notShownText, parseRunReply,
} from './launch.js';

export const SESSION_KINDS = {
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

function trimFolder(path) {
  const raw = typeof path === 'string' && path !== '' ? path : null;
  return raw != null && raw.length > 1 && raw.endsWith('/') ? raw.slice(0, -1) : raw;
}

function folderMatches(folder, checkout) {
  return typeof folder === 'string' && (folder === checkout || folder.startsWith(`${checkout}/`));
}

function worktreeLabels(terminals) {
  const labels = new Map();
  for (const t of Array.isArray(terminals) ? terminals : []) {
    const address = t?.address;
    const label = trimFolder(t?.labels?.[LABEL_WORKTREE]);
    if (typeof address !== 'string' || label == null) continue;
    labels.set(address, label);
  }
  return labels;
}

// The pane to bring forward for a PR, or null. Machine `on` (null/'' = local, same as
// liveAddress) as today. A terminal's `otto.worktree` label (from `terminals`, keyed by
// address) overrides its pane cwd. Tiers: labelled agent, then unlabelled agent by cwd,
// then (only if `shells`) a non-agent pane in the worktree. With no cwd, branch equals
// `branch` (agents only). Within a tier: blocked, then working, then the rest; ties:
// focused, then panes() order. Empty `terminals` and `shells: false` match today.
export function agentPaneFor(panes, { on, cwd, branch, terminals = [], shells = false } = {}) {
  const want = on == null || on === '' ? null : on;
  const c = trimFolder(cwd);
  const labels = worktreeLabels(terminals);
  let best = null;
  let bestTier = 3;
  let bestRank = 3;
  let bestFocused = false;
  for (const p of panes ?? []) {
    if (machineOf(p.terminal) !== want) continue;
    const isAgent = p?.status != null;
    if (!isAgent && !shells) continue;
    let tier;
    if (c != null) {
      const labelled = typeof p.terminal === 'string' ? labels.get(p.terminal) : undefined;
      if (isAgent && labelled != null && folderMatches(labelled, c)) {
        tier = 0;
      } else if (isAgent && folderMatches(p.cwd, c)) {
        tier = 1;
      } else if (!isAgent && folderMatches(p.cwd, c)) {
        tier = 2;
      } else {
        continue;
      }
    } else if (!isAgent || p.branch !== branch) {
      continue;
    } else {
      tier = 1;
    }
    const rank = p.status === 'blocked' ? 0 : p.status === 'working' ? 1 : 2;
    const focused = Boolean(p.focused);
    if (
      best == null || tier < bestTier
      || (tier === bestTier && (rank < bestRank || (rank === bestRank && focused && !bestFocused)))
    ) {
      best = p;
      bestTier = tier;
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
 * deps: {exec, run, panes, spaces, terminals, sleep} (the herdr functions; sleep: the spawn's `cx.sleep`).
 * target: {on, cwd, branch, repo, features}.
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
    const pane = agentPaneFor(panes(), {
      on, cwd: checkout, branch: target.branch, terminals: deps.terminals?.() ?? [],
    });
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
  if (!(await keepStarted(deps, res.terminal))) return failed(notShownText(res.terminal));
  return { ok: true, opened: 'started' };
}
