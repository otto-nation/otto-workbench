/**
 * Scope helpers for the PRs board: which checkouts the board reads
 * ("this space" or every open space) and how their results merge. Pure
 * values; no imports.
 */

const SKIPPABLE_RE = /not a git repository|not in a repository|origin is not a GitHub remote/i;

export function skippable(err) {
  return err?.kind === 'failed' && SKIPPABLE_RE.test(err.message ?? '');
}

// Operator-facing text for a failed target; raw git/gh stderr never reaches the board.
export function friendlyError(err, on, scope = 'this') {
  if (!err) return '';
  const where = on != null ? String(on) : 'this Mac';
  if (err.kind === 'auth') return `run gh auth login on ${where}`;
  const what = err.command || 'a command';
  if (err.kind === 'timeout') return `${what} took too long on ${where}`;
  if (err.kind === 'exec') {
    // herdr's exec throws with no reason when output passes its size cap.
    if (!err.message) return `${what} failed on ${where} with no reason from herdr (often output over its size limit)`;
    return `${what} failed on ${where}: ${err.message}`;
  }
  if (err.kind === 'missing-tool') {
    if (Array.isArray(err.tools) && err.tools.length) {
      return `install ${err.tools.join(', ')} on ${where}`;
    }
    return `a program could not start on ${where}: ${err.message}`;
  }
  const msg = err.message ?? '';
  if (SKIPPABLE_RE.test(msg)) {
    const hint = scope !== 'all' ? '; open a space in a repo or use All spaces' : '';
    if (/not a git repository/i.test(msg)) {
      return `this space is not in a git repository${hint}`;
    }
    if (/not in a repository/i.test(msg)) {
      return `this space has no folder${hint}`;
    }
  }
  return msg;
}

// Distinct (machine, folder) among the open spaces, in space order then pane
// order. Prefers the panes' live folders and falls back to the space folder.
export function spaceTargets(spaces, panes) {
  const seen = new Set();
  const out = [];
  const paneList = panes ?? [];
  for (const s of spaces ?? []) {
    const tabIds = new Set((s.tabs ?? []).map((t) => t.id));
    const folders = [];
    const local = new Set();
    for (const p of paneList) {
      if (!tabIds.has(p.tab)) continue;
      if (!p.cwd || local.has(p.cwd)) continue;
      local.add(p.cwd);
      folders.push(p.cwd);
    }
    if (!folders.length && s.folder) folders.push(s.folder);
    const on = s.machine ?? null;
    for (const folder of folders) {
      const id = `${on ?? ''}\u0000${folder}`;
      if (seen.has(id)) continue;
      seen.add(id);
      out.push({ on, cwd: folder });
    }
  }
  return out;
}

// Merge per-target states into one PR list: one entry per repo (two spaces on
// one repo count once), each PR tagged with its repo slug, plus repoSlugs in
// first-seen order (one per distinct repoId, even when that repo has no PRs).
export function mergeStates(states) {
  const seen = new Set();
  const prs = [];
  const repoSlugs = [];
  let me = '';
  let slug = null;
  for (const st of states) {
    if (!st?.repo || !st.prs) continue;
    const id = repoId(st.repo);
    if (seen.has(id)) continue;
    seen.add(id);
    repoSlugs.push(st.repo.slug);
    slug = slug ?? st.repo.slug;
    me = me || st.me || '';
    for (const pr of st.prs) {
      prs.push({
        ...pr, repoSlug: st.repo.slug, host: st.repo.host, viewer: st.me,
        on: st.repo.on ?? null, cwd: st.repo.cwd ?? null,
      });
    }
  }
  return { prs, me, repos: seen.size, slug, repoSlugs };
}

export function repoId(repo) {
  return `${repo.host ?? ''}/${repo.slug}`;
}

// Cache keys no longer in the keep set.
export function staleKeys(keys, keep) {
  const hold = new Set(keep);
  return [...keys].filter((k) => !hold.has(k));
}

// Unique per repo so two repos' #12 never share an element id.
export function cardId(pr) {
  return `pr-${pr.repoSlug ?? ""}#${pr.number}`;
}

// First target per known repo; unknown-repo targets all stay so they get resolved.
export function representatives(targets, repoOf) {
  const seen = new Set();
  const out = [];
  for (const t of targets ?? []) {
    const id = repoOf(t);
    if (id == null) { out.push(t); continue; }
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(t);
  }
  return out;
}

// What the board shows for these targets: merged PRs, each failure with the
// target it belongs to, and counts of skipped and still-loading targets.
export function boardState(targets, states, scope) {
  const shown = [], errors = [];
  let skipped = 0, loading = 0;
  targets.forEach((t, i) => {
    const st = states[i] ?? {};
    if (scope === 'all' && skippable(st.error)) { skipped++; return; }
    if (st.error) errors.push({ on: t.on ?? null, cwd: t.cwd ?? null, error: st.error });
    else if (st.prs == null) loading++;
    shown.push(st);
  });
  return { ...mergeStates(shown), errors, skipped, loading };
}
