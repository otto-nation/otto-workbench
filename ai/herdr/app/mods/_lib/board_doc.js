/**
 * The board document `pr board` prints, as the records the views read. The wire is snake_case and
 * Python owns every fact, lane, badge and column; this file only renames, so the views keep the
 * camelCase record and nothing here derives anything.
 */

export const PHASES = Object.freeze(['listing', 'status', 'owed']);

// A full status is 18 s locally; remote runs are slower, and herdr's 30 s default would fail them.
export const PHASE_TIMEOUT_MS = Object.freeze({ listing: 60000, status: 180000, owed: 120000 });

export function boardArgv(cwd, phase, { prs = null, stamp = null, offline = false } = {}) {
  return [
    'board', '--schema-version', '1', '--checkout', cwd, '--phase', phase,
    ...(prs?.length ? ['--prs', prs.join(',')] : []),
    ...(stamp ? ['--if-changed', stamp] : []),
    ...(offline ? ['--offline'] : []),
  ];
}

// One document row as the record the views read. `run` stays the document's snake_case run.
export function rowToPr(row) {
  return {
    repoSlug: row.repo, number: row.number, title: row.title, author: row.author,
    branch: row.branch, baseBranch: row.base_branch, url: row.url, headSha: row.head_sha,
    labels: row.labels ?? [], isDraft: row.is_draft, state: row.state,
    additions: row.additions, deletions: row.deletions, changedFiles: row.changed_files,
    hydrated: row.hydrated, busy: row.busy, isMine: row.is_mine,
    hasNewChangesSinceReview: row.has_new_changes_since_review,
    reviewRuns: (row.review_runs ?? []).map((run) => ({ postedEvent: run.posted_event ?? null, run })),
    lanes: row.lanes, issues: row.issues, presentation: row.presentation,
  };
}

const keyOf = (r) => `${r.repoSlug ?? ''}#${r.number}`;

// A complete document replaces the rows; a partial one (`status --prs`, `owed`) patches them by
// (repo, number) and appends rows the board did not have.
export function mergeRows(prev, rows, complete) {
  if (complete) return rows;
  const patch = new Map(rows.map((r) => [keyOf(r), r]));
  const out = (prev ?? []).map((r) => patch.get(keyOf(r)) ?? r);
  const have = new Set(out.map(keyOf));
  for (const r of rows) if (!have.has(keyOf(r))) out.push(r);
  return out;
}

// Marks the PRs a live review terminal is running for. `running` maps PR number to {terminal}.
export function withRunning(prs, running) {
  return (prs ?? []).map((p) => ({
    ...p, reviewing: running.has(p.number), reviewTerminal: running.get(p.number)?.terminal ?? null,
  }));
}

// A document as the per-target state the board caches. `prev` is that target's last state, whose
// rows a partial document patches. A failed repo clears `phase` so repoPhase reads stale or failed.
export function docToState(doc, ctx, prev = null) {
  const repo = doc.repos?.[0] ?? {};
  const state = {
    ...(prev ?? {}),
    repo: { slug: repo.slug, host: repo.host, on: ctx.on ?? null, cwd: ctx.cwd },
    me: doc.viewer ?? prev?.me ?? '',
    columns: doc.columns?.length ? doc.columns : prev?.columns,
    reviewsRoot: doc.reviews_root || prev?.reviewsRoot || '',
    partial: Boolean(repo.partial),
  };
  if (repo.phase === 'failed' || repo.error) {
    state.error = { kind: repo.error?.kind ?? 'failed', message: repo.error?.message ?? '' };
    delete state.phase;
    return state;
  }
  state.error = null;
  state.phase = repo.phase;
  state.rows = mergeRows(prev?.rows, (doc.rows ?? []).map(rowToPr), doc.complete !== false);
  return state;
}
