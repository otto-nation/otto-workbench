/**
 * What the page receives: the merged board (scope.js boardState) with errors already in words and no
 * server paths. Pure.
 */
import { friendlyError } from '../../app/mods/_lib/scope.js';

const baseOf = (p) => String(p ?? '').split('/').filter(Boolean).pop() ?? '';

// Listing.py ReviewRow / ReviewRun fields the page uses (historyRows, stamps for GET /api/review).
// Whitelist rather than delete review_file: schema v1 is allowed to grow optional path-bearing keys
// (reviews_root already exists on the document; a worktree/dir field would leak the same way), and
// the page never needs a path — it fetches bodies by stamp. Nested `history` is omitted because
// runsFor already flattens it into reviewRuns[].
const PUBLIC_RUN_KEYS = Object.freeze([
  'stamp', 'head_sha', 'status', 'reviewed_at', 'started_at', 'structured',
  'verdict', 'findings', 'cost_usd', 'input_tokens', 'output_tokens',
  'cache_read_tokens', 'cache_write_tokens', 'duration_ms', 'failure_detail',
  'post', 'review_type', 'mode', 'unpushed_fix_commit', 'reviewed_through',
  'host', 'repo', 'pr_number', 'head_ref', 'base_ref',
]);

function publicRun(run) {
  if (run == null || typeof run !== 'object') return run;
  const out = {};
  for (const k of PUBLIC_RUN_KEYS) {
    if (Object.hasOwn(run, k)) out[k] = run[k];
  }
  return out;
}

function publicReviewRun(entry) {
  if (entry == null || typeof entry !== 'object') return entry;
  const { run, ...rest } = entry;
  return { ...rest, run: publicRun(run) };
}

// A board record as the page may see it: the checkout path stays on the server.
export function publicPr(pr) {
  const { cwd, ...rest } = pr;
  return {
    ...rest,
    hasCheckout: cwd != null,
    reviewRuns: (pr.reviewRuns ?? []).map(publicReviewRun),
  };
}

export function toSnapshot(view, { name, version, updatedAt = null, refreshing = false, discoveryError = null }) {
  const v = view ?? { prs: [], me: '', repos: 0, repoSlugs: [], errors: [], skipped: 0, loading: 0, repoStates: [] };
  const errors = [];
  if (discoveryError) errors.push({ where: name, folder: '', message: `herdr-core: ${discoveryError}` });
  for (const e of v.errors ?? []) {
    // updatedAt (scope.js boardState) is the last good load: set means stale data is shown, null means failed.
    errors.push({ where: e.on ?? name, folder: baseOf(e.cwd), message: friendlyError(e.error, e.on, 'all', name), updatedAt: e.updatedAt ?? null });
  }
  return {
    name, version, updatedAt, refreshing,
    loading: view == null || (v.loading ?? 0) > 0,
    me: v.me ?? '',
    repos: v.repos ?? 0,
    repoSlugs: [...(v.repoSlugs ?? [])],
    skipped: v.skipped ?? 0,
    errors,
    prs: (v.prs ?? []).map(publicPr),
    repoStates: (v.repoStates ?? []).map((r) => ({
      slug: r.slug,
      folder: r.folder,
      on: r.on,
      phase: r.phase,
      partial: r.partial,
      updatedAt: r.updatedAt,
      error: r.error == null ? null : friendlyError(r.error, r.on, 'all', name),
    })),
  };
}
