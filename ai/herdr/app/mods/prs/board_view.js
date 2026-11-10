// PRs board. Scope key `prs.scope`: "this" reads the focused checkout, "all" reads every
// open space's start folder. One fetch per repo (repoId), one listing per machine, one watch per reviews
// root; spaces outside a repo are skipped in All spaces.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import {
  exec, spaces, panes, focused, shared, setShared, watchFolder, theme, run, props, layout,
} from "herdr";
import { locate } from "./_lib/location.js";
import { boardTabId } from "./_lib/tabs.js";
import { enterPlan, exitSteps, fullscreenState, isPanelCopy } from "./_lib/fullscreen.js";
import { boardState, cardId, friendlyError, repoId, representatives, spaceTargets, staleKeys } from "./_lib/scope.js";
import { busyPrs } from "./_lib/batch.js";
import {
  assemble, checkTools, loadBatchDoc, loadTerminals, runningFor, loadLanded, loadListing, loadOpenPrs, loadOwed, loadRepo, loadUser,
} from "./_lib/board_data.js";
import {
  TEAM_COLUMNS, MINE_COLUMNS, teamColumn, mineColumn, filterPrs, filterRepos, filterLabels, filterFacets, filterOn,
  isMine, groupColumns,
} from "./_lib/columns.js";
import { viewOf, compactSections } from "./_lib/compact.js";
import { renderBoard, renderChips } from "./board.js";
import { renderCompact } from "./compact.js";
import { Workflows } from "./workflows.js";
import { BatchLauncher } from "./batch_launcher.js";
import { renderBatchPage } from "./batch_page.js";
import { prKey } from "./_lib/launch.js";
import { SP, LAYOUT } from "./_lib/tokens.js";
import { segment, segmented, emptyPane, pane, button, iconButton, tag, text, type, statusText, placeholder, errorLine } from "./_ui/ui.js";
import { relativeTime } from "./_lib/history.js";

function ctxOf() {
  return locate(spaces(), panes(), focused());
}

function scopeOf() {
  return shared("prs.scope") === "all" ? "all" : "this";
}

// The checkouts the board reads: the focused one, or every open space's.
function targetsOf() {
  if (scopeOf() === "all") {
    const all = spaceTargets(spaces(), panes());
    if (all.length) return all;
  }
  const c = ctxOf();
  return [{ on: c.on, cwd: c.cwd }];
}

function ctxKey(ctx) {
  return JSON.stringify({ on: ctx?.on ?? null, cwd: ctx?.cwd ?? null });
}

function filtersOf() {
  const f = shared("prs.filters") ?? {};
  return {
    drafts: f.drafts,
    mineDrafts: f.mineDrafts,
    landed: f.landed,
    empty: f.empty,
    authors: [...(f.authors ?? [])],
    repos: [...(f.repos ?? [])],
    labels: [...(f.labels ?? [])],
  };
}

function tabOf() {
  return shared("prs.tab") === "mine" ? "mine" : "team";
}

function errorText(err, ctx) {
  return friendlyError(err, ctx?.on, scopeOf());
}

// Unfiltered: the Batch form applies the shared chips itself (BatchLauncher.shown), so a selected
// PR a chip hides still counts as hidden rather than vanishing.
function mineOpen(state) {
  return (state.prs ?? [])
    .filter((p) => isMine(p, state.me) && p.state !== "MERGED" && p.state !== "CLOSED");
}

// The PRs a tab lists before the label chips: the label chips offered are those among them.
function tabList(state, filters, tab) {
  const base = filterRepos(state.prs ?? [], filters.repos, state.repoSlugs);
  const me = state.me ?? "";
  if (tab === "mine") return base.filter((p) => isMine(p, me) && (filterOn(filters, "mineDrafts") || !p.isDraft));
  return filterPrs(base, filters, me);
}


export default class PrsBoard extends View {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    // Where the copy is mounted, not how wide it is, decides whether it offers the board tab.
    this.inPanel = isPanelCopy(props());
    this.fsState = null;
    if (this.inPanel) {
      cx.timer.every(1000, (cx) => {
        const s = fullscreenState(layout());
        if (s !== this.fsState) {
          this.fsState = s;
          cx.notify();
        }
      });
    }
    this.workflows = new Workflows(cx, () => this.activeTargets());
    // The Batch form's Drafts chip is the Mine tab's: one setting, so the list and the form agree.
    // The Batch form's chips are the board's: one prs.filters, so a view and the form agree.
    this.launcher = new BatchLauncher(cx, this.workflows, {
      get: () => filtersOf(),
      toggle: (name) => this.toggleChip(name, this.acx),
    });
    this.batchOpen = false;
    this.cache = new Map();
    this.toolsByOn = new Map();
    this.loginByOn = new Map();
    // Each machine's last good listing, which an unchanged answer stands in for.
    this.listingByOn = new Map();
    this.repoByKey = new Map();
    this.inflight = this.again = this.againListing = false;
    this.watchRoot = this.key = null;
    this.pollListing = false;
    this.manual = false;
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
        // Clear only on a full pull with no other full pull queued behind it.
        if (!listingOnly && !(this.again && !this.againListing)) this.manual = false;
        cx.notify();
        if (this.again) {
          const next = this.againListing;
          this.again = this.againListing = false;
          this.refresh(cx, { listingOnly: next });
        }
      }
    });
  }

  // The header's Refresh: a full pull now. `manual` labels it until the pull it asked for lands.
  refreshNow() {
    this.manual = true;
    // A manual refresh asks for every row, not for what the stamps say changed.
    this.listingByOn.clear();
    this.refresh(this.acx);
    this.acx.notify();
  }

  refreshButton() {
    return iconButton("refresh", "mods/prs/refresh.svg", this.manual ? "Refreshing…" : "Refresh", () => this.refreshNow(), { disabled: this.manual, spinning: this.manual });
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
    if (!cycle.has(k)) {
      const onKey = ctx.on ?? "";
      cycle.set(k, loadListing(exec, ctx, this.listingByOn.get(onKey)).then((r) => {
        if (r.ok) this.listingByOn.set(onKey, r);
        return r;
      }));
    }
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
    const keep = [ctxKey(ctxOf()), ...spaceTargets(spaces(), panes()).map(ctxKey)];
    for (const k of staleKeys(this.cache.keys(), keep)) {
      this.cache.delete(k);
      this.repoByKey.delete(k);
    }
  }

  // Has Comments is read only while Mine shows; opening Mine reads it now rather than at the
  // next full refresh.
  showMine() {
    setShared("prs.tab", "mine");
    this.refresh(this.acx);
  }

  // What `pr` says each PR is owed, read only for Mine. A failed read keeps the last good answer
  // rather than emptying the column.
  async owedFor(ctx, slug, prev) {
    if (tabOf() !== "mine") return prev ?? null;
    const r = await loadOwed(exec, ctx, slug);
    return r.ok ? r.owed : (prev ?? null);
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
    const [open, listing, doc, terms, owed] = await Promise.all([
      loadOpenPrs(exec, ctx, repo.slug),
      this.listingFor(ctx, cycle),
      this.batchDocFor(ctx, cycle),
      this.terminalsFor(ctx, cycle),
      this.owedFor(ctx, repo.slug, this.cache.get(key)?.owed),
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
      openPrs: open.prs, landedPrs, owed,
      prs: assemble({
        openPrs: open.prs, landedPrs, listing: listing.listing,
        slug: repo.slug, host: repo.host, login, busy, running, owed,
      }),
    }, ctx, cx);
  }

  async landed(ctx, key, repo, listing, openPrs) {
    if (!filterOn(filtersOf(), "landed")) return [];
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
        owed: prev.owed,
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
    if (name === "drafts" || name === "mineDrafts" || name === "landed" || name === "empty") {
      f[name] = !filterOn(f, name);
      setShared("prs.filters", f);
      if (name === "landed" && f.landed) this.refresh(cx);
      return;
    }
    for (const [prefix, field] of [["repo:", "repos"], ["label:", "labels"]]) {
      if (!name.startsWith(prefix)) continue;
      const value = name.slice(prefix.length).toLowerCase();
      const set = new Set(f[field].map((r) => String(r).toLowerCase()));
      if (set.has(value)) set.delete(value); else set.add(value);
      f[field] = [...set];
      setShared("prs.filters", f);
      return;
    }
    if (!name.startsWith("author:")) return;
    const login = name.slice(7).toLowerCase();
    const set = new Set(f.authors.map((a) => String(a).toLowerCase()));
    if (set.has(login)) set.delete(login); else set.add(login);
    f.authors = [...set];
    setShared("prs.filters", f);
  }

  render(cx) {
    this.workflows.show();
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
    const mode = viewOf(this.size?.width);
    const note = (e) => e.on_size_change((size, cx2) => this.onSize(size, cx2));
    if (this.batchOpen && tabOf() === "mine") {
      this.launcher.loaded = view.repoSlugs;
      this.launcher.knownLabels = filterFacets(tabList(view, filtersOf(), "mine")).labels;
      return note(renderBatchPage({
        launcher: this.launcher,
        workflows: this.workflows,
        prs: mineOpen(view),
        allPrs: view.prs,
        onOpenPr: (pr) => this.openDetail(pr, cx, "batch"),
        onBack: () => { this.batchOpen = false; this.acx.notify(); },
        run,
      }));
    }
    if (view.repos === 0 && view.errors.length && !view.loading) {
      return note(emptyPane())
        .children(view.errors.slice(0, 3).map((e) => errorLine(errLine(e)).max_w(LAYOUT.msgMaxW)))
        .child(button("refresh-retry", "Retry", () => this.refreshNow()))
        .child(this.scopeToggle());
    }
    if (scope === "all" && view.repos === 0 && !view.errors.length && !view.loading) {
      return note(emptyPane())
        .child(placeholder("No open space is in a GitHub repository").max_w(LAYOUT.msgMaxW))
        .child(this.scopeToggle());
    }
    if (mode === "compact") return this.compactView(view, t, cx);
    return this.boardView(cx, targets[0], view, t);
  }

  listed(state) {
    const filters = filtersOf();
    return filterLabels(tabList(state, filters, tabOf()), filters.labels);
  }

  // The detail replaces the board in this same pane; Back returns. The card is looked up in the
  // live state on every render, so its runs and busy state follow refreshes; the clicked copy only
  // stands in if the PR drops out of the list.
  // `tab` names the detail tab to open on (main.js reads pendingTab); omitted, the detail chooses.
  openDetail(pr, cx, tab = null) {
    this.pendingTab = tab;
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

  // A Mine column's shortcut: the Batch page with that column's visible PRs and its step set up.
  batchFor(items, preset) {
    this.launcher.applyPreset(items.map((p) => prKey(p.repoSlug, p.number)), preset);
    this.batchOpen = true;
    this.acx.notify();
  }

  // The label names the action only; that runs are going shows beside it, not inside it.
  batchControl() {
    const running = this.workflows.activeRuns().length > 0;
    return h_flex().gap(SP.sm).items_center()
      .child(button("batch", "Batch", () => { this.batchOpen = true; this.acx.notify(); }))
      .when(running, (e) => e.child(statusText("running", "Running")));
  }

  onSize(size, cx) { this.size = size; cx.notify(); }

  compactHeader(tab) {
    return v_flex().flex_none().px(SP.md).py(SP.sm).gap(SP.sm)
      .child(h_flex().items_center()
        .child(this.scopeToggle())
        .child(div().flex_1())
        .child(this.refreshButton()))
      .child(h_flex().items_center().justify_between()
        .child(segmented([
          segment("tab-team", "Team", tab === "team", () => setShared("prs.tab", "team")),
          segment("tab-mine", "Mine", tab === "mine", () => this.showMine()),
        ]))
        .child(h_flex().gap(SP.md).items_center()
          .when(tab === "mine", (e) => e.child(this.batchControl()))
          .when(this.inPanel, (e) => e.child(this.fullscreenButton()).child(this.openBoardButton()))));
  }

  fullscreenButton() {
    const state = fullscreenState(layout());
    return button("fullscreen", state === "on" ? "Exit full screen" : "Full screen", () => {
      if (state === "on" || state === "stranded") {
        for (const step of exitSteps(shared("prs.panelPx"))) run(step.command, step.args);
        setShared("prs.panelPx", null);
        return;
      }
      const plan = enterPlan(layout());
      if (plan.savedPx !== null) setShared("prs.panelPx", plan.savedPx);
      for (const step of plan.steps) run(step.command, step.args);
    });
  }

  openBoardButton() {
    return button("open-board", "Open board", () => {
      const id = boardTabId(spaces(), "PRs");
      if (id) run("tab.select", { id });
      else run("tab.new", { name: "PRs", mod: "prs" });
    });
  }

  compactView(state, t, cx) {
    const tab = tabOf(), mine = tab === "mine";
    const spec = mine ? MINE_COLUMNS : TEAM_COLUMNS;
    const sections = compactSections(groupColumns(this.listed(state), mine ? mineColumn : teamColumn, spec));
    return pane("prs")
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(this.compactHeader(tab))
      .child(renderCompact({
        sections, onOpen: (pr) => this.openDetail(pr, cx), showRepo: scopeOf() === "all",
        width: this.size?.width, onBatch: mine ? (items, preset) => this.batchFor(items, preset) : null,
      }));
  }

  scopeToggle() {
    const scope = scopeOf();
    return segmented([
      segment("scope-this", "This space", scope === "this", () => setShared("prs.scope", "this")),
      segment("scope-all", "All spaces", scope === "all", () => setShared("prs.scope", "all")),
    ]);
  }

  boardView(cx, ctx, state, t) {
    const filters = filtersOf(), tab = tabOf();
    // Team view is about teammates: the viewer has the Mine tab.
    const me = String(state.me ?? "").toLowerCase();
    const authors = [...new Set((state.prs ?? []).map((p) => String(p.author ?? "").toLowerCase()).filter((a) => a && a !== me))].sort();
    const repos = scopeOf() === "all" ? [...(state.repoSlugs ?? [])].sort() : [];
    const labels = filterFacets(tabList(state, filters, tab)).labels;
    return pane("prs")
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(this.header(ctx, state, tab, t))
      .child(renderChips(filters, authors, { onToggle: (name) => this.toggleChip(name, cx), tab, repos, labels }))
      .child(renderBoard({
        tab, prs: this.listed(state), filters, me: state.me ?? "",
        onOpen: (pr) => this.openDetail(pr, cx),
        onBatch: (items, preset) => this.batchFor(items, preset),
        spinner: "⠋", showRepo: scopeOf() === "all", loading: Boolean(state.loading && !state.repos),
        workflowOf: (pr) => this.workflows.itemFor(pr.repoSlug, pr.number),
      }));
  }

  header(ctx, state, tab, t) {
    const loading = state.loading && !state.repos;
    const nFail = state.errors?.length ?? 0;
    const first = state.errors?.[0];
    const all = scopeOf() === "all";
    const title = all ? "All spaces" : (loading ? "Loading…" : (state.slug ?? "No repo"));
    const note = all ? (loading ? "Loading…" : `${state.repos} repos`) : null;
    // Title on the left names the subject (the repo); the controls that filter it sit on the right.
    return h_flex().id("prs-header").min_h(LAYOUT.headerH).flex_wrap().py(SP.sm).flex_none().px(SP.lg).gap(SP.md).items_center()
      .child(h_flex().flex_1().min_w(0).gap(SP.sm).items_center()
        .child(type(div().min_w(0).truncate(), "title").child(title))
        .when(Boolean(note), (e) => e.child(text("meta", note, t.muted).flex_none()))
        .when(!all && ctx?.on != null, (e) => e.child(tag(String(ctx.on))))
        .when(nFail && !all, (e) => e
          // DESIGN.md: stale is warn plus how old the shown data is; with nothing loaded yet it is
          // an error, not stale.
          .child(first.updatedAt != null
            ? statusText("warn", `stale · ${relativeTime(new Date(first.updatedAt).toISOString(), Date.now())}`)
            : statusText("fail", "failed"))
          .child(text("meta", errorText(first.error, ctx), t.muted).min_w(0).truncate()))
        .when(nFail && all, (e) => e
          .child(statusText("fail", `${nFail} failed`))
          .child(text("meta", friendlyError(first.error, first.on, "all"), t.muted).min_w(0).truncate())))
      .when(tab === "mine", (e) => e.child(this.batchControl()))
      .child(this.scopeToggle())
      .child(segmented([
        segment("tab-team", "Team", tab === "team", () => setShared("prs.tab", "team")),
        segment("tab-mine", "Mine", tab === "mine", () => this.showMine()),
      ]))
      .when(this.inPanel, (e) => e.child(this.fullscreenButton()).child(this.openBoardButton()))
      .child(this.refreshButton());
  }
}
