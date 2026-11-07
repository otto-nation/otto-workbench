/**
 * `pr batch status --full --offline` JSON (`cli.pr_batch_views.cmd_status`).
 *
 * Document: {schema_version, active, run_dir, run}. Items live at run.items.
 * Identity is item.key ("owner/name#N"); state is item.status (ItemStatus).
 * item.pr in this document is the int; the non-full report puts the key string
 * on item.pr. busyPrs reads run.items and key, or a string pr.
 * No run: CLI prints "pr batch: no batch runs yet" (not JSON).
 */

// ItemStatus values a batch is still working a PR in (ai/lib/batch/model.py).
export const BUSY_STATES = ['queued', 'waiting_admission', 'running'];

export function busyPrs(status, repo) {
  const out = new Set();
  const items = status?.run?.items;
  if (!Array.isArray(items)) return out;
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    if (!BUSY_STATES.includes(item.status)) continue;
    const ident = typeof item.key === 'string' ? item.key
      : typeof item.pr === 'string' ? item.pr
      : null;
    if (ident == null) continue;
    const i = ident.lastIndexOf('#');
    if (i < 1) continue;
    if (ident.slice(0, i) !== repo) continue;
    const n = Number(ident.slice(i + 1));
    if (!Number.isInteger(n)) continue;
    out.add(n);
  }
  return out;
}
