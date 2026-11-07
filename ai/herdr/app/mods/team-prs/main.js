import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import {
  exec, spaces, panes, focused, shared, setShared, watchFolder, appHome, theme,
} from "herdr";
import { locate, isUntrustedHome } from "./_lib/location.js";
import { boardState, friendlyError, repoId, representatives, spaceTargets, staleKeys } from "./_lib/scope.js";
import { busyPrs } from "./_lib/batch.js";
import {
  assemble, checkTools, loadBatchDoc, loadLanded, loadListing, loadOpenPrs, loadRepo, loadUser,
} from "./_lib/board_data.js";
import { renderBoard, renderChips } from "./board.js";

function ctxOf() {
  return shared("team-prs.pin") ?? locate(spaces(), panes(), focused());
}

function scopeOf() {
  return shared("team-prs.scope") === "all" ? "all" : "this";
}

// The checkouts the board reads: the pinned or focused one, or every open space's.
function targetsOf() {
  if (scopeOf() === "all") {
    const all = spaceTargets(spaces());
    if (all.length) return all;
  }
  const c = ctxOf();
  return [{ on: c.on, cwd: c.cwd }];
}

function ctxKey(ctx) {
  return JSON.stringify({ on: ctx?.on ?? null, cwd: ctx?.cwd ?? null });
}

function filtersOf() {
  const f = shared("team-prs.filters") ?? {};
  return { mine: !!f.mine, drafts: !!f.drafts, landed: !!f.landed, authors: [...(f.authors ?? [])] };
}

function tabOf() {
  return shared("team-prs.tab") === "mine" ? "mine" : "team";
}

function errorText(err, ctx) {
  return friendlyError(err, ctx?.on, scopeOf());
}


export default class TeamPrs extends View {
  init(_props, cx) {
    this.cache = new Map();
    this.toolsByOn = new Map();
    this.loginByOn = new Map();
    this.repoByKey = new Map();
    this.inflight = this.again = this.againListing = false;
    this.watchRoot = this.key = null;
    this.pollListing = false;
    if (isUntrustedHome(appHome())) return;
    cx.timer.every(180000, (cx) => this.refresh(cx));
    cx.timer.every(15000, (cx) => {
      if (this.activeTargets().some((c) => c.on != null) || this.pollListing) this.refresh(cx, { listingOnly: true });
    });
    this.refresh(cx);
  }

  refresh(cx, opts = {}) {
    if (isUntrustedHome(appHome())) return;
    const listingOnly = Boolean(opts.listingOnly);
    if (this.inflight) {
      if (!this.again) this.againListing = listingOnly;
      else if (!listingOnly) this.againListing = false;
      this.again = true;
      return;
    }
    this.inflight = true;
    cx.spawn(async (cx) => {
      try { await this.pull(cx, listingOnly); }
      finally {
        this.inflight = false;
        cx.notify();
        if (this.again) {
          const next = this.againListing;
          this.again = this.againListing = false;
          this.refresh(cx, { listingOnly: next });
        }
      }
    });
  }

  activeTargets() {
    const all = targetsOf();
    if (scopeOf() !== "all") return all;
    return representatives(all, (c) => this.repoByKey.get(ctxKey(c)));
  }

  same(key) { return this.activeTargets().some((c) => ctxKey(c) === key); }

  fail(key, error) {
    this.cache.set(key, { ...(this.cache.get(key) ?? {}), error, stale: true });
  }

  commit(key, data, ctx, cx) {
    this.cache.set(key, { ...data, error: null, stale: false, updatedAt: Date.now() });
    // The watch follows the machine's reviews root, not a checkout.
    if (ctx.on == null) this.startWatch(cx, data.reviewsRoot);
  }

  async ensureTools(ctx, key) {
    const onKey = String(ctx.on);
    let have = this.toolsByOn.get(onKey);
    if (!have) {
      const tools = await checkTools(exec, ctx);
      if (!this.same(key)) return null;
      if (!tools.ok) { this.fail(key, tools.error); return null; }
      this.toolsByOn.set(onKey, have = tools.have);
    }
    const missing = ["pr", "gh"].filter((n) => !have[n]);
    if (missing.length) {
      this.fail(key, { kind: "missing-tool", tools: missing, message: missing.join(", ") });
      return null;
    }
    return have;
  }

  async loginOf(ctx, key) {
    const onKey = String(ctx.on);
    const hit = this.loginByOn.get(onKey);
    if (hit && Date.now() - hit.at < 3600000) return hit.login;
    const user = await loadUser(exec, ctx);
    if (!this.same(key)) return null;
    if (!user.ok) { this.fail(key, user.error); return null; }
    this.loginByOn.set(onKey, { login: user.login, at: Date.now() });
    return user.login;
  }

  listingFor(ctx, cycle) {
    const k = `listing:${ctx.on ?? ""}`;
    if (!cycle.has(k)) cycle.set(k, loadListing(exec, ctx));
    return cycle.get(k);
  }

  batchDocFor(ctx, cycle) {
    const k = `batch:${ctx.on ?? ""}`;
    if (!cycle.has(k)) cycle.set(k, loadBatchDoc(exec, ctx));
    return cycle.get(k);
  }

  async pull(cx, listingOnly) {
    const cycle = new Map();
    await Promise.all(this.activeTargets().map(async (ctx) => {
      const key = ctxKey(ctx);
      if (!ctx.cwd) {
        this.cache.set(key, { error: { kind: "failed", message: "Not in a repository" }, stale: false });
        return;
      }
      if (listingOnly) await this.pullListing(ctx, key, cx, cycle);
      else await this.pullFull(ctx, key, cx, cycle);
      cx.notify();
    }));
    const keep = [ctxKey(ctxOf()), ...spaceTargets(spaces()).map(ctxKey)];
    for (const k of staleKeys(this.cache.keys(), keep)) {
      this.cache.delete(k);
      this.repoByKey.delete(k);
    }
  }

  async pullFull(ctx, key, cx, cycle) {
    const have = await this.ensureTools(ctx, key);
    if (!have || !this.same(key)) return;
    const repo = await loadRepo(exec, ctx);
    if (!this.same(key)) return;
    if (!repo.ok) { this.fail(key, repo.error); return; }
    this.repoByKey.set(key, repoId(repo));
    const login = await this.loginOf(ctx, key);
    if (login == null || !this.same(key)) return;
    const [open, listing, doc] = await Promise.all([
      loadOpenPrs(exec, ctx, repo.slug),
      this.listingFor(ctx, cycle),
      this.batchDocFor(ctx, cycle),
    ]);
    if (!this.same(key)) return;
    const bad = [open, listing].find((r) => !r.ok);
    if (bad) { this.fail(key, bad.error); return; }
    const busy = busyPrs(doc.doc, repo.slug);
    const landedPrs = await this.landed(ctx, key, repo, listing.listing, open.prs);
    if (landedPrs == null || !this.same(key)) return;
    this.commit(key, {
      repo: { slug: repo.slug, host: repo.host }, me: login, tools: have,
      listing: listing.listing, reviewsRoot: listing.reviewsRoot, busy,
      openPrs: open.prs, landedPrs,
      prs: assemble({
        openPrs: open.prs, landedPrs, listing: listing.listing,
        slug: repo.slug, host: repo.host, login, busy,
      }),
    }, ctx, cx);
  }

  async landed(ctx, key, repo, listing, openPrs) {
    if (!filtersOf().landed) return [];
    const result = await loadLanded(exec, ctx, repo.slug, listing, repo.host, openPrs, Date.now());
    if (!this.same(key)) return null;
    if (!result.ok) { this.fail(key, result.error); return null; }
    return result.prs;
  }

  async pullListing(ctx, key, cx, cycle) {
    const prev = this.cache.get(key);
    if (!prev?.repo) { await this.pullFull(ctx, key, cx, cycle); return; }
    const listing = await this.listingFor(ctx, cycle);
    if (!this.same(key)) return;
    if (!listing.ok) { this.fail(key, listing.error); return; }
    const doc = await this.batchDocFor(ctx, cycle);
    if (!this.same(key)) return;
    const busy = busyPrs(doc.doc, prev.repo.slug);
    let landedPrs;
    if (JSON.stringify(listing.listing) === JSON.stringify(prev.listing)) {
      landedPrs = prev.landedPrs ?? [];
    } else {
      landedPrs = await this.landed(ctx, key, prev.repo, listing.listing, prev.openPrs ?? []);
      if (landedPrs == null || !this.same(key)) return;
    }
    this.commit(key, {
      ...prev, listing: listing.listing, reviewsRoot: listing.reviewsRoot, busy, landedPrs,
      prs: assemble({
        openPrs: prev.openPrs ?? [], landedPrs, listing: listing.listing,
        slug: prev.repo.slug, host: prev.repo.host, login: prev.me ?? "", busy,
      }),
    }, ctx, cx);
  }

  startWatch(cx, root) {
    if (!root || this.watchRoot === root) return;
    this.watchRoot = root;
    this.pollListing = false;
    cx.spawn(async (cx) => {
      try {
        while (this.watchRoot === root && this.activeTargets().some((c) => c.on == null)) {
          await watchFolder(root);
          if (this.watchRoot === root && this.activeTargets().some((c) => c.on == null)) {
            this.refresh(cx, { listingOnly: true });
          }
        }
      } catch { if (this.watchRoot === root) this.pollListing = true; }
    });
  }

  toggleChip(name, cx) {
    const f = filtersOf();
    if (name === "mine" || name === "drafts" || name === "landed") {
      f[name] = !f[name];
      setShared("team-prs.filters", f);
      if (name === "landed" && f.landed) this.refresh(cx);
      return;
    }
    if (!name.startsWith("author:")) return;
    const login = name.slice(7).toLowerCase();
    const set = new Set(f.authors.map((a) => String(a).toLowerCase()));
    if (set.has(login)) set.delete(login); else set.add(login);
    f.authors = [...set];
    setShared("team-prs.filters", f);
  }

  render(cx) {
    const t = theme();
    // Running gh/pr from the window that also holds agent machines is the exposure the untrusted-window split exists to prevent.
    if (isUntrustedHome(appHome())) {
      return v_flex().id("prs").size_full().items_center().justify_center().bg(t.background)
        .child(div().text_color(t.dim).child("PR board is disabled in the untrusted window"));
    }
    const targets = this.activeTargets();
    const scopeKey = scopeOf() + JSON.stringify(targets.map(ctxKey));
    if (this.key !== scopeKey) {
      this.key = scopeKey; this.refresh(cx, { listingOnly: true });
    }
    const states = targets.map((c) => this.cache.get(ctxKey(c)) ?? {});
    const scope = scopeOf();
    const view = boardState(targets, states, scope);
    const errLine = (e) => {
      const base = String(e.cwd).split("/").filter(Boolean).pop();
      return `${base ?? ""}${e.on != null ? " on " + e.on : ""}: ${friendlyError(e.error, e.on, scope)}`;
    };
    if (view.repos === 0 && view.errors.length && !view.loading) {
      return v_flex().id("prs").size_full().items_center().justify_center().gap(8).bg(t.background)
        .children(view.errors.slice(0, 3).map((e) => div().text_color(t.dim).child(errLine(e))))
        .child(this.scopeToggle(t));
    }
    if (scope === "all" && view.repos === 0 && !view.errors.length && !view.loading) {
      return v_flex().id("prs").size_full().items_center().justify_center().gap(8).bg(t.background)
        .child(div().text_color(t.dim).child("No open space is in a GitHub repository"))
        .child(this.scopeToggle(t));
    }
    return this.boardView(cx, targets[0], view, t);
  }

  scopeToggle(t) {
    const scope = scopeOf();
    const btn = (name, label) => div().id(`scope-${name}`).px(8).py(4).rounded(6).text_size(11)
      .cursor_pointer().when(scope === name, (e) => e.bg(t.selected).text_color(t.foreground))
      .when(scope !== name, (e) => e.text_color(t.muted))
      .on_click(() => setShared("team-prs.scope", name)).child(label);
    return h_flex().gap(4).child(btn("this", "This space")).child(btn("all", "All spaces"));
  }

  boardView(cx, ctx, state, t) {
    const filters = filtersOf(), tab = tabOf(), pin = shared("team-prs.pin");
    const authors = [...new Set((state.prs ?? []).map((p) => String(p.author ?? "").toLowerCase()).filter(Boolean))].sort();
    return v_flex().id("prs").size_full().min_h(0).bg(t.background).text_color(t.foreground).text_size(13)
      .child(this.header(ctx, state, tab, pin, t))
      .child(renderChips(filters, authors, { onToggle: (name) => this.toggleChip(name, cx) }))
      .child(renderBoard({
        tab, prs: state.prs ?? [], filters, me: state.me ?? "",
        // Detail window lands in PR 4.
        onOpen: (_pr) => {},
        spinner: "⠋", showRepo: scopeOf() === "all",
      }));
  }

  header(ctx, state, tab, pin, t) {
    const btn = (name, label) => div().id(`tab-${name}`).px(8).py(4).rounded(6).text_size(11)
      .cursor_pointer().when(tab === name, (e) => e.bg(t.selected).text_color(t.foreground))
      .when(tab !== name, (e) => e.text_color(t.muted))
      .on_click(() => setShared("team-prs.tab", name)).child(label);
    const loading = state.loading && !state.repos;
    const nFail = state.errors?.length ?? 0;
    const first = state.errors?.[0];
    return h_flex().id("prs-header").h(42).flex_none().px(14).gap(8).items_center()
      .child(this.scopeToggle(t))
      .when(scopeOf() === "all", (e) => e.child(div().text_size(11).text_color(t.dim)
        .child(loading ? "loading…" : `${state.repos} repos`)))
      .when(scopeOf() !== "all", (e) => e.child(div().font_semibold().child(loading ? "loading…" : (state.slug ?? "no repo"))))
      .when(scopeOf() !== "all" && ctx?.on != null, (e) => e.child(div().text_size(11).text_color(t.dim).child(String(ctx.on))))
      .child(h_flex().gap(4).child(btn("team", "Team")).child(btn("mine", "Mine")))
      .when(scopeOf() !== "all", (e) => e.child(div().id("pin").px(8).py(4).rounded(6).text_size(11).cursor_pointer()
        .border_1().border_color(t.border)
        .on_click(() => setShared("team-prs.pin", pin ? null : { on: ctx.on, cwd: ctx.cwd }))
        .child(pin ? "pinned" : "pin")))
      .when(nFail && scopeOf() !== "all", (e) => e
        .child(div().text_size(11).text_color(t.ansi1).child("stale"))
        .child(div().text_size(11).text_color(t.dim).truncate().child(errorText(first.error, ctx))))
      .when(nFail && scopeOf() === "all", (e) => e
        .child(div().text_size(11).text_color(t.ansi1).truncate()
          .child(`${nFail} failed — ${friendlyError(first.error, first.on, "all")}`)));
  }
}
