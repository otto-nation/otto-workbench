/**
 * The fix buttons a PR's detail offers for its own problems: one batch step per problem, started
 * for that one PR. The problem comes from the document row's `issues` kinds (board.columns.issues),
 * so the button list is decided where the badge is. Only the viewer's own PRs get any.
 *
 * AI is on for every fix: a plain rebase stops at the first real conflict, the comments step does
 * not run without it, and a CI fix that only reruns cannot fix code. The run is a `pr batch run`
 * item, so it shows in the Batch tab and the board's busy badge like any other batch.
 */

import { COLUMN_BATCH } from './batch_launch.js';

// The step a fix runs is the one its lane's shortcut runs (batch_launch.js COLUMN_BATCH).
const stepsOf = (lane) => COLUMN_BATCH[lane].steps;

// Issue kind -> its fix. Two kinds may share one (changes requested and owed comments both want
// their threads answered), so the list is deduplicated by fix id.
const FIX_FOR = Object.freeze({
  conflicts: { id: 'fix-rebase', label: 'Rebase', steps: stepsOf('has-conflicts') },
  'ci-failing': { id: 'fix-ci', label: 'Fix CI', steps: stepsOf('ci-failing') },
  'has-comments': { id: 'fix-comments', label: 'Answer comments', steps: stepsOf('has-comments') },
  'changes-requested': { id: 'fix-comments', label: 'Answer comments', steps: stepsOf('has-comments') },
});

/** The fixes for `pr`'s present problems, in the order the badges read; none for someone else's PR. */
export function fixesFor(pr) {
  if (!pr?.isMine || pr.state === 'MERGED' || pr.state === 'CLOSED') return [];
  const seen = new Set();
  const out = [];
  for (const issue of pr.issues ?? []) {
    const fix = FIX_FOR[issue.kind];
    if (!fix || seen.has(fix.id)) continue;
    seen.add(fix.id);
    out.push({ ...fix, ai: true });
  }
  return out;
}
