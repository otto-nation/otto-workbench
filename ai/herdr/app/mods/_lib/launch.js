/**
 * Label contract for herdr PR terminals. PR 6/7 depend on it.
 */

import { classifyCode, readJson, toolCall } from './board_data.js';

export const LABEL_PR = 'otto.pr';
export const LABEL_ACTION = 'otto.action';
export const LABEL_REPO = 'otto.repo';
// Constant on every terminal the PR surfaces start: the one label `hc ls --label` can select on.
export const LABEL_APP = 'otto.app';
export const APP_NAME = 'pr';
// A pi session terminal: the session id the recipe named.
export const LABEL_SESSION = 'otto.session';
// Worktree a session claims. Written by ai/lib/pr/agent_worktree.py; overrides the pane cwd.
export const LABEL_WORKTREE = 'otto.worktree';

export function prKey(repo, n) {
  return `${repo}#${n}`;
}

export function prLabel(repo, n) {
  return `${LABEL_PR}=${prKey(repo, n)}`;
}

export function parsePrKey(s) {
  if (typeof s !== 'string') return null;
  const hash = s.lastIndexOf('#');
  if (hash <= 0) return null;
  const repo = s.slice(0, hash);
  const tail = s.slice(hash + 1);
  if (!/^[0-9]+$/.test(tail)) return null;
  const number = Number(tail);
  if (!Number.isInteger(number)) return null;
  return { repo, number };
}

export function machineOf(id) {
  if (typeof id !== 'string') return null;
  const slash = id.indexOf('/');
  if (slash <= 0) return null;
  return id.slice(0, slash);
}

function hcLaunchArgv({ cwd, on, argv, labels }) {
  const args = ['run', '--bg', '--json', '--cwd', cwd];
  // A terminal started on another machine has no login PATH either, so `pr` would be coreutils'.
  const run = toolCall({ on }, argv[0], argv.slice(1));
  if (on) {
    args.push('--on', on);
  }
  for (const label of labels) {
    args.push('--label', label);
  }
  args.push('--', run.cmd, ...run.args);
  return args;
}

export function hcRunArgv({ repo, number, action, cwd, on, argv }) {
  return hcLaunchArgv({
    cwd,
    on,
    argv,
    labels: [
      `${LABEL_PR}=${prKey(repo, number)}`,
      `${LABEL_ACTION}=${action}`,
      `${LABEL_APP}=${APP_NAME}`,
    ],
  });
}

export function hcBatchArgv({ repo, cwd, on, argv }) {
  return hcLaunchArgv({
    cwd,
    on,
    argv,
    labels: [
      `${LABEL_REPO}=${repo}`,
      `${LABEL_ACTION}=batch`,
      `${LABEL_APP}=${APP_NAME}`,
    ],
  });
}

// A terminal running the given session argv (which execs pi) in its checkout.
// The session label is how herdr finds it again: `hc ls --json` lists no labels, but `--label`
// filters by them.
export function hcSessionArgv({ repo, argv, sessionId, cwd, on }) {
  return hcLaunchArgv({
    cwd,
    on,
    argv,
    labels: [
      ...(repo ? [`${LABEL_REPO}=${repo}`] : []),
      `${LABEL_ACTION}=session`,
      `${LABEL_APP}=${APP_NAME}`,
      `${LABEL_SESSION}=${sessionId}`,
    ],
  });
}

// How long keepStarted waits for herdr to list a terminal hc just started, and how often it looks.
export const KEEP_WAIT_MS = 3000;
export const KEEP_STEP_MS = 100;

/**
 * Give a terminal `hc run --bg` just started a tab. herdr hears of it a moment after hc
 * replies and refuses `terminal.keep` until then ("no running terminal"), so this waits
 * until herdr's `terminals()` lists `address` before keeping it.
 * deps: {run, terminals, sleep(ms)} (sleep: the spawn's `cx.sleep`). Resolves true once
 * kept, false when herdr has not listed it after KEEP_WAIT_MS (nothing is run then).
 */
export async function keepStarted({ run, terminals, sleep }, address) {
  for (let waited = 0; ; waited += KEEP_STEP_MS) {
    if ((terminals() ?? []).some((t) => t?.address === address)) {
      run('terminal.keep', { terminal: address });
      return true;
    }
    if (waited >= KEEP_WAIT_MS) return false;
    await sleep(KEEP_STEP_MS);
  }
}

// The error for a started terminal herdr never listed: it runs, so say where to find it.
export function notShownText(address) {
  return `started ${address}, but herdr did not show it within ${KEEP_WAIT_MS / 1000}s (hc attach ${address})`;
}

export function terminalCommand(name, id) {
  return { name, args: { terminal: id } };
}

export function parseRunReply({ code, stdout, stderr }) {
  const coded = classifyCode({ code, stdout, stderr });
  if (coded) return { ok: false, error: coded };
  const parsed = readJson(stdout ?? '');
  if (!parsed.ok) return parsed;
  const address = parsed.value?.address;
  if (typeof address !== 'string') {
    return { ok: false, error: { kind: 'failed', message: 'no address in hc reply' } };
  }
  return { ok: true, terminal: address };
}

const DONE = {
  review: 'Review', rerun: 'Review', recover: 'Recovery',
  'post-draft': 'Draft post', 'post-comment': 'Post', 'post-approve': 'Approval',
  'post-request-changes': 'Request for changes',
  'publish-comment': 'Publish', 'publish-approve': 'Approval',
  'publish-request-changes': 'Request for changes',
  dismiss: 'Dismissal',
  'batch-fix': 'Fix',
};

// Notification text for a finished action terminal; exitCode null means it ended without one.
export function outcomeText(action, exitCode) {
  const what = DONE[action] ?? 'Action';
  if (exitCode === 0) return { level: 'success', title: `${what} finished` };
  const why = exitCode == null ? 'ended unexpectedly' : `failed (exit ${exitCode})`;
  return { level: 'error', title: `${what} ${why}` };
}
