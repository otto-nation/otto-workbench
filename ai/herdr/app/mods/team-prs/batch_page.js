// Batch page: running runs, start form, recent runs. Replaces Mine in the pane.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { groupByRepo, STEP_NAMES, STEP_HELP, planSummary, formFacets } from "./_lib/batch_launch.js";
import { prKey } from "./_lib/launch.js";
import { SP, TEXT } from "./_lib/tokens.js";
import { scrollBody, mono, button, solidButton, pane, paneBar, chip as chipOf, filterGroup } from "./_ui/ui.js";
import { renderRunPanel } from "./workflows_view.js";

function shortRepo(slug) {
  return String(slug ?? "").split("/").pop();
}

function machineOf(on) {
  return on ?? "this computer";
}

function sectionLabel(text) {
  const t = theme();
  return div().font_family(mono()).text_size(TEXT.caption).text_color(t.muted).child(text);
}

function cardCheckbox(on) {
  const t = theme();
  return div().flex_none().w(SP.md).h(SP.md).rounded(SP.xxs).border_1().border_color(t.dim)
    .when(Boolean(on), (e) => e.bg(t.foreground));
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
    .child(sectionLabel("RUNNING"))
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
        .child(div().text_size(TEXT.small).text_color(t.foreground).child(repos))
        .child(div().text_size(TEXT.small).text_color(t.muted).child(machineOf(on)))
        .child(div().text_size(TEXT.small).text_color(t.working).child("running"))
        .child(div().text_size(TEXT.small).text_color(t.muted).child(`${progress.done} of ${progress.total} done`))
        .child(div().flex_1())
        .when(Boolean(terminal), (e) => e.child(
          button(`log-${killKey}`, "Log", () => run("terminal.keep", { terminal }))))
        .child(button(`cancel-${killKey}`, "Cancel", () => { void L.cancel(on, id, false); }))
        .when(confirm, (e) => e.child(
          div().text_size(TEXT.small).text_color(t.ansi1).child("Kills the run's processes")))
        .child(button(`kill-${killKey}`, confirm ? "Confirm kill" : "Kill", () => { void L.cancel(on, id, true); }));
    }));
}

function prRow(pr, L, t) {
  const key = prKey(pr.repoSlug, pr.number);
  const runnable = Boolean(pr.cwd);
  const row = h_flex().id(`pr-${key}`).gap(SP.md).items_center().px(SP.sm).py(SP.xs)
    .when(runnable, (e) => e.cursor_pointer().on_click(() => L.toggle(key)).child(cardCheckbox(L.selected.has(key))))
    .child(div().flex_none().text_size(TEXT.small).text_color(t.muted).child(`#${pr.number}`))
    .child(div().min_w(0).flex_1().truncate().text_size(TEXT.small)
      .text_color(runnable ? t.foreground : t.muted).child(pr.title ?? ""))
    .child(div().flex_none().text_size(TEXT.caption).text_color(t.muted).font_family(mono())
      .child(pr.branch ?? ""));
  if (runnable) return row;
  return v_flex().gap(SP.xxs)
    .child(row)
    .child(div().px(SP.sm).text_size(TEXT.caption).text_color(t.muted)
      .child("no checkout of this repo on its machine"));
}

function renderFormFilters(L, prs) {
  const facets = formFacets(prs);
  const repoOn = new Set([...L.repoFilter].map((r) => r.toLowerCase()));
  const labelOn = new Set([...L.labelFilter].map((n) => n.toLowerCase()));
  const any = L.repoFilter.size > 0 || L.labelFilter.size > 0;
  if (facets.repos.length <= 1 && facets.labels.length === 0 && !any) return null;
  const out = v_flex().gap(SP.xs);
  if (facets.repos.length > 1) {
    out.child(filterGroup("Repo", facets.repos.map((slug) => chipOf(
      `chip-repo:${slug}`, shortRepo(slug), repoOn.has(slug.toLowerCase()),
      () => L.toggleRepoFilter(slug)))));
  }
  if (facets.labels.length > 0) {
    out.child(filterGroup("Label", facets.labels.map((name) => chipOf(
      `chip-label:${name}`, name, labelOn.has(name.toLowerCase()),
      () => L.toggleLabelFilter(name)))));
  }
  if (any) out.child(button("batch-filters-clear", "Clear filters", () => L.clearFilters()));
  return out;
}

function renderPrs(L, prs, t) {
  const shown = L.shown(prs);
  if (!prs.length) {
    return v_flex().gap(SP.sm)
      .child(sectionLabel("PRs"))
      .child(div().text_size(TEXT.small).text_color(t.muted)
        .child("No open PRs of yours match the filters"));
  }
  if (!shown.length) {
    return v_flex().gap(SP.sm)
      .child(sectionLabel("PRs"))
      .child(div().text_size(TEXT.small).text_color(t.muted)
        .child("No PRs match these filters"));
  }
  return v_flex().gap(SP.sm)
    .child(sectionLabel("PRs"))
    .children(groupPrs(shown).map((g) => {
      const keys = g.prs.filter((p) => p.cwd).map((p) => prKey(p.repoSlug, p.number));
      const allOn = keys.length > 0 && keys.every((k) => L.selected.has(k));
      return v_flex().id(`repo-${g.on ?? ""}-${g.repo}`).gap(SP.xs)
        .child(h_flex().gap(SP.md).items_center()
          .when(keys.length > 0, (e) => e.cursor_pointer().on_click(() => L.toggleRepo(keys))
            .child(cardCheckbox(allOn)))
          .child(div().text_size(TEXT.small).font_semibold().text_color(t.foreground)
            .child(shortRepo(g.repo)))
          .child(div().text_size(TEXT.small).text_color(t.muted).child(machineOf(g.on))))
        .children(g.prs.map((pr) => prRow(pr, L, t)));
    }));
}

function checkLine(id, on, label, extra, onToggle, { strong = false } = {}) {
  const t = theme();
  const name = div().text_size(TEXT.small).text_color(t.foreground).child(label);
  return h_flex().id(id).gap(SP.md).items_center().cursor_pointer().on_click(onToggle)
    .child(cardCheckbox(on))
    .child(strong ? name.font_semibold() : name)
    .when(Boolean(extra), (e) => e.child(
      div().min_w(0).text_size(TEXT.small).text_color(t.muted).child(extra)));
}

function renderForm({ launcher: L, prs }) {
  const t = theme();
  const all = prs ?? [];
  const shown = L.shown(all);
  const selectedPrs = shown.filter((p) => L.selected.has(prKey(p.repoSlug, p.number)));
  const hiddenSelected = all.filter((p) => L.selected.has(prKey(p.repoSlug, p.number))).length
    - selectedPrs.length;
  const { groups } = groupByRepo(selectedPrs);
  const steps = STEP_NAMES.filter((s) => L.steps.has(s));
  const r = groups.length;
  const disabled = L.launching || selectedPrs.length === 0 || L.steps.size === 0;
  const reason = L.launching ? null
    : selectedPrs.length === 0 ? "Select at least one PR"
    : L.steps.size === 0 ? "Turn on at least one step"
    : null;
  const summary = reason ?? planSummary(groups, steps);
  const filters = renderFormFilters(L, all);
  return v_flex().gap(SP.lg)
    .child(sectionLabel("START A BATCH"))
    .when(Boolean(filters), (e) => e.child(filters))
    .child(renderPrs(L, all, t))
    .child(v_flex().gap(SP.sm)
      .child(sectionLabel("Steps"))
      .children(STEP_NAMES.map((s) => checkLine(`step-${s}`, L.steps.has(s), s, STEP_HELP[s] ?? "", () => L.toggleStep(s), { strong: true }))))
    .child(v_flex().gap(SP.sm)
      .child(sectionLabel("Options"))
      .children(["rebase", "ci", "comments"].map((s) => checkLine(
        `ai-${s}`, L.withAi.has(s), `Use AI for ${s}`, "", () => L.toggleAi(s))))
      .child(checkLine("auto-publish", L.autoPublish, "Push clean fast-forwards", "", () => L.toggleAutoPublish())))
    .child(v_flex().gap(SP.sm)
      .child(div().text_size(TEXT.small).text_color(reason ? t.muted : t.foreground).child(summary ?? ""))
      .when(hiddenSelected > 0, (e) => e.child(
        div().text_size(TEXT.small).text_color(t.muted)
          .child(`${hiddenSelected} selected PR(s) hidden by filters won't run`)))
      .when(Boolean(L.note), (e) => e.child(
        div().text_size(TEXT.small).text_color(t.ansi1).child(L.note)))
      .child(h_flex().gap(SP.md).items_center()
        .child(solidButton(
          "batch-start",
          L.launching ? "Starting…" : `Start ${r} run${r === 1 ? "" : "s"}`,
          () => L.launch(prs),
          { disabled },
        ))
        .child(button("batch-clear", "Clear", () => L.reset()))));
}

export function renderBatchPage({ launcher, workflows, prs, onBack, run }) {
  const running = renderRunning({ launcher, workflows, run });
  return pane("prs-batch")
    .child(paneBar()
      .child(h_flex().gap(SP.sm).items_center()
        .child(button("batch-back", "Back", onBack))
        .child(div().text_size(TEXT.title).font_semibold().child("Batch"))))
    .child(scrollBody().id("batch-body").px(SP.lg).py(SP.lg).gap(SP.xl)
      .when(Boolean(running), (e) => e.child(running))
      .child(renderForm({ launcher, prs }))
      .child(v_flex().gap(SP.sm)
        .child(sectionLabel("RECENT RUNS"))
        .child(renderRunPanel(workflows.barState()))));
}
