/**
 * Action table for PRs board detail buttons.
 */

import { prKey, terminalCommand } from './launch.js';

export const ACTION_IDS = [
  'review',
  'rerun',
  'recover',
  'post-draft',
  'post-comment',
  'post-approve',
  'post-request-changes',
  'publish-comment',
  'publish-approve',
  'publish-request-changes',
  'dismiss',
  'cancel',
  'watch',
];

const LABELS = {
  review: 'Review',
  rerun: 'Re-run',
  recover: 'Recover',
  'post-draft': 'Post draft',
  'post-comment': 'Post comment',
  'post-approve': 'Approve',
  'post-request-changes': 'Request changes',
  'publish-comment': 'Publish draft',
  'publish-approve': 'Approve',
  'publish-request-changes': 'Request changes',
  dismiss: 'Dismiss',
  cancel: 'Cancel',
  watch: 'Watch',
};

const POSTS = {
  'post-draft': { mode: 'post', event: null },
  'post-comment': { mode: 'post', event: 'comment' },
  'post-approve': { mode: 'post', event: 'approve' },
  'post-request-changes': { mode: 'post', event: 'request_changes' },
  'publish-comment': { mode: 'publish', event: 'comment' },
  'publish-approve': { mode: 'publish', event: 'approve' },
  'publish-request-changes': { mode: 'publish', event: 'request_changes' },
};

const FAILED = new Set(['partial', 'error']);

const FEATURE = 'review-publish';

function confirmMessage(id, pr, sha) {
  switch (id) {
    case 'post-draft':
      return `Post draft on ${pr}? Puts the review of ${sha} on GitHub as a draft only you can see.`;
    case 'post-comment':
      return `Post comment on ${pr}? Publishes the review of ${sha} as a comment.`;
    case 'post-approve':
      return `Approve ${pr}? Publishes the review of ${sha} as an approval.`;
    case 'post-request-changes':
      return `Request changes on ${pr}? Publishes the review of ${sha} as a change request.`;
    case 'publish-comment':
      return `Publish draft on ${pr}? Publishes the draft of ${sha} as a comment.`;
    case 'publish-approve':
      return `Approve ${pr}? Publishes the draft of ${sha} as an approval.`;
    case 'publish-request-changes':
      return `Request changes on ${pr}? Publishes the draft of ${sha} as a change request.`;
    default:
      return '';
  }
}

export function prArgv(id, number, repoDir) {
  const n = String(number);
  switch (id) {
    case 'review':
      return ['pr', 'review', '--no-post', '--pr', n, '--repo-dir', repoDir];
    case 'rerun':
      return ['pr', 'review', '--no-post', '--force', '--pr', n, '--repo-dir', repoDir];
    case 'recover':
      return ['pr', 'review', '--recover', '--pr', n, '--repo-dir', repoDir];
    case 'dismiss':
      return ['pr', 'review', '--dismiss', '--pr', n, '--repo-dir', repoDir];
    default: {
      const post = POSTS[id];
      if (!post) return null;
      if (post.mode === 'publish') {
        return ['pr', 'review', '--publish', post.event, '--pr', n, '--repo-dir', repoDir];
      }
      if (post.event) {
        return ['pr', 'review', '--post', '--publish', post.event, '--pr', n, '--repo-dir', repoDir];
      }
      return ['pr', 'review', '--post', '--pr', n, '--repo-dir', repoDir];
    }
  }
}

function unknownReason(run, repoDir) {
  if (run === undefined) return 'Review state unknown';
  if (!repoDir) return 'No checkout for this repo';
  return null;
}

function prReason(id, { run, repoDir, headMoved, batchBusy, live, features }) {
  const unknown = unknownReason(run, repoDir);
  if (unknown) return unknown;
  if (batchBusy || live != null) return 'A review or batch is running on this PR';
  if (id === 'review') {
    return run === null ? null : 'Already reviewed — use Re-run';
  }
  if (!run) return 'Not reviewed yet';
  const failed = FAILED.has(run.status);
  const posted = run.post != null;
  const pending = run.post?.event === 'pending';
  if (id === 'recover') return failed ? null : 'Last run completed';
  const post = POSTS[id];
  if (post) {
    if (!(features ?? []).includes(FEATURE)) {
      return 'Update pr on this machine to post (otto-workbench sync)';
    }
    if (post.mode === 'post') {
      if (pending) return 'Draft already on GitHub — publish it';
      if (posted) return 'Already posted';
      if (headMoved) return 'Head moved since the review — re-run';
      if (failed) return 'Run incomplete — recover first';
      return null;
    }
    if (!pending) return 'No draft on GitHub';
    if (headMoved) return 'Head moved since the draft — re-run';
    return null;
  }
  if (id === 'dismiss') {
    if (pending) return 'Draft already on GitHub';
    if (posted) return 'Already posted';
  }
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
    const post = POSTS[id];
    if (post && run?.head_sha) {
      const sha = String(run.head_sha).slice(0, 7);
      const pr = prKey(repo, number);
      action.confirm = { pr, event: post.event, sha, message: confirmMessage(id, pr, sha) };
    }
    return action;
  });
}
