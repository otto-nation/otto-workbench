/**
 * One target's pull of the board: the `pr board` phases in order, each published as it lands so
 * the first paint does not wait for the slowest phase. Listing paints rows (CI still unknown),
 * status hydrates them, owed adds what each PR is owed an answer for. A caller never runs two pulls
 * of one target at once; that is what keeps a partial document consistent with the state it patches.
 */

import { loadBoard, owedDue, runningFor } from './board_data.js';
import { docToState, withRunning } from './board_doc.js';

const NONE = Object.freeze({ listing: null, status: null, offline: null });

// The state with live review terminals laid over its rows.
function withTerminals(state, entries) {
  const slug = state.repo?.slug;
  return { ...state, prs: withRunning(state.rows ?? [], slug ? runningFor(entries ?? [], slug) : new Map()) };
}

// One phase: the new state, the unchanged one, or an error. `slot` names the stamp it answers to.
async function step(exec, ctx, cur, phase, slot, { offline = false, entries, now }) {
  const stamp = cur?.stamps?.[slot] ?? null;
  const read = await loadBoard(exec, ctx, phase, { stamp, offline });
  if (!read.ok) return { error: read.error };
  const stamps = { ...(cur?.stamps ?? NONE) };
  if (read.unchanged) {
    return { state: withTerminals({ ...cur, error: null, updatedAt: now }, entries), unchanged: true };
  }
  stamps[slot] = read.doc.stamp ?? null;
  const next = docToState(read.doc, ctx, cur);
  next.stamps = stamps;
  if (!next.error) next.updatedAt = now;
  return { state: withTerminals(next, entries), doc: read.doc };
}

// What each PR is owed, patched onto the rows; a failed read keeps the last good state.
export async function pullOwed(exec, ctx, prev, { entries, now }, publish) {
  const owed = await step(exec, ctx, prev, 'owed', 'owed', { entries, now });
  if (owed.error) return { ok: true, kept: true };
  publish({ ...owed.state, owedAt: now });
  return { ok: true };
}

/**
 * Listing, then status, then (when `owed`) owed. `listingOnly` re-reads the cache with no GitHub
 * call and falls through to a full pull when there is nothing cached. `fresh` ignores every stamp.
 * `isCurrent()` is asked between phases; false abandons the pull. Resolves {ok:true} or
 * {ok:false, error}; a repo that failed in the document is published with its error, not returned.
 */
export async function pullTarget(exec, ctx, prev, opts, publish) {
  const { listingOnly = false, fresh = false, owed = false, entries = [], now = Date.now() } = opts;
  const isCurrent = opts.isCurrent ?? (() => true);
  const base = { entries, now };
  // A fresh pull asks for every row: no stamp is sent, so nothing answers unchanged.
  let cur = fresh && prev ? { ...prev, stamps: NONE, owedAt: null } : prev;

  if (listingOnly && cur?.repo) {
    const r = await step(exec, ctx, cur, 'listing', 'offline', { ...base, offline: true });
    if (r.error) return { ok: false, error: r.error };
    if (!isCurrent()) return { ok: true };
    if (r.unchanged || r.doc.repos?.[0]?.phase !== 'queued') {
      publish(r.state);
      if (r.state.error || !owed || !owedDue(r.state.owedAt, now)) return { ok: true };
      return pullOwed(exec, ctx, r.state, { entries, now }, publish);
    }
  }

  const listing = await step(exec, ctx, cur, 'listing', 'listing', base);
  if (listing.error) return { ok: false, error: listing.error };
  if (!isCurrent()) return { ok: true };
  cur = listing.unchanged || listing.state.error ? listing.state : { ...listing.state, phase: 'hydrating' };
  publish(cur);
  if (cur.error) return { ok: true };

  const status = await step(exec, ctx, cur, 'status', 'status', base);
  if (status.error) return { ok: false, error: status.error };
  if (!isCurrent()) return { ok: true };
  cur = status.state.error ? status.state : { ...status.state, phase: 'loaded' };
  publish(cur);
  if (cur.error || !owed || !owedDue(cur.owedAt, now)) return { ok: true };

  return pullOwed(exec, ctx, cur, { entries, now }, publish);
}
