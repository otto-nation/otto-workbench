/**
 * The web board's data: the checkouts the agent's herdr-core terminals sit in, pulled through _lib
 * in the order prs/board_view.js pullFull/pullListing use (tools → repo → login → open PRs ∥
 * listing ∥ batch doc ∥ terminals → landed → assemble), merged with scope.js boardState(…, 'all').
 * Each repo is published as it loads. changeFeed in sse.js broadcasts a whole snapshot when the board
 * changed, or a small status event (updatedAt, refreshing) when only the clock moved.
 * One pull at a time; refreshes asked for meanwhile collapse into one more (full wins over listing).
 */
import { boardState, repoId, representatives, staleKeys } from '../../app/mods/_lib/scope.js';
import { busyPrs } from '../../app/mods/_lib/batch.js';
import {
  assemble, checkTools, loadBatchDoc, loadLanded, loadListing, loadOpenPrs, loadRepo, loadTerminals, loadUser,
  readJson, runningFor,
} from '../../app/mods/_lib/board_data.js';
import { lsAllArgv, terminalTargets } from '../lib/targets.js';
import { toSnapshot } from '../lib/snapshot.js';

const LOGIN_TTL_MS = 3600000;

export const ctxKey = (c) => JSON.stringify({ on: c?.on ?? null, cwd: c?.cwd ?? null });

export function createBoard({ exec, name, version, now = Date.now, onChange = () => {} }) {
  const cache = new Map();
  const repoByKey = new Map();
  const toolsByOn = new Map();
  const loginByOn = new Map();
  const listingByOn = new Map();
  let targets = [];
  let discoveryError = null;
  let terminals = [];
  let view = null;
  let pulledAt = null;
  let inflight = null;
  // null: nothing queued; true: a listing-only pull; false: a full pull.
  let queued = null;
  let queuedFresh = false;

  const fail = (key, error) => cache.set(key, { ...(cache.get(key) ?? {}), error, stale: true });
  const commit = (key, data) => cache.set(key, { ...data, error: null, stale: false, updatedAt: now() });
  const begin = (key) => cache.set(key, { ...(cache.get(key) ?? {}), pulling: true });
  const end = (key) => {
    const cur = cache.get(key);
    if (!cur?.pulling) return;
    const next = { ...cur };
    delete next.pulling;
    cache.set(key, next);
  };
  const active = () => representatives(targets, (c) => repoByKey.get(ctxKey(c)));

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

  async function ensureTools(ctx, key) {
    const onKey = String(ctx.on);
    let have = toolsByOn.get(onKey);
    if (!have) {
      const tools = await checkTools(exec, ctx);
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

  async function loginOf(ctx, key) {
    const onKey = String(ctx.on);
    const hit = loginByOn.get(onKey);
    if (hit && now() - hit.at < LOGIN_TTL_MS) return hit.login;
    const user = await loadUser(exec, ctx);
    if (!user.ok) { fail(key, user.error); return null; }
    loginByOn.set(onKey, { login: user.login, at: now() });
    return user.login;
  }

  function listingFor(ctx, cycle) {
    const k = `listing:${ctx.on ?? ''}`;
    if (!cycle.has(k)) {
      const onKey = ctx.on ?? '';
      cycle.set(k, loadListing(exec, ctx, listingByOn.get(onKey)).then((r) => {
        if (r.ok) listingByOn.set(onKey, r);
        return r;
      }));
    }
    return cycle.get(k);
  }

  function batchDocFor(ctx, cycle) {
    const k = `batch:${ctx.on ?? ''}`;
    if (!cycle.has(k)) cycle.set(k, loadBatchDoc(exec, ctx));
    return cycle.get(k);
  }

  function terminalsFor(ctx, cycle) {
    if (!cycle.has('terminals')) cycle.set('terminals', loadTerminals(exec, ctx));
    return cycle.get('terminals');
  }

  // ceiling: Landed PRs load on every pull (the mod loads them only while its Landed chip is on);
  // load them only for a connected page that asked if the extra GraphQL calls ever matter.
  async function landed(ctx, key, repo, listing, openPrs) {
    const r = await loadLanded(exec, ctx, repo.slug, listing, repo.host, openPrs, now());
    if (!r.ok) { fail(key, r.error); return null; }
    return r.prs;
  }

  async function pullFull(ctx, key, cycle) {
    const have = await ensureTools(ctx, key);
    if (!have) return;
    const repo = await loadRepo(exec, ctx);
    if (!repo.ok) { fail(key, repo.error); return; }
    repoByKey.set(key, repoId(repo));
    const login = await loginOf(ctx, key);
    if (login == null) return;
    const [open, listing, doc, terms] = await Promise.all([
      loadOpenPrs(exec, ctx, repo.slug), listingFor(ctx, cycle), batchDocFor(ctx, cycle), terminalsFor(ctx, cycle),
    ]);
    const bad = [open, listing].find((r) => !r.ok);
    if (bad) { fail(key, bad.error); return; }
    const busy = busyPrs(doc.doc, repo.slug);
    const landedPrs = await landed(ctx, key, repo, listing.listing, open.prs);
    if (landedPrs == null) return;
    commit(key, {
      repo: { slug: repo.slug, host: repo.host, on: ctx.on ?? null, cwd: ctx.cwd }, me: login, tools: have,
      listing: listing.listing, reviewsRoot: listing.reviewsRoot, busy, openPrs: open.prs, landedPrs,
      prs: assemble({
        openPrs: open.prs, landedPrs, listing: listing.listing, slug: repo.slug, host: repo.host, login, busy,
        running: runningFor(terms.entries, repo.slug),
      }),
      partial: Boolean(open.partial),
    });
  }

  async function pullListing(ctx, key, cycle) {
    const prev = cache.get(key);
    if (!prev?.repo) { await pullFull(ctx, key, cycle); return; }
    const listing = await listingFor(ctx, cycle);
    if (!listing.ok) { fail(key, listing.error); return; }
    const doc = await batchDocFor(ctx, cycle);
    const terms = await terminalsFor(ctx, cycle);
    const busy = busyPrs(doc.doc, prev.repo.slug);
    let landedPrs = prev.landedPrs ?? [];
    if (JSON.stringify(listing.listing) !== JSON.stringify(prev.listing)) {
      landedPrs = await landed(ctx, key, prev.repo, listing.listing, prev.openPrs ?? []);
      if (landedPrs == null) return;
    }
    commit(key, {
      ...prev, listing: listing.listing, reviewsRoot: listing.reviewsRoot, busy, landedPrs,
      prs: assemble({
        openPrs: prev.openPrs ?? [], landedPrs, listing: listing.listing, slug: prev.repo.slug, host: prev.repo.host,
        login: prev.me ?? '', busy, running: runningFor(terms.entries, prev.repo.slug),
      }),
    });
  }

  async function pull(listingOnly) {
    await discover();
    const cycle = new Map();
    const publish = () => {
      const act = active();
      view = boardState(act, act.map((c) => cache.get(ctxKey(c)) ?? {}), 'all');
      onChange(snapshot());
    };
    const act = active();
    for (const ctx of act) begin(ctxKey(ctx));
    publish();
    await Promise.all(act.map(async (ctx) => {
      const key = ctxKey(ctx);
      try {
        await (listingOnly ? pullListing(ctx, key, cycle) : pullFull(ctx, key, cycle));
      } finally {
        end(key);
      }
      publish();
    }));
    for (const k of staleKeys(cache.keys(), targets.map(ctxKey))) {
      cache.delete(k);
      repoByKey.delete(k);
    }
    if (cycle.has('terminals')) {
      const t = await cycle.get('terminals');
      if (t.ok) terminals = t.entries;
    }
    const done = active();
    view = boardState(done, done.map((c) => cache.get(ctxKey(c)) ?? {}), 'all');
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
    // A manual refresh asks for every row, not for what the listing stamps say changed.
    if (fresh) listingByOn.clear();
    // Through .then so `inflight` is set before the first onChange reads it (refreshing: true).
    inflight = Promise.resolve().then(async () => {
      try {
        onChange(snapshot());
        await pull(listingOnly);
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
    reviewsRootFor: (on) => listingByOn.get(on ?? '')?.reviewsRoot ?? null,
    prTerminals: () => terminals,
    pulledAt: () => pulledAt,
  };
}

/**
 * Polls only while a page is connected (hub.count() > 0): full every fullMs, listing-only every
 * listingMs — the mod's 180 s / 15 s. wake() is called when a page connects: a never-pulled or
 * fullMs-old board pulls fully, a listingMs-old one pulls the listing.
 */
export function createPoller({ board, hub, fullMs, listingMs, now = Date.now, every = setInterval, stopEvery = clearInterval }) {
  const timers = [];
  return {
    start() {
      timers.push(every(() => { if (hub.count() > 0) board.refresh(); }, fullMs));
      timers.push(every(() => { if (hub.count() > 0) board.refresh({ listingOnly: true }); }, listingMs));
    },
    stop() { for (const t of timers.splice(0)) stopEvery(t); },
    wake() {
      const at = board.pulledAt();
      if (at == null || now() - at >= fullMs) board.refresh();
      else if (now() - at >= listingMs) board.refresh({ listingOnly: true });
    },
  };
}
