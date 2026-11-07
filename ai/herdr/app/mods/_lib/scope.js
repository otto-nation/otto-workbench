/**
 * Scope helpers for the team-prs board: which checkouts the board reads
 * ("this space" or every open space) and how their results merge. Pure
 * values; no imports.
 */

// Operator-facing text for a failed target; raw git/gh stderr never reaches the board.
export function friendlyError(err, on) {
  if (!err) return '';
  const where = on != null ? String(on) : 'this Mac';
  if (err.kind === 'auth') return `run gh auth login on ${where}`;
  if (err.kind === 'unreachable') return `${where} is unreachable`;
  if (err.kind === 'missing-tool') return `install ${err.message} on ${where}`;
  if (/not a git repository/i.test(err.message ?? '')) {
    return 'this space is not in a git repository; open a space in a repo or use All spaces';
  }
  if (/Not in a repository/i.test(err.message ?? '')) {
    return 'this space has no folder; open a space in a repo or use All spaces';
  }
  return err.message ?? '';
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
    const id = `${st.repo.host ?? ''}/${st.repo.slug}`;
    if (seen.has(id)) continue;
    seen.add(id);
    slug = slug ?? st.repo.slug;
    me = me || st.me || '';
    for (const pr of st.prs) prs.push({ ...pr, repoSlug: st.repo.slug });
  }
  return { prs, me, repos: seen.size, slug };
}
