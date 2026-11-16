/**
 * How a PR reads: the slots every PR view draws (prs/board.js and prs/compact.js cards and rows,
 * prs/detail.js header, web/public/board_view.js and detail_view.js), decided once. Keys are the
 * document's snake_case. Cards and compact rows never carry a pass status: positive state
 * (CI passing, Approved, a finished batch) is for the detail's ci, decision and busy slots.
 * `presentationOf` re-splices the live review and batch slots onto the document presentation.
 */
import { cardState, words } from './batch.js';
import { hasQueueLanes } from './columns.js';

// _lib/batch.js cardState tones as status kinds (board.presentation.TONE_STATUS).
export const TONE_STATUS = Object.freeze({ failed: 'fail', attention: 'warn', busy: 'running', done: 'pass' });

const status = (kind, text) => ({ kind: 'status', status: kind, text });

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

// Live review slot: a terminal, or a queue ticket when the machine's `pr board` features
// include queue-lanes. Status kinds are _lib/status.js. Queue position (`queued · behind #N`)
// is not a board fact; the line is `queued` or `reviewing`. An older `pr` keeps "Review running".
function reviewStatus(pr) {
  const gated = hasQueueLanes(pr);
  if (pr.reviewing || (gated && pr.queue?.state === 'running')) {
    return status('running', gated ? 'reviewing' : 'Review running');
  }
  if (gated && pr.queue?.state === 'queued') return status('none', 'queued');
  return null;
}

// A document row's presentation with the two client-only slots laid over it: a live review
// terminal and the PR's batch run item. Python's layout is badges = issues + live + reviews +
// labels and row_badges = issues + live, and the document's `review` is always null, so the
// overlay only re-splices the live slot. `item` is prs/workflows.js itemFor, or null.
export function presentationOf(pr, { item = null } = {}) {
  const p = pr.presentation;
  const review = reviewStatus(pr);
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

// What every view draws: the document's presentation with live review and batch slots.
export function prPresentation(pr, opts = {}) {
  return presentationOf(pr, opts);
}
