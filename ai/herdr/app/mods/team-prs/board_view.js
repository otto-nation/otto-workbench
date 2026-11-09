// team-prs board. Scope key `team-prs.scope`: "this" reads the pinned or focused checkout, "all" reads every
// open space's start folder. One fetch per repo (repoId), one listing per machine, one watch per reviews
// root; spaces outside a repo are skipped in All spaces.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import {
  exec, spaces, panes, focused, shared, setShared, watchFolder, theme, layout, run, props,
} from "herdr";
import { locate } from "./_lib/location.js";
import { boardTabId } from "./_lib/tabs.js";
import { boardState, cardId, friendlyError, repoId, representatives, spaceTargets, staleKeys } from "./_lib/scope.js";
import { busyPrs } from "./_lib/batch.js";
import {
  assemble, checkTools, loadBatchDoc, loadTerminals, runningFor, loadLanded, loadListing, loadOpenPrs, loadRepo, loadUser,
} from "./_lib/board_data.js";
import { TEAM_COLUMNS, MINE_COLUMNS, teamColumn, mineColumn, filterPrs, isMine, groupColumns } from "./_lib/columns.js";
import { viewOf, compactSections } from "./_lib/compact.js";
import { renderBoard, renderChips } from "./board.js";
import { renderCompact, renderRail } from "./compact.js";
import { SP, TEXT, RADIUS, CONTROL_H, LAYOUT } from "./_lib/tokens.js";
import { segment, segmented } from "./ui.js";

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
  return { drafts: !!f.drafts, landed: !!f.landed, authors: [...(f.authors ?? [])] };
}

function tabOf() {
  return shared("team-prs.tab") === "mine" ? "mine" : "team";
}

function errorText(err, ctx) {
  return friendlyError(err, ctx?.on, scopeOf());
}


export default class TeamPrs extends View {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    this.cache = new Map();
    this.toolsByOn = new Map();
    this.loginByOn = new Map();
    this.repoByKey = new Map();
    this.inflight = this.again = this.againListing = false;
    this.watchRoot = this.key = null;
    this.pollListing = false;
    cx.timer.every(180000, (cx) => this.refresh(cx));
    cx.timer.every(15000, (cx) => {
      if (this.activeTargets().some((c) => c.on != null) || this.pollListing) this.refresh(cx, { listingOnly: true });
    });
    this.refresh(cx);
  }

  refresh(cx, opts = {}) {
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

  terminalsFor(ctx, cycle) {
    if (!cycle.has("terminals")) cycle.set("terminals", loadTerminals(exec, ctx));
    return cycle.get("terminals");
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
    const [open, listing, doc, terms] = await Promise.all([
      loadOpenPrs(exec, ctx, repo.slug),
      this.listingFor(ctx, cycle),
      this.batchDocFor(ctx, cycle),
      this.terminalsFor(ctx, cycle),
    ]);
    const running = runningFor(terms.entries, repo.slug);
    if (!this.same(key)) return;
    const bad = [open, listing].find((r) => !r.ok);
    if (bad) { this.fail(key, bad.error); return; }
    const busy = busyPrs(doc.doc, repo.slug);
    const landedPrs = await this.landed(ctx, key, repo, listing.listing, open.prs);
    if (landedPrs == null || !this.same(key)) return;
    this.commit(key, {
      repo: { slug: repo.slug, host: repo.host, on: ctx.on ?? null, cwd: ctx.cwd }, me: login, tools: have,
      listing: listing.listing, reviewsRoot: listing.reviewsRoot, busy,
      openPrs: open.prs, landedPrs,
      prs: assemble({
        openPrs: open.prs, landedPrs, listing: listing.listing,
        slug: repo.slug, host: repo.host, login, busy, running,
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
    const terms = await this.terminalsFor(ctx, cycle);
    if (!this.same(key)) return;
    const busy = busyPrs(doc.doc, prev.repo.slug);
    const running = runningFor(terms.entries, prev.repo.slug);
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
        slug: prev.repo.slug, host: prev.repo.host, login: prev.me ?? "", busy, running,
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
    if (name === "drafts" || name === "landed") {
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
    const picked = this.selectedId
      ? (view.prs.find((p) => cardId(p) === this.selectedId) ?? this.selectedPr) : null;
    const open = picked ? this.detailView(picked, cx) : null;
    if (open) return open;
    const mode = viewOf(props(), this.size?.width, layout());
    const note = (e) => e.on_size_change((size, cx2) => this.onSize(size, cx2));
    if (mode === "rail" && view.repos === 0 && !view.loading) return this.railView(view);
    if (view.repos === 0 && view.errors.length && !view.loading) {
      return note(v_flex().id("prs").size_full().items_center().justify_center().gap(SP.sm).p(SP.xl)
        .bg(t.background).text_size(TEXT.body))
        .children(view.errors.slice(0, 3).map((e) => div().max_w(LAYOUT.msgMaxW).text_color(t.muted).child(errLine(e))))
        .child(this.scopeToggle());
    }
    if (scope === "all" && view.repos === 0 && !view.errors.length && !view.loading) {
      return note(v_flex().id("prs").size_full().items_center().justify_center().gap(SP.sm).p(SP.xl)
        .bg(t.background).text_size(TEXT.body))
        .child(div().max_w(LAYOUT.msgMaxW).text_color(t.muted).child("No open space is in a GitHub repository"))
        .child(this.scopeToggle());
    }
    if (mode === "rail") return this.railView(view);
    if (mode === "compact") return this.compactView(view, t, cx);
    return this.boardView(cx, targets[0], view, t);
  }

  listed(state) {
    const prs = state.prs ?? [], me = state.me ?? "";
    return tabOf() === "mine" ? prs.filter((p) => isMine(p, me)) : filterPrs(prs, filtersOf(), me);
  }

  // The detail replaces the board in this same pane; Back returns. The card is looked up in the
  // live state on every render, so its runs and busy state follow refreshes; the clicked copy only
  // stands in if the PR drops out of the list.
  openDetail(pr, cx) {
    this.selectedId = cardId(pr);
    this.selectedPr = pr;
    this.acx.notify();
  }

  closeDetail(cx) {
    this.selectedId = this.selectedPr = null;
    this.acx.notify();
  }

  // The selector (main.js) supplies the view; the plain board has none.
  detailView() { return null; }

  onSize(size, cx) { this.size = size; cx.notify(); }

  compactHeader(tab, t) {
    return v_flex().flex_none().px(SP.md).py(SP.sm).gap(SP.sm)
      .child(h_flex().child(this.scopeToggle()))
      .child(h_flex().items_center().justify_between()
        .child(segmented([
          segment("tab-team", "Team", tab === "team", () => setShared("team-prs.tab", "team")),
          segment("tab-mine", "Mine", tab === "mine", () => setShared("team-prs.tab", "mine")),
        ]))
        .child(div().id("open-board").text_size(TEXT.caption).text_color(t.muted).cursor_pointer()
          .on_click(() => {
            const id = boardTabId(spaces(), "PRs");
            if (id) run("tab.select", { id });
            else run("tab.new", { name: "PRs", mod: "team-prs" });
          })
          .child("Open board")));
  }

  compactView(state, t, cx) {
    const mine = tabOf() === "mine";
    const spec = mine ? MINE_COLUMNS : TEAM_COLUMNS;
    const sections = compactSections(groupColumns(this.listed(state), mine ? mineColumn : teamColumn, spec));
    return v_flex().id("prs").size_full().min_h(0).bg(t.background).text_color(t.foreground)
      .text_size(TEXT.small)
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(this.compactHeader(tabOf(), t))
      .child(renderCompact({
        sections, onOpen: (pr) => this.openDetail(pr, cx), showRepo: scopeOf() === "all",
        width: this.size?.width,
      }));
  }

  railView(state) {
    return v_flex().id("prs").size_full()
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(renderRail({ count: this.listed(state).length }));
  }

  scopeToggle() {
    const scope = scopeOf();
    return segmented([
      segment("scope-this", "This space", scope === "this", () => setShared("team-prs.scope", "this")),
      segment("scope-all", "All spaces", scope === "all", () => setShared("team-prs.scope", "all")),
    ]);
  }

  boardView(cx, ctx, state, t) {
    const filters = filtersOf(), tab = tabOf(), pin = shared("team-prs.pin");
    // Team view is about teammates: the viewer has the Mine tab.
    const me = String(state.me ?? "").toLowerCase();
    const authors = [...new Set((state.prs ?? []).map((p) => String(p.author ?? "").toLowerCase()).filter((a) => a && a !== me))].sort();
    return v_flex().id("prs").size_full().min_h(0).bg(t.background).text_color(t.foreground).text_size(TEXT.body)
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(this.header(ctx, state, tab, pin, t))
      .child(renderChips(filters, authors, { onToggle: (name) => this.toggleChip(name, cx) }))
      .child(renderBoard({
        tab, prs: state.prs ?? [], filters, me: state.me ?? "",
        onOpen: (pr) => this.openDetail(pr, cx),
        spinner: "⠋", showRepo: scopeOf() === "all",
      }));
  }

  header(ctx, state, tab, pin, t) {
    const loading = state.loading && !state.repos;
    const nFail = state.errors?.length ?? 0;
    const first = state.errors?.[0];
    const all = scopeOf() === "all";
    const title = all ? "All spaces" : (loading ? "loading…" : (state.slug ?? "no repo"));
    const note = all ? (loading ? "loading…" : `${state.repos} repos`) : null;
    const pinned = (e) => e.child(div().id("pin").px(SP.sm).min_h(CONTROL_H.compact).flex().items_center()
      .rounded(RADIUS.control).text_size(TEXT.caption).cursor_pointer().border_1().border_color(t.border)
      .hover((s) => s.border_color(t.dim))
      .on_click(() => setShared("team-prs.pin", pin ? null : { on: ctx.on, cwd: ctx.cwd }))
      .child(pin ? "pinned" : "pin"));
    // Title on the left names the subject (the repo); the controls that filter it sit on the right.
    return h_flex().id("prs-header").min_h(LAYOUT.headerH).flex_wrap().py(SP.sm).flex_none().px(SP.lg).gap(SP.md).items_center()
      .child(h_flex().flex_1().min_w(0).gap(SP.sm).items_center()
        .child(div().min_w(0).truncate().text_size(TEXT.title).font_semibold().child(title))
        .when(Boolean(note), (e) => e.child(div().flex_none().text_size(TEXT.caption).text_color(t.dim).child(note)))
        .when(!all && ctx?.on != null, (e) => e.child(div().flex_none().px(SP.sm).py(1).rounded(RADIUS.control)
          .border_1().border_color(t.border).text_size(TEXT.caption).text_color(t.muted).child(String(ctx.on))))
        .when(nFail && !all, (e) => e
          .child(div().flex_none().text_size(TEXT.caption).text_color(t.ansi1).child("stale"))
          .child(div().min_w(0).text_size(TEXT.caption).text_color(t.dim).truncate().child(errorText(first.error, ctx))))
        .when(nFail && all, (e) => e.child(div().min_w(0).text_size(TEXT.caption).text_color(t.ansi1).truncate()
          .child(`${nFail} failed — ${friendlyError(first.error, first.on, "all")}`))))
      .child(this.scopeToggle())
      .child(segmented([
        segment("tab-team", "Team", tab === "team", () => setShared("team-prs.tab", "team")),
        segment("tab-mine", "Mine", tab === "mine", () => setShared("team-prs.tab", "mine")),
      ]))
      .when(!all, pinned);
  }
}
