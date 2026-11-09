/**
 * Join `pr review --list --schema-version 1` rows onto GitHub PR records.
 *
 * Imports nothing. Field names match listing.py (`RunPost`, `ReviewRow`).
 * Derivations follow ui-code `toTeamPrInfo` with workbench post event names.
 */

export const POST_STATE_BY_EVENT = {
  approve: 'APPROVED',
  requested_changes: 'CHANGES_REQUESTED',
  comment: 'COMMENTED',
};

export function posted(run) {
  return run != null && run.post != null;
}

export function health(run) {
  if (run != null && run.status === 'partial') return 'Partial';
  if (run != null && run.status === 'error') return 'Error';
  // 'Interrupted' needs terminals, PR 5
  return 'ok';
}

export const LANDED_CAP = 100;
export const LANDED_DAYS = 30;

function rowsOf(listing) {
  if (Array.isArray(listing)) return listing;
  if (listing != null && Array.isArray(listing.reviews)) return listing.reviews;
  return [];
}

function hostOk(row, host) {
  return row.host == null || row.host === '' || row.host === host;
}

export function matchesRepo(row, repo, host) {
  return row.repo === repo && row.mode === 'pr' && typeof row.pr_number === 'number' && hostOk(row, host);
}

export function runsFor(row) {
  return [row, ...(row.history ?? [])].map((run) => ({
    postedEvent: run.post ? (run.post.event || '') : null,
    head_sha: run.head_sha ?? null,
    status: run.status ?? null,
    reviewed_at: run.reviewed_at ?? null,
    run,
  }));
}

function pickRow(rows) {
  let best = null;
  for (const row of rows) {
    if (best == null || (row.reviewed_at ?? '') > (best.reviewed_at ?? '')) best = row;
  }
  return best;
}

function reviewStateOf(pr, runs, me) {
  const mine = (me ?? '').toLowerCase();
  const reviews = pr.latestReviews ?? [];
  for (let i = reviews.length - 1; i >= 0; i--) {
    const r = reviews[i];
    if ((r.login ?? '').toLowerCase() !== mine) continue;
    if (r.state === 'DISMISSED' || r.state === 'PENDING') continue;
    return r.state;
  }
  for (const run of runs) {
    const mapped = POST_STATE_BY_EVENT[run.postedEvent];
    if (run.postedEvent != null && mapped) return mapped;
  }
  return null;
}

export function joinReviews(prs, listing, repo, host, me) {
  const rows = rowsOf(listing);
  const mine = (me ?? '').toLowerCase();
  return prs.map((pr) => {
    const match = pickRow(
      rows.filter((row) => matchesRepo(row, repo, host) && row.pr_number === pr.number),
    );
    const reviewRuns = match ? runsFor(match) : [];
    const myReviewState = reviewStateOf(pr, reviewRuns, me);
    return {
      ...pr,
      reviewRuns,
      myReviewState,
      isRequestedReviewer:
        (pr.requestedReviewers ?? []).some((l) => l.toLowerCase() === mine) && myReviewState === null,
      hasNewChangesSinceReview: reviewRuns.length > 0 && reviewRuns[0].head_sha !== pr.headSha,
      // needs the otto.pr hc label PR 5 introduces
      reviewing: false,
      health: health(reviewRuns[0]?.run),
    };
  });
}

export function landedCandidates(prs, listing, repo, host) {
  const present = new Set(prs.map((p) => p.number));
  const best = new Map();
  for (const row of rowsOf(listing)) {
    if (!matchesRepo(row, repo, host) || present.has(row.pr_number)) continue;
    const prev = best.get(row.pr_number);
    if (prev == null || (row.reviewed_at ?? '') > (prev.reviewed_at ?? '')) best.set(row.pr_number, row);
  }
  return [...best.values()]
    .sort((a, b) => (b.reviewed_at ?? '').localeCompare(a.reviewed_at ?? ''))
    .map((row) => row.pr_number)
    .slice(0, LANDED_CAP);
}

export function keepLanded(list, nowMs) {
  const day = 24 * 60 * 60 * 1000;
  return list.filter((entry) => {
    if (entry.mergedAt == null || entry.mergedAt === '') return false;
    const t = Date.parse(entry.mergedAt);
    if (Number.isNaN(t)) return false;
    return t >= nowMs - LANDED_DAYS * day && t <= nowMs + day;
  });
}
