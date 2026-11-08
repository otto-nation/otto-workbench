/**
 * Scope helpers for the team-prs board: which checkouts the board reads
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
  if (err.kind === 'unreachable') return `${where} is unreachable`;
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

// Every distinct (machine, folder) among the open spaces, in space order.
export function spaceTargets(spaces) {
  const seen = new Set();
  const out = [];
  for (const s of spaces ?? []) {
    if (!s.folder) continue;
    const on = s.machine ?? null;
    const id = `${on ?? ''}\u0000${s.folder}`;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push({ on, cwd: s.folder });
  }
  return out;
}

// Merge per-target states into one PR list: one entry per repo (two spaces on
// one repo count once), each PR tagged with its repo slug.
export function mergeStates(states) {
  const seen = new Set();
  const prs = [];
  let me = '';
  let slug = null;
  for (const st of states) {
    if (!st?.repo || !st.prs) continue;
    const id = repoId(st.repo);
    if (seen.has(id)) continue;
    seen.add(id);
    slug = slug ?? st.repo.slug;
    me = me || st.me || '';
    for (const pr of st.prs) {
      prs.push({
        ...pr, repoSlug: st.repo.slug, host: st.repo.host, viewer: st.me,
        on: st.repo.on ?? null,
      });
    }
  }
  return { prs, me, repos: seen.size, slug };
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
