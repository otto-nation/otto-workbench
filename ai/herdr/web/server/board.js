/**
 * The web board's data: the checkouts the agent's herdr-core terminals sit in (discovered with
 * `hc ls --json`), pulled through _lib/board_store.js — the same store the herdr-app mod pulls through
 * (`pr board` phases, owed when due, one checkout per repo) — and merged with boardState(…, 'all').
 * Each repo is published as it loads; changeFeed in sse.js broadcasts a snapshot when the board
 * changed, or a small status event when only the clock moved. One pull at a time; refreshes asked for
 * meanwhile collapse into one more (full wins over listing; a fresh ask sticks to the next full pull).
 */
import { NO_WANT, createBoardStore, ctxKey } from '../../app/mods/_lib/board_store.js';
import { readJson } from '../../app/mods/_lib/board_data.js';
import { lsAllArgv, terminalTargets } from '../lib/targets.js';
import { toSnapshot } from '../lib/snapshot.js';

export { ctxKey };

// `want()` is what the connected pages show that needs on-demand data (main.js: owed while any page
// is on Mine); the store asks it when owed would start and again when the answer lands.
export function createBoard({ exec, name, version, now = Date.now, onChange = () => {}, want = () => NO_WANT }) {
  const store = createBoardStore({ exec, now });
  let targets = [];
  let discoveryError = null;
  let terminals = [];
  let view = null;
  let pulledAt = null;
  let inflight = null;
  // null: nothing queued; true: a listing-only pull; false: a full pull.
  let queued = null;
  let queuedFresh = false;
  // Last reviews root per machine: the store only has one after a commit, so a first pull that
  // fails before publish would otherwise leave GET /api/review with nowhere to read.
  const reviewsRootByOn = new Map();

  async function discover() {
    try {
      const ran = await exec('hc', lsAllArgv(), {});
      if (ran.code !== 0) { discoveryError = (ran.stderr || `hc ls exit ${ran.code}`).trim(); return; }
      const parsed = readJson(ran.stdout);
      if (!parsed.ok) { discoveryError = 'hc ls printed no JSON'; return; }
      targets = terminalTargets(parsed.value);
      discoveryError = null;
    } catch (err) {
      // Keep the last targets: a core restart must not empty the board.
      discoveryError = String(err?.message ?? err);
    }
  }

  const merged = () => store.view(store.active(targets), 'all');

  async function pull(listingOnly, fresh) {
    await discover();
    const done = await store.pull({ targets: store.active(targets), listingOnly, fresh, want, keep: () => targets }, {
      onChange: () => { view = merged(); onChange(snapshot()); },
      onCommit: (state, ctx) => {
        if (state.reviewsRoot) reviewsRootByOn.set(ctx.on ?? '', state.reviewsRoot);
      },
    });
    if (done.terminals) terminals = done.terminals;
    view = merged();
    pulledAt = now();
  }

  function snapshot() {
    return toSnapshot(view, { name, version, updatedAt: pulledAt, refreshing: inflight != null, discoveryError });
  }

  function refresh({ listingOnly = false, fresh = false } = {}) {
    if (inflight) {
      queued = queued === null ? listingOnly : queued && listingOnly;
      queuedFresh = queuedFresh || fresh;
      return inflight;
    }
    // A manual refresh asks for every row, not for what the stamps say changed: on a full pull only,
    // as the mod's Refresh; a fresh ask queued behind a listing-only pull waits for the next full one.
    const runFresh = fresh && !listingOnly;
    if (fresh && listingOnly) queuedFresh = true;
    // Through .then so `inflight` is set before the first onChange reads it (refreshing: true).
    inflight = Promise.resolve().then(async () => {
      try {
        onChange(snapshot());
        await pull(listingOnly, runFresh);
      } finally {
        inflight = null;
        onChange(snapshot());
        if (queued !== null) {
          const next = { listingOnly: queued, fresh: queuedFresh };
          queued = null;
          queuedFresh = false;
          refresh(next);
        }
      }
    });
    return inflight;
  }

  return {
    refresh,
    snapshot,
    findPr: (repo, number) => view?.prs.find((p) => p.repoSlug === repo && p.number === number) ?? null,
    reviewsRootFor: (on) => store.reviewsRoot(on) ?? reviewsRootByOn.get(on ?? '') ?? null,
    prTerminals: () => terminals,
    pulledAt: () => pulledAt,
  };
}

/**
 * Polls only while a page is connected (hub.count() > 0): full every fullMs, listing-only every
 * listingMs — the mod's 180 s / 15 s. wake() is called when a page connects: a never-pulled or
 * fullMs-old board pulls fully, a listingMs-old one pulls the listing; a page entering Mine
 * (`owedWanted`) gets a listing pull now, which reads owed when due — the mod's comments-only refresh.
 */
export function createPoller({ board, hub, fullMs, listingMs, now = Date.now, every = setInterval, stopEvery = clearInterval }) {
  const timers = [];
  return {
    start() {
      timers.push(every(() => { if (hub.count() > 0) board.refresh(); }, fullMs));
      timers.push(every(() => { if (hub.count() > 0) board.refresh({ listingOnly: true }); }, listingMs));
    },
    stop() { for (const t of timers.splice(0)) stopEvery(t); },
    wake(owedWanted = false) {
      const at = board.pulledAt();
      if (at == null || now() - at >= fullMs) board.refresh();
      else if (owedWanted || now() - at >= listingMs) board.refresh({ listingOnly: true });
    },
  };
}
