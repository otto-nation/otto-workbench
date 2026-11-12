/**
 * How a PR reads: the slots every PR view draws (prs/board.js and prs/compact.js cards and rows,
 * prs/detail.js header, web/public/board_view.js and detail_view.js), decided once. The client twin of
 * board.presentation.present; tests/fixtures/board_presentation.json holds the two to one output, so
 * keys are the document's snake_case. Cards and compact rows never carry a pass status: positive state
 * (CI passing, Approved, a finished batch) is for the detail's ci, decision and busy slots.
 */
import { prIssues } from './columns.js';
import { cardState, words } from './batch.js';

// ceiling: legacyPresentation is an interim client twin of board.presentation, kept equal by the
// golden fixture, for the web server's rows, which carry no `presentation`. Upgrade trigger: once
// the web reads `pr board` rows, delete it and draw row.presentation through presentationOf alone.

// _lib/batch.js cardState tones as status kinds (board.presentation.TONE_STATUS).
export const TONE_STATUS = Object.freeze({ failed: 'fail', attention: 'warn', busy: 'running', done: 'pass' });

const DECISIONS = Object.freeze({
  APPROVED: ['pass', 'Approved'],
  CHANGES_REQUESTED: ['warn', 'Changes requested'],
  REVIEW_REQUIRED: ['none', 'Review required'],
});
const CI_KINDS = new Set(['ci-failing', 'ci-running', 'ci-unknown', 'ci-pending']);
const DETAIL_HIDDEN = new Set([...CI_KINDS, 'changes-requested']);

const status = (kind, text) => ({ kind: 'status', status: kind, text });
const plain = (kind, text) => ({ kind, status: null, text });
const fromIssue = (i) => status(i.status, i.label);

// The stats row's three strings (board.presentation.stats_text); the detail passes file-summary counts.
export function statsText({ additions = 0, deletions = 0, files = 0 } = {}) {
  return { additions: `+${additions}`, deletions: `-${deletions}`, files: `${files} ${files === 1 ? 'file' : 'files'}` };
}

// A batch run item's state as a status badge, without the "Batch · " prefix; null when cardState has none.
export function batchStatus(item) {
  const state = item ? cardState(item) : null;
  if (state == null) return null;
  return status(TONE_STATUS[state.tone] ?? 'none', state.text);
}

// A run row's item state: batchStatus, or the raw status as none for a closed item.
export function itemStatus(item) {
  return batchStatus(item) ?? { kind: 'status', status: 'none', text: words(item?.status) };
}

function busyOf(pr, item) {
  const s = batchStatus(item);
  if (s) return { ...s, text: `Batch · ${s.text}` };
  return pr.busy ? status('running', 'Batch · working') : null;
}

function ciOf(pr, raw) {
  if (pr.state === 'MERGED') return null;
  const issue = raw.find((i) => CI_KINDS.has(i.kind));
  if (issue) return fromIssue(issue);
  return pr.ciStatus === 'passing' ? status('pass', 'CI passing') : null;
}

function decisionOf(value) {
  if (!value) return null;
  const known = DECISIONS[value];
  if (known) return status(known[0], known[1]);
  const words = String(value).toLowerCase().replace(/_/g, ' ');
  return status('none', words.charAt(0).toUpperCase() + words.slice(1));
}

function tagsOf(pr) {
  const out = [];
  if (pr.state === 'MERGED') out.push('Merged');
  if (pr.inMergeQueue) out.push('In merge queue');
  if (pr.isDraft) out.push('Draft');
  return out;
}

// A document row's presentation with the two client-only slots laid over it: a live review
// terminal and the PR's batch run item. Python's layout is badges = issues + live + reviews +
// labels and row_badges = issues + live, and the document's `review` is always null, so the
// overlay only re-splices the live slot. `item` is prs/workflows.js itemFor, or null.
export function presentationOf(pr, { item = null } = {}) {
  const p = pr.presentation;
  const review = pr.reviewing ? status('running', 'Review running') : null;
  const fromItem = batchStatus(item);
  const busy = fromItem ? { ...fromItem, text: `Batch · ${fromItem.text}` } : p.busy;
  if (review === p.review && busy === p.busy) return p;
  const shown = (b) => b != null && b.status !== 'pass';
  const live = [review, busy].filter(shown);
  const n = p.issues.length;
  const docLive = [p.review, p.busy].filter(shown).length;
  return {
    ...p, review, busy,
    badges: [...p.badges.slice(0, n), ...live, ...p.badges.slice(n + docLive)],
    row_badges: [...p.row_badges.slice(0, n), ...live],
  };
}

// What every view draws: the document's presentation when the row has one, else the client twin.
export function prPresentation(pr, opts = {}) {
  return pr.presentation ? presentationOf(pr, opts) : legacyPresentation(pr, opts);
}

function legacyPresentation(pr, { item = null } = {}) {
  const raw = prIssues(pr);
  const issues = raw.map(fromIssue);
  const review = pr.reviewing ? status('running', 'Review running') : null;
  const busy = busyOf(pr, item);
  const live = [review, busy].filter((b) => b != null && b.status !== 'pass');
  const runs = (pr.reviewRuns ?? []).length;
  const reviews = runs > 0 ? [plain('text', `${runs} ${runs === 1 ? 'review' : 'reviews'}`)] : [];
  const labels = (pr.labels ?? []).slice(0, 2).map((name) => plain('tag', name));
  return {
    ref: pr.repoSlug ? `${pr.repoSlug.split('/').pop()}#${pr.number}` : `#${pr.number}`,
    title: pr.title ?? '',
    tags: tagsOf(pr),
    author: pr.author ?? '',
    branch: pr.branch ?? '',
    base_branch: pr.baseBranch ?? '',
    stats: statsText({ additions: pr.additions ?? 0, deletions: pr.deletions ?? 0, files: pr.changedFiles ?? 0 }),
    issues,
    review,
    busy,
    ci: ciOf(pr, raw),
    decision: decisionOf(pr.reviewDecision),
    detail_issues: raw.filter((i) => !DETAIL_HIDDEN.has(i.kind)).map(fromIssue),
    badges: [...issues, ...live, ...reviews, ...labels],
    row_badges: [...issues, ...live],
  };
}
