/**
 * Document lanes and list filters for the PR board.
 *
 * Python owns lane membership (`pr.lanes`); `laneOf` reads it. Filters hide
 * drafts and landed unless their chips are on.
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
  { key: 'has-comments', label: 'Has Comments' },
  { key: 'changes-requested', label: 'Changes Requested' },
  { key: 'in-review', label: 'In Review' },
  { key: 'awaiting-review', label: 'Awaiting Review' },
  { key: 'approved', label: 'Approved' },
  { key: 'in-queue', label: 'In Queue' },
  { key: 'merged', label: 'Merged' },
];

// A document row always carries its lanes (Python owns them; `pr board` via rowToPr is the only producer),
// so a row without them is malformed and groupColumns drops it.  `reviewing` picks the variant the live
// review terminal changes. `board` is 'team' or 'mine'.
export function laneOf(pr, board) {
  const l = pr.lanes ?? {};
  return pr.reviewing ? l[`${board}_reviewing`] : l[board];
}

// The document's column specs for one board, in order.
export function columnsFor(columns, board) {
  return (columns ?? []).filter((c) => c.board === board).sort((a, b) => a.order - b.order);
}

// Show-menu flag defaults
export const DEFAULT_FILTERS = Object.freeze({
  drafts: false,
  mineDrafts: false,
  landed: false,
  empty: false,
  hideBots: false,
});

// Stored boolean wins; otherwise DEFAULT_FILTERS[key], or false for an unknown key.
export function filterOn(filters, key) {
  const stored = filters?.[key];
  if (typeof stored === 'boolean') return stored;
  return DEFAULT_FILTERS[key] ?? false;
}

export function isMine(pr, me) {
  if (typeof pr.isMine === 'boolean') return pr.isMine;
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

function fold(s) {
  return String(s ?? '').toLowerCase();
}

/** Distinct repo slugs (first-seen) and label names (case-insensitive sort) among `prs`: the chips a view offers. */
export function filterFacets(prs) {
  const repos = [];
  const seenRepos = new Set();
  const labels = [];
  const seenLabels = new Set();
  for (const pr of prs ?? []) {
    const slug = pr?.repoSlug;
    if (slug && !seenRepos.has(fold(slug))) {
      seenRepos.add(fold(slug));
      repos.push(slug);
    }
    for (const name of pr?.labels ?? []) {
      if (!name || seenLabels.has(fold(name))) continue;
      seenLabels.add(fold(name));
      labels.push(name);
    }
  }
  labels.sort((a, b) => fold(a).localeCompare(fold(b)));
  return { repos, labels };
}

// A PR matches when it carries any selected label. A selection naming no label among `prs` is
// ignored, as a stale repo is, so a label whose last PR merged does not empty the view.
// `knownLabels` lists the labels the board offers as chips; without it, those among `prs` stand in.
export function filterLabels(prs, labels, knownLabels) {
  const known = new Set((knownLabels ?? filterFacets(prs).labels).map(fold));
  const active = new Set([...(labels ?? [])].map(fold).filter((l) => known.has(l)));
  if (active.size === 0) return prs;
  return prs.filter((p) => (p.labels ?? []).some((n) => active.has(fold(n))));
}

export function filterPrs(prs, filters, me) {
  const drafts = filterOn(filters, 'drafts');
  const landed = filterOn(filters, 'landed');
  // facets are _lib/filters.js facetView
  return prs.filter((pr) => {
    if (!drafts && pr.isDraft) return false;
    if (!landed && laneOf(pr, 'team') === 'landed') return false;
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
