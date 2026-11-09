/**
 * Label contract for herdr PR terminals. PR 6/7 depend on it.
 */

import { classifyCode, readJson, toolCall } from './board_data.js';

export const LABEL_PR = 'otto.pr';
export const LABEL_ACTION = 'otto.action';
// Constant on every terminal the PR surfaces start: the one label `hc ls --label` can select on.
export const LABEL_APP = 'otto.app';
export const APP_NAME = 'pr';

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

export function hcRunArgv({ repo, number, action, cwd, on, argv }) {
  const args = ['run', '--bg', '--json', '--cwd', cwd];
  // A terminal started on another machine has no login PATH either, so `pr` would be coreutils'.
  const run = toolCall({ on }, argv[0], argv.slice(1));
  if (on) {
    args.push('--on', on);
  }
  args.push(
    '--label',
    `${LABEL_PR}=${prKey(repo, number)}`,
    '--label',
    `${LABEL_ACTION}=${action}`,
    '--label',
    `${LABEL_APP}=${APP_NAME}`,
    '--',
    run.cmd,
    ...run.args,
  );
  return args;
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
  'post-comment': 'Post', 'post-approve': 'Approval', 'post-request-changes': 'Request for changes',
  dismiss: 'Dismissal',
};

// Notification text for a finished action terminal; exitCode null means it ended without one.
export function outcomeText(action, exitCode) {
  const what = DONE[action] ?? 'Action';
  if (exitCode === 0) return { level: 'success', title: `${what} finished` };
  const why = exitCode == null ? 'ended unexpectedly' : `failed (exit ${exitCode})`;
  return { level: 'error', title: `${what} ${why}` };
}
