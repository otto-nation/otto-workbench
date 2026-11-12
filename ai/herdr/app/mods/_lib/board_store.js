/**
 * The board's pulls over many checkouts, shared by the herdr-app mod (prs/board_view.js) and the web
 * server (web/server/board.js): one cache entry per target, the tools check once per machine, the live
 * review terminals once per pull, one pull per known repo (representatives), and each target's
 * `pr board` phases through board_pull.js pullTarget. No timers and no host API: the host chooses the
 * targets, decides when to pull, and redraws on onChange.
 */
import { checkTools, loadTerminals } from './board_data.js';
import { pullTarget, eachByMachine, FULL_PULL_MS } from './board_pull.js';
export { FULL_PULL_MS };
import { boardState, repoId, representatives, staleKeys } from './scope.js';

export const ctxKey = (c) => JSON.stringify({ on: c?.on ?? null, cwd: c?.cwd ?? null });

// What a host shows that needs on-demand data: nothing. `owed` is the Has Comments read.
export const NO_WANT = Object.freeze({ owed: false });

export function createBoardStore({ exec, now = Date.now }) {
  const cache = new Map();
  const repoByKey = new Map();
  const toolsByOn = new Map();
  // Last offline→online fall-through per target, so a queued cache is not pulled every 15s.
  const fellAt = new Map();

  const stateOf = (c) => cache.get(ctxKey(c)) ?? {};
  const begin = (key) => cache.set(key, { ...(cache.get(key) ?? {}), pulling: true });
  const end = (key) => {
    const cur = cache.get(key);
    if (!cur?.pulling) return;
    const next = { ...cur };
    delete next.pulling;
    cache.set(key, next);
  };
  // Rows stay; dropping `phase` lets repoPhase read the error (stale with rows, failed without).
  const fail = (key, error) => {
    const next = { ...(cache.get(key) ?? {}), error, stale: true };
    delete next.phase;
    cache.set(key, next);
  };

  // Owed on demand. A repo reads `loading` from the moment a view wants owed until an answer lands
  // ('ready') or fails ('error', with the reason); an answer dropped because the view stopped wanting
  // it, or a pull that never reached the owed phase, returns `loading` to `idle`. 'ready' and 'error'
  // persist across later pulls (the rows shown are the last good ones) until the next answer.
  const OWED_SETTLED = new Set(['ready', 'error']);
  function settleOwed(key, done, onChange) {
    const cur = cache.get(key);
    if (!cur) return;
    let next = null;
    if (done?.owed === 'read') next = { owedState: 'ready', owedError: null };
    else if (done?.owed === 'failed') next = { owedState: 'error', owedError: done.owedError, owedAt: now() };
    else if (cur.owedState === 'loading') next = { owedState: 'idle', owedError: null };
    if (!next) return;
    cache.set(key, { ...cur, ...next });
    onChange();
  }

  async function ensureTools(ctx, key, isCurrent) {
    const onKey = String(ctx.on);
    let have = toolsByOn.get(onKey);
    if (!have) {
      const tools = await checkTools(exec, ctx);
      if (!isCurrent(key)) return null;
      if (!tools.ok) { fail(key, tools.error); return null; }
      toolsByOn.set(onKey, (have = tools.have));
    }
    const missing = ['pr', 'gh'].filter((n) => !have[n]);
    if (missing.length) {
      fail(key, { kind: 'missing-tool', tools: missing, message: missing.join(', ') });
      return null;
    }
    return have;
  }

  function terminalsFor(ctx, cycle) {
    if (!cycle.has('terminals')) cycle.set('terminals', loadTerminals(exec, ctx));
    return cycle.get('terminals');
  }

  async function pullOne(ctx, key, cycle, { listingOnly, fresh, want, isCurrent }, { onChange, onCommit }) {
    const prev = cache.get(key);
    if (!listingOnly || !prev?.repo) {
      const have = await ensureTools(ctx, key, isCurrent);
      if (!have || !isCurrent(key)) return;
    }
    const terms = await terminalsFor(ctx, cycle);
    if (!isCurrent(key)) return;
    let cur = cache.get(key);
    if (want().owed && !OWED_SETTLED.has(cur?.owedState)) {
      // settleOwed resolves 'loading' after the pull, whatever the outcome.
      cache.set(key, (cur = { ...(cur ?? {}), owedState: 'loading', owedError: null }));
      onChange();
    }
    // `owed` is a getter: pullTarget asks it when owed would start and before publishing the answer.
    const done = await pullTarget(exec, ctx, cur, {
      listingOnly, fresh, owed: () => Boolean(want().owed), entries: terms.entries, now: now(),
      isCurrent: () => isCurrent(key),
      fellAt: fellAt.get(key), onFall: (t) => fellAt.set(key, t),
    }, (state) => {
      if (state.repo?.slug) repoByKey.set(key, repoId(state.repo));
      cache.set(key, { ...state, stale: false });
      onCommit(state, ctx);
      onChange();
    });
    if (!done.ok) fail(key, done.error);
    settleOwed(key, done.ok ? done : null, onChange);
  }

  return {
    active: (targets) => representatives(targets ?? [], (c) => repoByKey.get(ctxKey(c))),
    stateOf,
    view: (targets, scope) => boardState(targets, targets.map(stateOf), scope),
    reviewsRoot(on) {
      for (const st of cache.values()) {
        if ((st.repo?.on ?? '') === (on ?? '') && st.reviewsRoot) return st.reviewsRoot;
      }
      return null;
    },
    // One pull over `targets`. `isCurrent(key)` is asked between steps; false abandons that target.
    // `want()` says which on-demand data the host shows now ({owed}). `keep()` names the targets
    // whose cache survives (default: `targets`).
    async pull({ targets, listingOnly = false, fresh = false, want = () => NO_WANT, isCurrent = () => true, keep = null },
      { onChange = () => {}, onCommit = () => {} } = {}) {
      const cycle = new Map();
      const opts = { listingOnly, fresh, want, isCurrent };
      for (const ctx of targets) begin(ctxKey(ctx));
      onChange();
      await eachByMachine(targets, (c) => c.on, async (ctx) => {
        const key = ctxKey(ctx);
        try {
          if (!ctx.cwd) {
            cache.set(key, { error: { kind: 'failed', message: 'Not in a repository' }, stale: false });
            return;
          }
          await pullOne(ctx, key, cycle, opts, { onChange, onCommit });
          onChange();
        } finally {
          end(key);
        }
      });
      const retained = keep ? keep() : targets;
      const hold = retained.map(ctxKey);
      for (const k of staleKeys(cache.keys(), hold)) {
        cache.delete(k);
        repoByKey.delete(k);
        fellAt.delete(k);
      }
      const terms = cycle.has('terminals') ? await cycle.get('terminals') : null;
      return { terminals: terms?.ok ? terms.entries : null };
    },
  };
}
