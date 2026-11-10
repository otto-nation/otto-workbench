/** A PR's changed files, loaded once per head (as prs/main.js ensureFiles), and a file's patch. */
import { loadFiles, loadPatch } from '../../app/mods/_lib/board_data.js';

export const FILES_CACHE_MAX = 10;

export function createFiles({ exec, max = FILES_CACHE_MAX }) {
  const byKey = new Map();
  const keyOf = (pr) => `${pr.on ?? ''}\u0000${pr.repoSlug}#${pr.number}@${pr.headSha ?? ''}`;
  const ctxOf = (pr) => ({ cwd: pr.cwd ?? null, on: pr.on ?? null });

  async function files(pr) {
    const key = keyOf(pr);
    if (byKey.has(key)) return byKey.get(key);
    const pending = loadFiles(exec, ctxOf(pr), pr.repoSlug, pr.number);
    byKey.set(key, pending);
    if (byKey.size > max) byKey.delete(byKey.keys().next().value);
    const result = await pending;
    if (!result.ok) byKey.delete(key);
    return result;
  }

  async function patch(pr, path) {
    const list = await files(pr);
    if (!list.ok) return list;
    const file = list.files.find((f) => f.path === path);
    if (!file) return { ok: false, notFound: true, error: { kind: 'failed', message: 'not a file of this PR' } };
    if (!file.hasPatch) return { ok: true, patch: null };
    return loadPatch(exec, ctxOf(pr), pr.repoSlug, pr.number, file);
  }

  return { files, patch };
}
