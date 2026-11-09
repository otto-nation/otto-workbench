/**
 * Classify a PR record into team / mine kanban columns and apply list filters.
 *
 * The record uses ui-code TeamPrInfo names:
 *   state: 'OPEN' | 'MERGED' | 'CLOSED'
 *   isDraft: boolean
 *   inMergeQueue: boolean
 *   mergeable: 'MERGEABLE' | 'CONFLICTING' | 'UNKNOWN'
 *   ciStatus: 'passing' | 'failing' | 'pending' | 'none'
 *   reviewDecision: 'APPROVED' | 'CHANGES_REQUESTED' | ... | null
 *   myReviewState: string | null
 *   isRequestedReviewer: boolean
 *   hasNewChangesSinceReview: boolean
 *   reviewing: boolean — ui-code's isReviewQueued, stored on the PR here
 *   author: login
 *   reviewRuns: array NEWEST FIRST of { postedEvent: null | string }
 *     null = unposted; any non-null, including 'dismissed', counts as posted
 */

export const TEAM_COLUMNS = [
  { key: 'open', label: 'Open' },
  { key: 'needs-my-review', label: 'Needs My Review' },
  { key: 'in-queue', label: 'In Queue' },
  { key: 'review-ready', label: 'Review Ready' },
  { key: 'reviewed', label: 'Reviewed' },
  { key: 'landed', label: 'Landed' },
];

export const MINE_COLUMNS = [
  { key: 'has-conflicts', label: 'Has Conflicts' },
  { key: 'ci-failing', label: 'CI Failing' },
  { key: 'changes-requested', label: 'Changes Requested' },
  { key: 'draft', label: 'Draft' },
  { key: 'pending-review', label: 'Pending Review' },
  { key: 'approved', label: 'Approved' },
  { key: 'in-queue', label: 'In Queue' },
  { key: 'merged', label: 'Merged' },
];

export function teamColumn(pr) {
  if (pr.reviewing) {
    return 'in-queue';
  }
  if (pr.state === 'MERGED' || pr.inMergeQueue) {
    return 'landed';
  }
  if (pr.isRequestedReviewer || pr.hasNewChangesSinceReview) {
    return 'needs-my-review';
  }
  if (pr.reviewRuns.length > 0) {
    const latestPostedIdx = pr.reviewRuns.findIndex((r) => r.postedEvent !== null);
    const hasNewerUnposted =
      latestPostedIdx === -1
        ? pr.reviewRuns.some((r) => r.postedEvent === null)
        : pr.reviewRuns.slice(0, latestPostedIdx).some((r) => r.postedEvent === null);
    if (hasNewerUnposted) return 'review-ready';
  }
  if (pr.myReviewState) {
    return 'reviewed';
  }
  if (pr.reviewRuns.length > 0) {
    return 'reviewed';
  }
  return 'open';
}

export function mineColumn(pr) {
  if (pr.state === 'MERGED') return 'merged';
  if (pr.inMergeQueue) return 'in-queue';
  if (pr.mergeable === 'CONFLICTING') return 'has-conflicts';
  if (pr.ciStatus === 'failing') return 'ci-failing';
  if (pr.reviewDecision === 'CHANGES_REQUESTED') return 'changes-requested';
  if (pr.isDraft) return 'draft';
  if (pr.reviewDecision === 'APPROVED' && (pr.ciStatus === 'passing' || pr.ciStatus === 'none'))
    return 'approved';
  return 'pending-review';
}

export function isMine(pr, me) {
  const login = String(pr.author ?? '').toLowerCase();
  const who = String(pr.viewer ?? me ?? '').toLowerCase();
  return login === who;
}

// `loaded` lists every repo the board has loaded, even one with no open PRs; a saved selection
// naming a repo outside it is ignored. Without it, the repos seen in `prs` stand in.
export function filterRepos(prs, repos, loaded) {
  const known = new Set((loaded ?? prs.map((p) => p.repoSlug)).map((r) => String(r ?? "").toLowerCase()));
  const active = new Set(
    [...(repos ?? [])].map((r) => String(r).toLowerCase()).filter((r) => known.has(r)),
  );
  if (active.size === 0) return prs;
  return prs.filter((p) => active.has(String(p.repoSlug ?? "").toLowerCase()));
}

export function filterPrs(prs, filters, me) {
  const drafts = Boolean(filters?.drafts);
  const landed = Boolean(filters?.landed);
  const authors = Array.isArray(filters?.authors) ? filters.authors : [];
  // The viewer is never an author filter: Team excludes them by design, and the Mine tab is theirs.
  const who = String(me ?? '').toLowerCase();
  const authorSet = new Set(authors.map((a) => String(a).toLowerCase()).filter((a) => a !== who));
  return prs.filter((pr) => {
    const login = String(pr.author ?? '').toLowerCase();
    if (!drafts && pr.isDraft) return false;
    if (!landed && teamColumn(pr) === 'landed') return false;
    if (authorSet.size > 0 && !authorSet.has(login)) return false;
    return true;
  });
}

// Bucket PRs into the given columns; unknown colOf keys are dropped.
export function groupColumns(prs, colOf, columns) {
  const buckets = {};
  for (const c of columns) buckets[c.key] = [];
  for (const pr of prs) {
    const key = colOf(pr);
    if (buckets[key]) buckets[key].push(pr);
  }
  return columns.map((c) => ({ ...c, items: buckets[c.key] ?? [] }));
}

export function visibleColumns(cols, showEmpty) {
  return showEmpty ? cols : cols.filter(c => c.items.length > 0);
}
