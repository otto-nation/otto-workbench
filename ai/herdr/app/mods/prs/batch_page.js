// Batch page: running runs, start form, recent runs. Replaces Mine in the pane.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { STEP_NAMES, STEP_HELP, formFacets } from "./_lib/batch_launch.js";
import { prKey } from "./_lib/launch.js";
import { SP } from "./_lib/tokens.js";
import {
  scrollBody, button, solidButton, pane, paneBar, chip as chipOf, filterGroup,
  sectionLabel, text, type, statusText,
} from "./_ui/ui.js";
import { renderRunPanel, renderRunItems } from "./workflows_view.js";

function shortRepo(slug) {
  return String(slug ?? "").split("/").pop();
}

function machineOf(on) {
  return on ?? "this computer";
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
    .child(sectionLabel("Running"))
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
        .child(statusText("running", "Running"))
        .child(text("meta", `${progress.done} of ${progress.total} done`, t.muted))
        .child(div().flex_1())
        .when(Boolean(terminal), (e) => e.child(
          button(`log-${killKey}`, "Log", () => run("terminal.keep", { terminal }))))
        .child(button(`cancel-${killKey}`, "Cancel", () => { void L.cancel(on, id, false); }))
        .when(confirm, (e) => e.child(text("body", "Kills the run's processes", t.ansi1)))
        .child(button(`kill-${killKey}`, confirm ? "Confirm kill" : "Kill", () => { void L.cancel(on, id, true); }));
    }));
}

function prRow(pr, L, t) {
  const key = prKey(pr.repoSlug, pr.number);
  const runnable = Boolean(pr.cwd);
  const row = h_flex().id(`pr-${key}`).gap(SP.md).items_center().px(SP.sm).py(SP.xs)
    .when(runnable, (e) => e.cursor_pointer().on_click(() => L.toggle(key)).child(cardCheckbox(L.selected.has(key))))
    .child(text("body", `#${pr.number}`, t.muted).flex_none())
    .child(type(div().min_w(0).flex_1().truncate(), "body")
      .text_color(runnable ? t.foreground : t.muted).child(pr.title ?? ""))
    .child(type(div().flex_none(), "code").text_color(t.muted).child(pr.branch ?? ""));
  if (runnable) return row;
  return v_flex().gap(SP.xxs)
    .child(row)
    .child(text("body", "No checkout of this repo on its machine", t.muted).px(SP.sm));
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
      .child(text("body", "No open PRs of yours match the filters", t.muted));
  }
  if (!shown.length) {
    return v_flex().gap(SP.sm)
      .child(sectionLabel("PRs"))
      .child(text("body", "No PRs match these filters", t.muted));
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
          .child(text("body", shortRepo(g.repo)))
          .child(text("meta", machineOf(g.on), t.muted)))
        .children(g.prs.map((pr) => prRow(pr, L, t)));
    }));
}

function checkLine(id, on, label, extra, onToggle) {
  const t = theme();
  return h_flex().id(id).gap(SP.md).items_center().cursor_pointer().on_click(onToggle)
    .child(cardCheckbox(on))
    .child(text("body", label))
    .when(Boolean(extra), (e) => e.child(text("meta", extra, t.muted).min_w(0)));
}

function renderForm({ launcher: L, prs }) {
  const t = theme();
  const all = prs ?? [];
  const shown = L.shown(all);
  const selectedPrs = shown.filter((p) => L.selected.has(prKey(p.repoSlug, p.number)));
  const hiddenSelected = all.filter((p) => L.selected.has(prKey(p.repoSlug, p.number))).length
    - selectedPrs.length;
  const disabled = L.launching || selectedPrs.length === 0 || L.steps.size === 0;
  const reason = L.launching ? null
    : selectedPrs.length === 0 ? "Select at least one PR"
    : L.steps.size === 0 ? "Turn on at least one step"
    : null;
  const filters = renderFormFilters(L, all);
  return v_flex().gap(SP.lg)
    .child(sectionLabel("Start a batch"))
    .when(Boolean(filters), (e) => e.child(filters))
    .child(renderPrs(L, all, t))
    .child(v_flex().gap(SP.sm)
      .child(sectionLabel("Steps"))
      .children(STEP_NAMES.map((s) => checkLine(`step-${s}`, L.steps.has(s), s, STEP_HELP[s] ?? "", () => L.toggleStep(s)))))
    .child(v_flex().gap(SP.sm)
      .child(sectionLabel("Options"))
      .children(["rebase", "ci", "comments"].map((s) => checkLine(
        `ai-${s}`, L.withAi.has(s), `Use AI for ${s}`, "", () => L.toggleAi(s))))
      .child(checkLine("auto-publish", L.autoPublish, "Push clean fast-forwards", "", () => L.toggleAutoPublish())))
    .child(v_flex().gap(SP.sm)
      .when(Boolean(reason), (e) => e.child(text("body", reason, t.muted)))
      .when(hiddenSelected > 0, (e) => e.child(
        text("body", `${hiddenSelected} selected PR(s) hidden by filters won't run`, t.muted)))
      .when(Boolean(L.note), (e) => e.child(text("body", L.note, t.ansi1)))
      .child(h_flex().gap(SP.md).items_center()
        .child(solidButton(
          "batch-start",
          L.launching ? "Starting…" : "Start",
          () => L.launch(prs),
          { disabled },
        ))
        .child(button("batch-clear", "Clear", () => L.reset()))));
}

export function renderBatchPage({ launcher, workflows, prs, allPrs, onBack, onOpenPr, run }) {
  const byKey = new Map((allPrs ?? prs ?? []).map((p) => [prKey(p.repoSlug, p.number).toLowerCase(), p]));
  const prOf = (item) => byKey.get(String(item.key ?? "").toLowerCase()) ?? null;
  const expanded = (r) => renderRunItems(workflows.docOf(r.on, r.id), { repos: r.repos ?? [], prOf, onOpen: onOpenPr });
  const running = renderRunning({ launcher, workflows, run });
  return pane("prs-batch")
    .child(paneBar()
      .child(h_flex().gap(SP.sm).items_center()
        .child(button("batch-back", "Back", onBack))
        .child(text("title", "Batch"))))
    .child(scrollBody().id("batch-body").px(SP.lg).py(SP.lg).gap(SP.xl)
      .when(Boolean(running), (e) => e.child(running))
      .child(renderForm({ launcher, prs }))
      .child(v_flex().gap(SP.sm)
        .child(sectionLabel("Recent runs"))
        .child(renderRunPanel(workflows.barState(), expanded))));
}
