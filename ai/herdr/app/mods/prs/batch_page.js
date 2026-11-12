// Batch page: running runs, start form, recent runs. Replaces Mine in the pane.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { STEP_ORDER, STEP_LABEL, AI_STEPS, runStatus } from "./_lib/batch.js";
import { filterFacets } from "./_lib/columns.js";
import { barModel } from "./_lib/filters.js";
import { prKey } from "./_lib/launch.js";
import { SP } from "./_lib/tokens.js";
import {
  scrollBody, button, solidButton, pane, pinned, checkbox,
  sectionHeading, text, type, statusText, badge, dangerColor,
} from "./_ui/ui.js";
import { renderRunPanel, renderRunItems } from "./workflows_view.js";
import { loadPlaceholder, renderLoadLine } from "./load_status.js";

function shortRepo(slug) {
  return String(slug ?? "").split("/").pop();
}

function machineOf(on) {
  return on ?? "this computer";
}

function groupPrs(prs) {
  const groups = [];
  const index = new Map();
  for (const pr of prs ?? []) {
    const repo = pr.repoSlug;
    if (!repo) continue;
    const gk = `${pr.on ?? ""}\0${repo}`;
    let g = index.get(gk);
    if (!g) {
      g = { repo, on: pr.on, prs: [] };
      index.set(gk, g);
      groups.push(g);
    }
    g.prs.push(pr);
  }
  return groups;
}

function renderRunning({ launcher: L, workflows, run }) {
  const t = theme();
  const runs = workflows.activeRuns();
  if (!runs.length) return null;
  return v_flex().gap(SP.sm)
    .child(sectionHeading("Running"))
    .children(runs.map((entry) => {
      const on = entry.on;
      const id = entry.run.id;
      const killKey = `${on ?? ""}\0${id}`;
      const confirm = L.confirmKill === killKey;
      const repos = (entry.run.repos ?? []).map(shortRepo).join(", ");
      const first = entry.run.repos?.[0];
      const terminal = first ? L.terminalFor(on, first) : null;
      const progress = entry.progress ?? { done: 0, total: 0 };
      return h_flex().id(`run-${killKey}`).gap(SP.md).items_center()
        .child(text("body", repos))
        .child(text("meta", machineOf(on), t.muted))
        .child(badge(runStatus("running")))
        .child(text("meta", `${progress.done} of ${progress.total} done`, t.muted))
        .child(div().flex_1())
        .when(Boolean(terminal), (e) => e.child(
          button(`log-${killKey}`, "Log", () => run("terminal.keep", { terminal }))))
        .child(button(`cancel-${killKey}`, "Cancel", () => { void L.cancel(on, id, false); }))
        .when(confirm, (e) => e.child(text("body", "Kills the run's processes", dangerColor())))
        .child(button(`kill-${killKey}`, confirm ? "Confirm kill" : "Kill", () => { void L.cancel(on, id, true); }));
    }));
}

function prRow(pr, L, t) {
  const key = prKey(pr.repoSlug, pr.number);
  const runnable = Boolean(pr.cwd);
  const row = h_flex().id(`pr-${key}`).gap(SP.md).items_center().px(SP.sm).py(SP.xs)
    .when(runnable, (e) => e.cursor_pointer().on_click(() => L.toggle(key)).child(checkbox(L.selected.has(key))))
    .child(text("body", `#${pr.number}`, t.muted).flex_none())
    .child(type(div().min_w(0).flex_1().truncate(), "body")
      .text_color(runnable ? t.foreground : t.muted).child(pr.title ?? ""))
    .child(type(div().flex_none(), "code").text_color(t.muted).child(pr.branch ?? ""));
  if (runnable) return row;
  return v_flex().gap(SP.xxs)
    .child(row)
    .child(text("body", "No checkout of this repo on its machine", t.muted).px(SP.sm));
}

function renderFormFilters(L, prs, filterBar) {
  return filterBar.render(
    barModel(L.filters.get(), { tab: "batch", options: { ...filterFacets(prs), authors: [] } }),
    (a) => L.filters.toggle(a),
  );
}

function renderPrs(L, prs, t, load) {
  const shown = L.shown(prs);
  if (!prs.length) {
    return v_flex().gap(SP.sm)
      .child(text("title", "PRs"))
      .child(load?.busy
        ? loadPlaceholder(load)
        : text("body", "No open PRs of yours match the filters", t.muted));
  }
  if (!shown.length) {
    return v_flex().gap(SP.sm)
      .child(text("title", "PRs"))
      .child(text("body", "No PRs match these filters", t.muted));
  }
  return v_flex().gap(SP.sm)
    .child(text("title", "PRs"))
    .children(groupPrs(shown).map((g) => {
      const keys = g.prs.filter((p) => p.cwd).map((p) => prKey(p.repoSlug, p.number));
      const allOn = keys.length > 0 && keys.every((k) => L.selected.has(k));
      return v_flex().id(`repo-${g.on ?? ""}-${g.repo}`).gap(SP.xs)
        .child(h_flex().gap(SP.md).items_center()
          .when(keys.length > 0, (e) => e.cursor_pointer().on_click(() => L.toggleRepo(keys))
            .child(checkbox(allOn)))
          .child(text("body", shortRepo(g.repo)))
          .child(text("meta", machineOf(g.on), t.muted)))
        .children(g.prs.map((pr) => prRow(pr, L, t)));
    }));
}

function checkLine(id, on, label, extra, onToggle) {
  const t = theme();
  return h_flex().id(id).gap(SP.md).items_center().cursor_pointer().on_click(onToggle)
    .child(checkbox(on))
    .child(text("body", label))
    .when(Boolean(extra), (e) => e.child(text("meta", extra, t.muted).min_w(0)));
}

function renderForm({ launcher: L, prs, load, filterBar }) {
  const t = theme();
  const all = prs ?? [];
  const shown = L.shown(all);
  const selectedPrs = shown.filter((p) => L.selected.has(prKey(p.repoSlug, p.number)));
  const hiddenSelected = all.filter((p) => L.selected.has(prKey(p.repoSlug, p.number))).length
    - selectedPrs.length;
  const disabled = L.starting || selectedPrs.length === 0 || L.steps.size === 0;
  const reason = L.starting ? null
    : selectedPrs.length === 0 ? "Select at least one PR"
    : L.steps.size === 0 ? "Turn on at least one step"
    : null;
  const filters = renderFormFilters(L, all, filterBar);
  return v_flex().gap(SP.lg)
    .child(filters)
    .child(renderPrs(L, all, t, load))
    .child(v_flex().gap(SP.sm)
      .child(text("title", "Steps"))
      .children(STEP_ORDER.map((s) => checkLine(
        `step-${s}`, L.steps.has(s), STEP_LABEL[s], "", () => L.toggleStep(s)))))
    .child(v_flex().gap(SP.sm)
      .child(text("title", "Options"))
      .children(AI_STEPS.map((s) => checkLine(
        `ai-${s}`, L.withAi.has(s), `Use AI for ${s}`, "", () => L.toggleAi(s))))
      .when(L.steps.has("rebase"), (e) => e.child(checkLine(
        "rebase-behind", L.rebaseBehind, "Also rebase PRs that are only behind", "",
        () => L.toggleRebaseBehind())))
      .child(checkLine("auto-publish", L.autoPublish, "Push clean fast-forwards", "", () => L.toggleAutoPublish())))
    .child(v_flex().gap(SP.sm)
      .when(Boolean(reason), (e) => e.child(text("body", reason, t.muted)))
      .when(hiddenSelected > 0, (e) => e.child(
        text("body", `${hiddenSelected} selected PR(s) hidden by filters won't run`, t.muted)))
      .when(Boolean(L.note), (e) => e.child(text("body", L.note, dangerColor())))
      .child(h_flex().gap(SP.md).items_center()
        .child(solidButton("batch-start", "Start", () => L.launch(prs), { disabled }))
        .child(button("batch-clear", "Clear", () => L.reset()))
        .when(L.starting, (e) => e.child(statusText("running", "Starting")))));
}

export function renderBatchPage({ launcher, workflows, prs, allPrs, onBack, onOpenPr, run, load, filterBar }) {
  const byKey = new Map((allPrs ?? prs ?? []).map((p) => [prKey(p.repoSlug, p.number).toLowerCase(), p]));
  const prOf = (item) => byKey.get(String(item.key ?? "").toLowerCase()) ?? null;
  const expanded = (r) => renderRunItems(workflows.docOf(r.on, r.id), { repos: r.repos ?? [], prOf, onOpen: onOpenPr });
  const running = renderRunning({ launcher, workflows, run });
  return pane("prs-batch")
    .child(pinned(h_flex().gap(SP.md).items_center().px(SP.lg).py(SP.md)
      .child(button("batch-back", "← Back", onBack))
      .child(text("heading", "Batch"))
      .when(Boolean(load?.shown), (e) => e.child(renderLoadLine(load)))))
    .child(scrollBody().id("batch-body").px(SP.lg).py(SP.lg).gap(SP.xl)
      .when(Boolean(running), (e) => e.child(running))
      .child(renderForm({ launcher, prs, load, filterBar }))
      .child(v_flex().gap(SP.sm)
        .child(sectionHeading("Recent runs"))
        .child(renderRunPanel(workflows.barState(), expanded))));
}
