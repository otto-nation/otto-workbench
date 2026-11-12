/**
 * Scope helpers for the PRs board: which checkouts the board reads
 * ("this space" or every open space) and how their results merge. Pure
 * values; no imports.
 */

const SKIPPABLE_RE = /not a git repository|not in a repository|origin is not a GitHub remote/i;

const SKIPPABLE_KINDS = new Set(['no-checkout', 'no-origin', 'not-git', 'not-github']);

export function skippable(err) {
  if (SKIPPABLE_KINDS.has(err?.kind)) return true;
  return err?.kind === 'failed' && SKIPPABLE_RE.test(err.message ?? '');
}

// Operator-facing text for a failed target; raw git/gh stderr never reaches the board. `here` names
// the machine the board itself runs on (the desktop app is on a Mac; the web board is a server).
export function friendlyError(err, on, scope = 'this', here = 'this Mac') {
  if (!err) return '';
  const where = on != null ? String(on) : here;
  if (err.kind === 'auth') return `run gh auth login on ${where}`;
  if (err.kind === 'outdated') {
    return `the workbench \`pr\` on ${where} does not match this board (run otto-workbench sync there)`;
  }
  if (err.kind === 'not-git' || err.kind === 'no-origin' || err.kind === 'no-checkout') {
    const hint = scope !== 'all' ? '; open a space in a repo or use All spaces' : '';
    return `this space is not in a git repository${hint}`;
  }
  if (err.kind === 'not-github') return 'origin is not a GitHub remote';
  if (err.kind === 'gh-failed') return err.message ?? '';
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
  let columns = null;
  for (const st of states) {
    if (!st?.repo || !st.prs) continue;
    columns = columns ?? (st.columns?.length ? st.columns : null);
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
  return { prs, me, repos: seen.size, slug, repoSlugs, columns };
}

export function repoId(repo) {
  return `${repo.host ?? ''}/${repo.slug}`;
}

export const REPO_PHASES = Object.freeze([
  'queued', 'loading', 'hydrating', 'loaded', 'failed', 'stale',
]);

export function repoPhase(st) {
  if (REPO_PHASES.includes(st?.phase)) return st.phase;
  if (st?.error) return st.updatedAt != null ? 'stale' : 'failed';
  if (st?.prs == null && st?.pulling) return 'loading';
  if (st?.prs == null) return 'queued';
  return 'loaded';
}

function folderName(cwd) {
  return String(cwd ?? '').split('/').filter(Boolean).pop() ?? '';
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
// target it belongs to, counts of skipped and still-loading targets, and one
// repoStates entry per distinct repo (unknown-repo targets all stay).
export function boardState(targets, states, scope) {
  const shown = [], errors = [], repoStates = [];
  let skipped = 0, loading = 0;
  const seen = new Set();
  targets.forEach((t, i) => {
    const st = states[i] ?? {};
    if (scope === 'all' && skippable(st.error)) { skipped++; return; }
    // updatedAt is the last successful load, so a failed refresh over older data reads as stale.
    if (st.error) errors.push({ on: t.on ?? null, cwd: t.cwd ?? null, error: st.error, updatedAt: st.updatedAt ?? null });
    else if (st.prs == null) loading++;
    shown.push(st);
    if (st.repo) {
      const id = repoId(st.repo);
      if (seen.has(id)) return;
      seen.add(id);
    }
    repoStates.push({
      slug: st.repo?.slug ?? null,
      folder: folderName(t.cwd),
      on: t.on ?? null,
      phase: repoPhase(st),
      error: st.error ?? null,
      partial: Boolean(st.partial),
      owed: { state: st.owedState ?? 'idle', error: st.owedError ?? null },
      updatedAt: st.updatedAt ?? null,
    });
  });
  return { ...mergeStates(shown), errors, skipped, loading, repoStates };
}
