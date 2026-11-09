// Batch-run parts for the Mine board and a PR's Workflow tab.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import {
  STEP_ORDER, itemTone, stepTone, durationText, laneOf,
} from "./_lib/batch.js";
import { relativeTime } from "./_lib/history.js";
import { SP, TEXT, LAYOUT, BATCH_COL, RADIUS } from "./_lib/tokens.js";
import {
  pill, hoverRow, placeholder, caption, link, scrollBody, mono,
} from "./_ui/ui.js";

function itemColor(t, status) {
  const tone = itemTone(status);
  if (tone === "busy") return t.ansi4;
  if (tone === "attention") return t.ansi3;
  if (tone === "done") return t.ansi2;
  return t.dim;
}

function stepColor(t, status) {
  const tone = stepTone(status);
  if (tone === "busy") return t.ansi4;
  if (tone === "attention") return status === "failed" ? t.ansi1 : t.ansi3;
  if (tone === "done") return t.ansi2;
  return t.dim;
}

function runColor(t, status) {
  if (status === "running") return t.ansi4;
  if (status === "waiting") return t.ansi3;
  if (status === "done") return t.ansi2;
  if (status === "cancelled" || status === "interrupted") return t.ansi1;
  return t.muted;
}

// What a run's status means to someone reading the header.
const RUN_WORD = { running: "running", waiting: "waiting", done: "finished", cancelled: "cancelled", interrupted: "interrupted" };

function runWord(status) {
  return RUN_WORD[status] ?? pretty(status);
}

function prCount(n) {
  return n === 1 ? "1 PR" : `${n} PRs`;
}

function pretty(s) {
  return String(s ?? "").replace(/_/g, " ");
}

export function renderRunBar(state) {
  const t = theme();
  const { runs, runId, doc, error, loading, showRuns, nowMs, onPickRun, onToggleRuns } = state;
  const bar = h_flex().px(SP.lg).py(SP.xs).gap(SP.md).items_center().flex_wrap().flex_none();
  if (loading && !runs.length) return bar.child(caption("Loading batch runs…"));
  if (error) return bar.child(caption(error.message ?? String(error), t.dim));
  if (!runs.length) return bar.child(caption("No batch runs on this machine", t.dim));
  const latest = runId == null || runId === runs[0]?.id;
  const picked = runs.find((r) => r.id === (runId ?? runs[0]?.id));
  const status = doc?.run?.status ?? picked?.status;
  const n = doc?.items?.length ?? picked?.items ?? 0;
  const started = relativeTime(doc?.run?.startedAt ?? picked?.startedAt ?? "", nowMs);
  const earlier = runs.length - 1;
  return bar
    .child(div().text_size(TEXT.small).text_color(t.foreground).child(latest ? "Latest run" : "Earlier run"))
    .child(div().text_size(TEXT.small).text_color(t.muted).child(`· started ${started} · ${prCount(n)} ·`))
    .child(div().text_size(TEXT.small).text_color(runColor(t, status)).child(runWord(status)))
    .when(!latest, (e) => e.child(link("wf-latest", "Back to latest", () => onPickRun(null))))
    .when(earlier > 0, (e) => e.child(link("wf-runs", showRuns ? "Hide earlier runs" : `Earlier runs (${earlier})`, onToggleRuns)));
}

export function renderRunList(state) {
  const t = theme();
  const { runs, runId, nowMs, onPickRun } = state;
  const current = runId ?? runs[0]?.id;
  return scrollBody().px(SP.md).pb(SP.lg).gap(SP.xs)
    .children(runs.map((r, i) => hoverRow(`run-${r.id}`).flex().items_center()
      .px(SP.md).py(SP.sm).gap(SP.md).rounded(RADIUS.control).border_1().border_color(t.border)
      .when(r.id === current, (e) => e.bg(t.selected))
      .on_click(() => onPickRun(i === 0 ? null : r.id))
      .child(v_flex().min_w(0).flex_1()
        .child(div().text_size(TEXT.body).child(`${i === 0 ? "Latest run" : "Run"} · started ${relativeTime(r.startedAt ?? "", nowMs)}`))
        .child(div().text_size(TEXT.caption).text_color(t.dim).truncate()
          .child([prCount(r.items), ...(r.repos ?? [])].join(" · ")))
        .when(Boolean(r.error), (e) => e.child(caption(r.error, t.ansi1))))
      .child(div().flex_none().text_size(TEXT.small).text_color(runColor(t, r.status)).child(runWord(r.status)))));
}

// The steps this run was asked for, in pipeline order: a step it never ran gets no column.
export function runSteps(doc) {
  const asked = doc?.run?.steps ?? [];
  const steps = STEP_ORDER.filter((s) => asked.includes(s));
  return steps.length ? steps : STEP_ORDER;
}

export function renderCardSteps(item, steps) {
  const t = theme();
  return h_flex().gap(SP.sm).flex_wrap().items_center().text_size(TEXT.caption)
    .children((steps ?? []).flatMap((name) => {
      const rec = item.steps?.[name] ?? null;
      if (rec == null) return [];
      return [div().text_color(stepColor(t, rec.status)).child(`${name} ${pretty(rec.status)}`)];
    }))
    .when(laneOf(item) === "attention", (e) => e.child(pill("needs you", t.ansi3)));
}

function renderStepRow(item, name, state) {
  const t = theme();
  const rec = item.steps?.[name] ?? null;
  if (rec == null) {
    return h_flex().id(`step-${name}`).px(SP.sm).py(SP.xs).rounded(RADIUS.control)
      .gap(SP.sm).items_center()
      .child(div().text_size(TEXT.body).w(BATCH_COL.step).text_color(t.dim).child(name))
      .child(div().text_size(TEXT.caption).text_color(t.dim).child("not in this run"));
  }
  const color = stepColor(t, rec.status);
  const selected = state.step === name;
  const dur = durationText(rec.startedAt, rec.endedAt, state.nowMs);
  return hoverRow(`step-${name}`).flex().items_center()
    .px(SP.sm).py(SP.xs).rounded(RADIUS.control).gap(SP.sm)
    .when(selected, (e) => e.bg(t.selected))
    .on_click(() => state.onStep(name))
    .child(div().text_size(TEXT.body).w(BATCH_COL.step).child(name))
    .child(div().text_size(TEXT.caption).text_color(color).child(pretty(rec.status)))
    .when(Boolean(dur), (e) => e.child(div().text_size(TEXT.caption).text_color(t.dim).child(dur)))
    .when(rec.exitCode != null, (e) => e.child(
      div().text_size(TEXT.caption)
        .text_color(rec.exitCode !== 0 ? t.ansi1 : t.dim)
        .child(`exit ${rec.exitCode}`)));
}

function renderFindings(item) {
  const t = theme();
  const findings = item.findings ?? [];
  if (!findings.length) return null;
  return v_flex().flex_none().gap(SP.sm)
    .child(div().text_size(TEXT.small).font_semibold().text_color(t.muted).child("Findings"))
    .children(findings.map((f, i) => {
      const files = (f.files ?? []).join(", ");
      return v_flex().id(`finding-${i}`).px(SP.sm).py(SP.xs)
        .border_1().border_color(t.border).rounded(RADIUS.control).gap(SP.xxs)
        .child(div().text_size(TEXT.body).font_medium().child(pretty(f.reason)))
        .child(caption(`step · ${f.step ?? ""}`))
        .when(Boolean(f.detail), (e) => e.child(
          div().text_size(TEXT.small).text_color(t.muted).child(f.detail)))
        .when(Boolean(files), (e) => e.child(caption(files)));
    }));
}

function renderLogPane(item, state) {
  const t = theme();
  if (!state.step) return placeholder("Pick a step to see its log").p(SP.md);
  const path = item.steps?.[state.step]?.logPath;
  const log = state.log;
  let body;
  if (log == null) body = placeholder("Loading log…");
  else if (log.error) body = placeholder(log.error);
  else {
    const lines = String(log.text ?? "").split("\n");
    body = v_flex().children(lines.map((l) =>
      div().font_family(mono()).text_size(TEXT.small).child(l === "" ? " " : l)));
  }
  return v_flex().flex_1().min_h(0).min_w(0)
    .child(h_flex().flex_none().px(SP.md).py(SP.xs).gap(SP.sm).items_center()
      .child(div().flex_none().text_size(TEXT.small).font_semibold().child(`${state.step} log`))
      .child(div().min_w(0).truncate().child(caption(path ?? "", t.dim))))
    .child(scrollBody().flex_1().px(SP.md).py(SP.sm).child(body));
}

export function renderItemBody(item, state) {
  const t = theme();
  const findings = renderFindings(item);
  return h_flex().flex_1().min_h(0)
    .child(v_flex().w(LAYOUT.colMin + LAYOUT.colMin).flex_none().min_h(0)
      .overflow_y_scrollbar().px(SP.lg).py(SP.md).gap(SP.md)
      .child(v_flex().gap(SP.xs)
        .when(Boolean(item.worktree), (e) => e.child(
          caption(item.worktree).font_family(mono())))
        .when(Boolean(item.waitReason), (e) => e.child(caption(item.waitReason, t.ansi3)))
        .when(item.headMoved, (e) => e.child(pill("head moved", t.ansi3))))
      .child(div().text_size(TEXT.small).font_semibold().text_color(t.muted).child("Steps"))
      .children(runSteps(state.doc).map((name) => renderStepRow(item, name, state)))
      .when(Boolean(findings), (e) => e.child(findings)))
    .child(v_flex().flex_1().min_w(0).min_h(0).border_l_1().border_color(t.border)
      .child(renderLogPane(item, state)));
}
