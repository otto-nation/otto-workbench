/**
 * Argv and grouping for launching `pr batch` from the herdr board.
 */

import { BUSY_STATES, laneOf } from './batch.js';

/** Step names a batch launch can request. */
export const STEP_NAMES = ['rebase', 'ci', 'comments', 'review'];

const AI_STEPS = new Set(['rebase', 'ci', 'comments']);
const DONE_LANES = new Set(['done', 'closed', 'attention']);

/** Group board PRs by machine+repo; PRs with no cwd are unrunnable. */
export function groupByRepo(prs) {
  const groups = [];
  const unrunnable = [];
  const index = new Map();
  for (const pr of prs ?? []) {
    const repo = pr?.repoSlug;
    const number = pr?.number;
    if (!repo || !Number.isInteger(number)) continue;
    const key = `${repo}#${number}`;
    const cwd = pr.cwd;
    if (cwd == null || cwd === '') {
      unrunnable.push({ key, reason: 'no checkout of this repo on its machine' });
      continue;
    }
    const gk = `${pr.on ?? ''}\0${repo}`;
    let g = index.get(gk);
    if (!g) {
      g = { repo, on: pr.on, cwd, keys: [] };
      index.set(gk, g);
      groups.push(g);
    }
    g.keys.push(key);
  }
  return { groups, unrunnable };
}

/** Argv after `pr` for `batch run` of one checkout group. */
export function runArgv({
  keys,
  steps,
  withAi = [],
  autoPublish = false,
  selects = {},
  focus = [],
  checkout,
}) {
  if (!Array.isArray(keys) || keys.length === 0) throw new Error('keys is empty');
  if (checkout == null || checkout === '') throw new Error('checkout is empty');

  const stepList = steps ?? [];
  const withoutReview = stepList.filter((s) => s !== 'review');
  const reviewOnly = withoutReview.length === 0 && stepList.includes('review');
  const wantsReview = stepList.includes('review')
    || Object.values(selects).some((list) => Array.isArray(list) && list.includes('review'));

  const argv = ['batch', 'run', '--checkout', checkout, '--prs', keys.join(',')];
  if (!reviewOnly && stepList.length > 0) {
    argv.push('--steps', withoutReview.join(','));
  }
  if (wantsReview || reviewOnly) argv.push('--with-review');

  const ai = withAi.filter((s) => AI_STEPS.has(s));
  if (ai.length > 0) argv.push('--with-ai', ai.join(','));
  if (autoPublish) argv.push('--auto-publish', 'ff');

  const effectiveSelects = { ...selects };
  if (reviewOnly) {
    for (const key of keys) {
      if (!Object.hasOwn(effectiveSelects, key)) effectiveSelects[key] = ['review'];
    }
  }
  for (const key of keys) {
    if (!Object.hasOwn(effectiveSelects, key)) continue;
    argv.push('--select', `${key}=${effectiveSelects[key].join(',')}`);
  }
  for (const raw of focus) {
    const text = String(raw ?? '').trim();
    if (text) argv.push('--focus', text);
  }
  return argv;
}

/** Argv after `pr` for `batch cancel`. */
export function cancelArgv(runId, { kill = false } = {}) {
  if (runId == null || runId === '') throw new Error('runId is empty');
  return ['--schema-version', '2', 'batch', 'cancel', ...(kill ? ['--kill'] : []), runId];
}

/** Finished-item counts from a parseRunDoc result. */
export function runProgress(parsed) {
  if (!parsed?.ok) return { done: 0, total: 0 };
  const items = parsed.items ?? [];
  let done = 0;
  for (const item of items) {
    if (!DONE_LANES.has(laneOf(item))) continue;
    if (BUSY_STATES.includes(item.status)) continue;
    done += 1;
  }
  return { done, total: items.length };
}
