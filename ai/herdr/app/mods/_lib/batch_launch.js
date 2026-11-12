/**
 * Argv and grouping for launching `pr batch` from the herdr board.
 */

import { AI_STEPS, BUSY_STATES, laneOf } from './batch.js';
import { DEFAULT_FILTERS } from './columns.js';
import { facetView } from './filters.js';

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

/**
 * Keep PRs the shared repo and label chips match, dropping drafts when `drafts` is false.
 * `loaded` is every repo the board has loaded, as for filterRepos (`knownLabels` likewise for labels): a repo chip on one where none
 * of `prs` lives empties the form, as it empties the board, instead of being read as stale.
 */
export function filterForm(prs, {
  repos = [],
  labels = [],
  excludeRepos = [],
  excludeLabels = [],
  drafts = DEFAULT_FILTERS.mineDrafts,
  loaded,
  knownLabels,
} = {}) {
  const list = (prs ?? []).filter((pr) => drafts || !pr.isDraft);
  return facetView(list, { repos, labels, excludeRepos, excludeLabels }, {
    tab: 'batch',
    loadedRepos: loaded,
    knownLabels,
  }).list;
}

/**
 * The batch a Mine column's shortcut sets up, by column key: the steps it turns on, whether
 * AI is on for its step, and whether a rebase also takes PRs only behind. A column absent
 * here gets no shortcut: it waits on someone else, and no step moves a PR out of it.
 *
 * Has Conflicts turns AI on because a plain rebase stops at every real conflict; Has
 * Comments, because the comments step does not run without it. CI Failing leaves AI off:
 * a plain CI step reruns infrastructure flakes, and fixing code is the larger step to opt into.
 */
const rebaseOnly = Object.freeze(AI_STEPS.filter((s) => s === 'rebase'));
export const COLUMN_BATCH = Object.freeze({
  'has-conflicts': { steps: rebaseOnly, ai: true },
  'ci-failing': { steps: ['ci'], ai: false },
  'has-comments': { steps: ['comments'], ai: true },
  approved: { steps: rebaseOnly, ai: false, rebaseBehind: true },
});

/** The shortcut a column `{key, items}` offers, or null: none for a column without a preset or PRs. */
export function columnPreset(col) {
  const preset = COLUMN_BATCH[col?.key];
  return preset && (col.items ?? []).length > 0 ? preset : null;
}

/**
 * The Start form after a shortcut: exactly `keys` selected (none still launching, per
 * `pending`), only the preset's steps on, AI for those steps as the preset says and for
 * every other step as `withAi` had it, and rebase-behind as the preset says.
 */
export function presetForm({ keys, preset, withAi = [], pending = () => false }) {
  const ai = new Set(withAi);
  for (const s of preset.steps) {
    if (preset.ai) ai.add(s); else ai.delete(s);
  }
  return {
    selected: keys.filter((k) => !pending(k)),
    steps: [...preset.steps],
    withAi: [...ai],
    rebaseBehind: Boolean(preset.rebaseBehind),
  };
}

/** Argv after `pr` for `batch run` of one checkout group. */
export function runArgv({
  keys,
  steps,
  withAi = [],
  autoPublish = false,
  rebaseBehind = false,
  selects = {},
  focus = [],
  phaseModels = [],
  checkout,
}) {
  if (!Array.isArray(keys) || keys.length === 0) throw new Error('keys is empty');
  if (checkout == null || checkout === '') throw new Error('checkout is empty');

  const stepList = steps ?? [];
  const withoutReview = stepList.filter((s) => s !== 'review');
  const reviewOnly = withoutReview.length === 0 && stepList.includes('review');
  const wantsReview = stepList.includes('review')
    || Object.values(selects).some((list) => Array.isArray(list) && list.includes('review'));

  // phaseModels is flag argv from model_overrides.js; --phase-model is global, so it leads.
  const argv = [...phaseModels, 'batch', 'run', '--checkout', checkout, '--prs', keys.join(',')];
  if (!reviewOnly && stepList.length > 0) {
    argv.push('--steps', withoutReview.join(','));
  }
  if (wantsReview || reviewOnly) argv.push('--with-review');

  const ai = withAi.filter((s) => AI_STEPS.includes(s));
  if (ai.length > 0) argv.push('--with-ai', ai.join(','));
  if (autoPublish) argv.push('--auto-publish', 'ff');
  // pr batch rebases only conflicting PRs unless told otherwise; the flag is sent only when it
  // changes something, so a machine whose pr predates it still runs the default.
  if (rebaseBehind && stepList.includes('rebase')) argv.push('--rebase', 'behind');

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

/**
 * What a launched `pr batch run` terminal's output says so far.
 *
 * started: the run emitted its `run_started` event, so it exists and owns its PRs.
 * error: the last non-blank line, which is the failure `pr batch` printed when the terminal
 * ends without `started` (a GitHub outage, another run holding the repo's lock).
 */
export function launchOutcome(text) {
  const lines = String(text ?? '').split('\n').map((l) => l.trim()).filter(Boolean);
  const started = lines.some((l) => l.includes('"kind": "run_started"'));
  return { started, error: lines.length ? lines[lines.length - 1] : '' };
}
