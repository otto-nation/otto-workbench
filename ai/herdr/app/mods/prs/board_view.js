// PRs board. Scope key `prs.scope`: "this" reads the focused checkout, "all" reads every
// open space's start folder. One fetch per repo (repoId), one listing per machine, one watch per reviews
// root; spaces outside a repo are skipped in All spaces.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import {
  exec, spaces, panes, focused, shared, setShared, watchFolder, theme, run, props, layout,
} from "herdr";
import { locate } from "./_lib/location.js";
import { fullscreenState, isPanelCopy } from "./_lib/fullscreen.js";
import { enterFullscreen, exitFullscreen } from "./_lib/fullscreen_run.js";
import { boardState, cardId, friendlyError, repoId, representatives, spaceTargets, staleKeys } from "./_lib/scope.js";
import { LOAD_DELAY_MS, loadState, errorsStatus } from "./_lib/load_state.js";
import { runStatus } from "./_lib/batch.js";
import { checkTools, loadTerminals } from "./_lib/board_data.js";
import { pullTarget } from "./_lib/board_pull.js";
import {
  TEAM_COLUMNS, MINE_COLUMNS, columnsFor, teamColumn, mineColumn, filterPrs, filterOn,
  isMine, groupColumns,
} from "./_lib/columns.js";
import { normalizeFilters, filterAction, facetView, barModel } from "./_lib/filters.js";
import { viewOf, compactSections } from "./_lib/compact.js";
import { renderBoard } from "./board.js";
import { loadPlaceholder, renderLoadLine, renderLoadProgress, renderLoadStrip } from "./load_status.js";
import { renderCompact } from "./compact.js";
import { Workflows } from "./workflows.js";
import { BatchLauncher } from "./batch_launcher.js";
import { renderBatchPage } from "./batch_page.js";
import { FilterBar } from "./filter_bar.js";
import { prKey } from "./_lib/launch.js";
import { SP, LAYOUT } from "./_lib/tokens.js";
import { segment, segmented, emptyPane, pinned, pane, button, iconButton, tag, text, type, statusText, badge, placeholder, errorLine } from "./_ui/ui.js";

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
  return normalizeFilters(shared("prs.filters"));
}

function tabOf() {
  return shared("prs.tab") === "mine" ? "mine" : "team";
}

// Unfiltered: the Batch form applies the shared chips itself (BatchLauncher.shown), so a selected
// PR a chip hides still counts as hidden rather than vanishing.
function mineOpen(state) {
  return (state.prs ?? [])
    .filter((p) => isMine(p, state.me) && p.state !== "MERGED" && p.state !== "CLOSED");
}

function tabBase(state, filters, tab) {
  const me = state.me ?? "";
  if (tab === "mine") {
    return (state.prs ?? []).filter((p) => isMine(p, me) && (filterOn(filters, "mineDrafts") || !p.isDraft));
  }
  return filterPrs(state.prs ?? [], filters, me);
}

function tabView(state, filters, tab) {
  return facetView(tabBase(state, filters, tab), filters, {
    tab, me: state.me ?? "", loadedRepos: state.repoSlugs, authorsFrom: state.prs ?? [],
  });
}


export default class PrsBoard extends View {
  // Handlers run after the render that built them; a redraw or task requested through that render's
  // cx is dropped or delayed, so they go through the long-lived context init receives.
  init(_props, cx) {
    this.acx = cx;
    // The copy mounted in the side panel (not its width) decides whether it offers Full screen.
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
    this.filterBar = new FilterBar(cx);
    this.batchOpen = false;
    this.cache = new Map();
    this.toolsByOn = new Map();
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
    this.freshNext = true;
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

  begin(key) {
    this.cache.set(key, { ...(this.cache.get(key) ?? {}), pulling: true });
  }

  end(key) {
    const cur = this.cache.get(key);
    if (!cur?.pulling) return;
    const next = { ...cur };
    delete next.pulling;
    this.cache.set(key, next);
  }

  fail(key, error) {
    // Rows stay; dropping `phase` lets repoPhase read the error (stale with rows, failed without).
    const next = { ...(this.cache.get(key) ?? {}), error, stale: true };
    delete next.phase;
    this.cache.set(key, next);
  }

  commit(key, data, ctx, cx) {
    this.cache.set(key, { ...data, stale: false });
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

  terminalsFor(ctx, cycle) {
    if (!cycle.has("terminals")) cycle.set("terminals", loadTerminals(exec, ctx));
    return cycle.get("terminals");
  }

  async pull(cx, listingOnly) {
    const cycle = new Map();
    const fresh = !listingOnly && this.freshNext;
    if (!listingOnly) this.freshNext = false;
    const targets = this.activeTargets();
    for (const ctx of targets) this.begin(ctxKey(ctx));
    this.pullStartedAt = Date.now();
    cx.timer.after(LOAD_DELAY_MS, (cx) => cx.notify());
    cx.notify();
    await Promise.all(targets.map(async (ctx) => {
      const key = ctxKey(ctx);
      try {
        if (!ctx.cwd) {
          this.cache.set(key, { error: { kind: "failed", message: "Not in a repository" }, stale: false });
          return;
        }
        await this.pullOne(ctx, key, cx, cycle, listingOnly, fresh);
        cx.notify();
      } finally {
        this.end(key);
      }
    }));
    const keep = [ctxKey(ctxOf()), ...spaceTargets(spaces(), panes()).map(ctxKey)];
    for (const k of staleKeys(this.cache.keys(), keep)) {
      this.cache.delete(k);
      this.repoByKey.delete(k);
    }
  }

  // What each PR is owed is read only while Mine shows, and only when the last read is older than a
  // minute: a listing pull reads it when due.
  showMine() {
    setShared("prs.tab", "mine");
    this.refresh(this.acx, { listingOnly: true });
  }

  // One target: the `pr board` phases, each committed as it lands (_lib/board_pull.js).
  async pullOne(ctx, key, cx, cycle, listingOnly, fresh) {
    const prev = this.cache.get(key);
    if (!listingOnly || !prev?.repo) {
      const have = await this.ensureTools(ctx, key);
      if (!have || !this.same(key)) return;
    }
    const terms = await this.terminalsFor(ctx, cycle);
    if (!this.same(key)) return;
    const done = await pullTarget(exec, ctx, prev, {
      listingOnly, fresh, owed: tabOf() === "mine", entries: terms.entries, now: Date.now(),
      isCurrent: () => this.same(key),
    }, (state) => {
      if (state.repo?.slug) this.repoByKey.set(key, repoId(state.repo));
      this.commit(key, state, ctx, cx);
      cx.notify();
    });
    if (!done.ok) this.fail(key, done.error);
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
    const before = filtersOf();
    const f = filterAction(before, name);
    if (f === before) return;
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
    const load = loadState(view.repoStates, { since: this.pullStartedAt ?? null, now: Date.now() });
    const errLine = (e) => {
      const base = String(e.cwd).split("/").filter(Boolean).pop();
      return `${base ?? ""}${e.on != null ? " on " + e.on : ""}: ${friendlyError(e.error, e.on, scope)}`;
    };
    const picked = this.selectedId
      ? (view.prs.find((p) => cardId(p) === this.selectedId) ?? this.selectedPr) : null;
    const open = picked ? this.detailView(picked, cx) : null;
    if (open) return open.on_size_change((size, cx2) => this.onSize(size, cx2));
    const mode = viewOf(this.size?.width);
    const note = (e) => e.on_size_change((size, cx2) => this.onSize(size, cx2));
    if (this.batchOpen && tabOf() === "mine") {
      this.launcher.loaded = view.repoSlugs;
      this.launcher.knownLabels = tabView(view, filtersOf(), "mine").options.labels;
      return note(renderBatchPage({
        launcher: this.launcher,
        workflows: this.workflows,
        prs: mineOpen(view),
        allPrs: view.prs,
        filterBar: this.filterBar,
        onOpenPr: (pr) => this.openDetail(pr, cx, "batch"),
        onBack: () => { this.batchOpen = false; this.acx.notify(); },
        run,
        load,
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
    if (mode === "compact") return this.compactView(view, t, cx, load);
    return this.boardView(cx, targets[0], view, t, load);
  }

  listed(state) {
    return tabView(state, filtersOf(), tabOf()).list;
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
      .when(running, (e) => e.child(badge(runStatus("running"))));
  }

  onSize(size, cx) { this.size = size; cx.notify(); }

  errorStatus(state) {
    const s = errorsStatus(state.errors, Date.now());
    if (!s) return null;
    const first = state.errors[0];
    return h_flex().min_w(0).gap(SP.sm).items_center().child(badge(s))
      .child(text("meta", friendlyError(first.error, first.on, scopeOf()), theme().muted).min_w(0).truncate());
  }

  compactHeader(state, tab, load) {
    const err = this.errorStatus(state);
    return pinned(v_flex().px(SP.md).py(SP.sm).gap(SP.sm)
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
          .when(this.inPanel, (e) => e.child(this.fullscreenButton()))))
      .when(Boolean(load.shown), (e) => e.child(renderLoadLine(load)))
      .when(Boolean(err), (e) => e.child(err)));
  }

  fullscreenButton() {
    const state = fullscreenState(layout());
    return button("fullscreen", state === "on" ? "Exit full screen" : "Full screen", () => {
      if (state === "on" || state === "stranded") {
        exitFullscreen();
        return;
      }
      enterFullscreen();
    });
  }

  compactView(state, t, cx, load) {
    const tab = tabOf(), mine = tab === "mine";
    const docSpec = columnsFor(state.columns, mine ? "mine" : "team");
    const spec = docSpec.length ? docSpec : mine ? MINE_COLUMNS : TEAM_COLUMNS;
    const sections = compactSections(groupColumns(this.listed(state), mine ? mineColumn : teamColumn, spec));
    const empty = state.loading && !state.repos ? loadPlaceholder(load) : placeholder("No pull requests");
    return pane("prs")
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(this.compactHeader(state, tab, load))
      .when(Boolean(load.shown), (e) => e.child(renderLoadProgress(load)))
      .child(renderCompact({
        sections, onOpen: (pr) => this.openDetail(pr, cx),
        width: this.size?.width, onBatch: mine ? (items, preset) => this.batchFor(items, preset) : null,
        empty,
        workflowOf: (pr) => this.workflows.itemFor(pr.repoSlug, pr.number),
      }));
  }

  scopeToggle() {
    const scope = scopeOf();
    return segmented([
      segment("scope-this", "This space", scope === "this", () => setShared("prs.scope", "this")),
      segment("scope-all", "All spaces", scope === "all", () => setShared("prs.scope", "all")),
    ]);
  }

  boardView(cx, ctx, state, t, load) {
    const filters = filtersOf(), tab = tabOf();
    const fv = tabView(state, filters, tab);
    const repos = scopeOf() === "all" ? [...(state.repoSlugs ?? [])].sort() : [];
    return pane("prs")
      .on_size_change((size, cx) => this.onSize(size, cx))
      .child(this.header(ctx, state, tab, t, load))
      .when(Boolean(load.shown), (e) => e.child(renderLoadProgress(load)))
      .child(this.filterBar.render(barModel(filters, {
        tab, options: { repos, labels: fv.options.labels, authors: fv.options.authors },
      }), (a) => this.toggleChip(a, this.acx)))
      .when(load.strip.length > 0, (e) => e.child(renderLoadStrip(load)))
      .child(renderBoard({
        tab, prs: this.listed(state), filters, me: state.me ?? "",
        onOpen: (pr) => this.openDetail(pr, cx),
        onBatch: (items, preset) => this.batchFor(items, preset),
        loading: Boolean(state.loading && !state.repos), columns: state.columns,
        workflowOf: (pr) => this.workflows.itemFor(pr.repoSlug, pr.number),
      }));
  }

  header(ctx, state, tab, t, load) {
    const loading = state.loading && !state.repos;
    const err = this.errorStatus(state);
    const all = scopeOf() === "all";
    const title = all ? "All spaces" : (loading ? "Loading…" : (state.slug ?? "No repo"));
    const note = all && !load.shown ? (loading ? "Loading…" : `${state.repos} repos`) : null;
    // Title on the left names the subject (the repo); the controls that filter it sit on the right.
    return pinned(h_flex().id("prs-header").min_h(LAYOUT.headerH).flex_wrap().py(SP.sm).px(SP.lg).gap(SP.md).items_center()
      .child(h_flex().flex_1().min_w(0).gap(SP.sm).items_center()
        .child(type(div().min_w(0).truncate(), "title").child(title))
        .when(Boolean(note), (e) => e.child(text("meta", note, t.muted).flex_none()))
        .when(Boolean(load.shown), (e) => e.child(renderLoadLine(load)))
        .when(!all && ctx?.on != null, (e) => e.child(tag(String(ctx.on))))
        .when(Boolean(err), (e) => e.child(err)))
      .when(tab === "mine", (e) => e.child(this.batchControl()))
      .child(this.scopeToggle())
      .child(segmented([
        segment("tab-team", "Team", tab === "team", () => setShared("prs.tab", "team")),
        segment("tab-mine", "Mine", tab === "mine", () => this.showMine()),
      ]))
      .when(this.inPanel, (e) => e.child(this.fullscreenButton()))
      .child(this.refreshButton()));
  }
}
