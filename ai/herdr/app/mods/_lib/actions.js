/**
 * Action table for team-prs detail buttons.
 */

import { prKey, terminalCommand } from './launch.js';

export const ACTION_IDS = [
  'review',
  'rerun',
  'recover',
  'post-comment',
  'post-approve',
  'post-request-changes',
  'dismiss',
  'cancel',
  'watch',
];

const LABELS = {
  review: 'Review',
  rerun: 'Re-run',
  recover: 'Recover',
  'post-comment': 'Post comment',
  'post-approve': 'Approve',
  'post-request-changes': 'Request changes',
  dismiss: 'Dismiss',
  cancel: 'Cancel',
  watch: 'Watch',
};

const POST_EVENTS = {
  'post-comment': 'comment',
  'post-approve': 'approve',
  'post-request-changes': 'request_changes',
};

const FAILED = new Set(['partial', 'error']);

export function prArgv(id, number, repoDir) {
  const n = String(number);
  switch (id) {
    case 'review':
      return ['pr', 'review', '--no-post', '--pr', n, '--repo-dir', repoDir];
    case 'rerun':
      return ['pr', 'review', '--no-post', '--force', '--pr', n, '--repo-dir', repoDir];
    case 'recover':
      return ['pr', 'review', '--recover', '--pr', n, '--repo-dir', repoDir];
    case 'post-comment':
    case 'post-approve':
    case 'post-request-changes':
      return ['pr', 'review', '--post', '--event', POST_EVENTS[id], '--pr', n, '--repo-dir', repoDir];
    case 'dismiss':
      return ['pr', 'review', '--dismiss', '--pr', n, '--repo-dir', repoDir];
    default:
      return null;
  }
}

function unknownReason(run, repoDir) {
  if (run === undefined) return 'Review state unknown';
  if (!repoDir) return 'No checkout for this repo';
  return null;
}

function prReason(id, { run, repoDir, headMoved, batchBusy, live }) {
  const unknown = unknownReason(run, repoDir);
  if (unknown) return unknown;
  if (batchBusy || live != null) return 'A review or batch is running on this PR';
  if (id === 'review') {
    return run === null ? null : 'Already reviewed — use Re-run';
  }
  if (!run) return 'Not reviewed yet';
  const failed = FAILED.has(run.status);
  const posted = run.post != null;
  if (id === 'recover') return failed ? null : 'Last run completed';
  if (POST_EVENTS[id]) {
    if (posted) return 'Already posted';
    if (headMoved) return 'Head moved since the review — re-run';
    if (failed) return 'Run incomplete — recover first';
    return null;
  }
  if (id === 'dismiss' && posted) return 'Already posted';
  return null;
}

function terminalReason(id, { batchBusy, live, finished }) {
  if (id === 'cancel') {
    if (live != null) return null;
    return batchBusy ? 'Started by pr batch' : 'Nothing running';
  }
  return (live ?? finished) ? null : 'No terminal';
}

export function actionsFor(ctx) {
  const { repo, number, repoDir, run, live, finished } = ctx;
  return ACTION_IDS.map((id) => {
    const isPr = id !== 'cancel' && id !== 'watch';
    const reason = isPr ? prReason(id, ctx) : terminalReason(id, ctx);
    const action = { id, label: LABELS[id], enabled: reason == null, reason };
    if (isPr) action.argv = prArgv(id, number, repoDir);
    if (id === 'cancel' && live != null) {
      action.command = terminalCommand('terminal.kill', live);
    }
    if (id === 'watch' && (live ?? finished)) {
      action.show = live ?? finished;
    }
    const event = POST_EVENTS[id];
    if (event && run?.head_sha) {
      action.confirm = { pr: prKey(repo, number), event, sha: String(run.head_sha).slice(0, 7) };
    }
    return action;
  });
}
