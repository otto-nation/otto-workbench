// Read-only Workflows: grouped list of batch items and a per-item detail page.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import {
  STEP_ORDER, LANES, groupLanes, laneCounts, stepIcon, itemTone, stepTone,
  shortKey, durationText,
} from "./_lib/batch.js";
import { SP, TEXT, LAYOUT, BATCH_COL, RADIUS } from "./_lib/tokens.js";
import {
  pill, hoverRow, placeholder, caption, link, emptyPane,
  scrollBody, segment, segmented, pane, mono, chip,
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

function stamp(id) {
  const i = String(id).indexOf("-");
  return i === -1 ? String(id) : String(id).slice(0, i);
}

function pretty(s) {
  return String(s ?? "").replace(/_/g, " ");
}

function reposOf(items) {
  return [...new Set((items ?? []).map((i) => i.repo).filter(Boolean))];
}

function renderEmpty(loading, error, viewSwitch) {
  const msg = loading ? "Loading runs…" : (error ? (error.message ?? String(error)) : "No batch runs on this machine");
  return emptyPane()
    .when(Boolean(viewSwitch), (e) => e.child(viewSwitch))
    .child(placeholder(msg));
}

function renderHeader(state) {
  const t = theme();
  const { on, runs, runId, doc, viewSwitch, onPickRun, onRefresh } = state;
  const selected = runId ?? runs[0]?.id;
  const picked = runs.find((r) => r.id === selected);
  const status = doc?.run?.status ?? picked?.status;
  const active = Boolean(doc?.run?.active ?? picked?.active);
  const segs = runs.slice(0, 6).map((r) =>
    segment(`run-${r.id}`, stamp(r.id), r.id === selected, () => onPickRun(r.id)));
  return h_flex().id("batch-header").min_h(LAYOUT.headerH).flex_none().flex_wrap()
    .px(SP.lg).py(SP.sm).gap(SP.md).items_center()
    .when(Boolean(viewSwitch), (e) => e.child(viewSwitch))
    .child(div().flex_none().text_size(TEXT.title).font_semibold().child("Workflows"))
    .child(pill(on ?? "This computer"))
    .when(segs.length > 0, (e) => e.child(segmented(segs)))
    .when(runs.length > 6, (e) => e.child(caption(`+${runs.length - 6} older`)))
    .when(Boolean(status), (e) => e.child(pill(pretty(status), runColor(t, status))))
    .when(active, (e) => e.child(pill("active", t.ansi4)))
    .child(div().flex_1().min_w(0))
    .child(link("wf-refresh", "Refresh", onRefresh));
}

function renderChips(items, filter, onFilter) {
  const counts = laneCounts(items);
  const chips = LANES.filter((l) => counts[l.id] > 0)
    .map((l) => chip(`lane-${l.id}`, `${l.label} ${counts[l.id]}`, filter === l.id, () => onFilter(l.id)));
  if (!chips.length) return null;
  return h_flex().flex_none().px(SP.lg).pb(SP.sm).gap(SP.xs).flex_wrap().items_center()
    .children(chips);
}

// The steps this run was asked for, in pipeline order: a step it never ran gets no column.
function runSteps(doc) {
  const asked = doc?.run?.steps ?? [];
  const steps = STEP_ORDER.filter((s) => asked.includes(s));
  return steps.length ? steps : STEP_ORDER;
}

function colHead(label, w, center = false) {
  const t = theme();
  const el = div().flex_none().text_size(TEXT.caption).text_color(t.dim).child(label)
    .when(center, (e) => e.flex().justify_center());
  return w != null ? el.w(w) : el.min_w(0).flex_1();
}

function renderColumns(steps) {
  return h_flex().id("batch-columns").flex_none().px(SP.md)
    .child(h_flex().flex_1().px(SP.md).gap(SP.md).items_center()
      .child(colHead("", BATCH_COL.key))
      .child(colHead("Status", BATCH_COL.status))
      .children(steps.map((step) => colHead(step, BATCH_COL.step, true)))
      .child(colHead("Notes")));
}

function stepMark(item, name) {
  const t = theme();
  const rec = item.steps?.[name] ?? null;
  const color = rec == null ? t.dim : stepColor(t, rec.status);
  const icon = rec == null ? "·" : stepIcon(rec.status);
  return div().flex_none().w(BATCH_COL.step).flex().items_center().justify_center()
    .child(div().text_size(TEXT.title).text_color(color).child(icon));
}

function notesOf(item) {
  const t = theme();
  const n = item.findings?.length ?? 0;
  return h_flex().min_w(0).flex_1().gap(SP.xs).items_center()
    .when(item.headMoved, (e) => e.child(pill("head moved", t.ansi3)))
    .when(n > 0, (e) => e.child(pill(n === 1 ? "1 finding" : `${n} findings`, t.ansi3)))
    .when(Boolean(item.waitReason), (e) => e.child(
      div().min_w(0).truncate().child(caption(item.waitReason, t.muted))));
}

function renderRow(item, repos, steps, onOpenItem) {
  const t = theme();
  return hoverRow(`row-${item.key}`).flex().items_center()
    .px(SP.md).py(SP.sm).gap(SP.md).rounded(RADIUS.control)
    .border_1().border_color(t.border).bg(t.background)
    .on_click(() => onOpenItem(item.key))
    .child(v_flex().flex_none().w(BATCH_COL.key).min_w(0)
      .child(div().text_size(TEXT.title).font_medium().child(shortKey(item.key, repos)))
      .child(div().text_size(TEXT.caption).text_color(t.dim).truncate().child(item.branch ?? "")))
    .child(h_flex().flex_none().w(BATCH_COL.status)
      .child(pill(pretty(item.status), itemColor(t, item.status))))
    .children(steps.map((name) => stepMark(item, name)))
    .child(notesOf(item));
}

function renderLane(lane, state, repos) {
  const t = theme();
  const { filter, collapsed, onToggleLane, onOpenItem } = state;
  const open = !collapsed.has(lane.id) || filter === lane.id;
  return v_flex().id(`lane-${lane.id}`).flex_none().gap(SP.xs)
    .child(hoverRow(`lane-head-${lane.id}`).flex().items_center().px(SP.sm).py(SP.xs).gap(SP.sm)
      .on_click(() => onToggleLane(lane.id))
      .child(div().text_size(TEXT.small).text_color(t.muted).child(open ? "▾" : "▸"))
      .child(div().text_size(TEXT.small).font_semibold().text_color(t.muted).child(lane.label))
      .child(caption(String(lane.items.length))))
    .when(open, (e) => e.children(lane.items.map((item) => renderRow(item, repos, runSteps(state.doc), onOpenItem))));
}

function renderList(items, state) {
  const repos = reposOf(items);
  const chips = renderChips(items, state.filter, state.onFilter);
  return v_flex().flex_1().min_h(0)
    .child(renderHeader(state))
    .when(Boolean(chips), (e) => e.child(chips))
    .child(renderColumns(runSteps(state.doc)))
    .child(scrollBody().px(SP.md).pb(SP.lg).gap(SP.xs)
      .children(groupLanes(items)
        .filter((lane) => lane.items.length > 0 && (state.filter == null || state.filter === lane.id))
        .map((lane) => renderLane(lane, state, repos))));
}

function renderDetailHeader(item, state) {
  const t = theme();
  return h_flex().flex_none().px(SP.lg).py(SP.sm).gap(SP.md).items_center()
    .border_b_1().border_color(t.border)
    .child(link("wf-back", "← Workflows", state.onBack))
    .child(div().min_w(0).text_size(TEXT.title).font_semibold().child(item.key ?? ""))
    .child(pill(pretty(item.status), itemColor(t, item.status)))
    .child(div().min_w(0).truncate().child(caption(item.branch ?? "", t.dim)))
    .child(div().flex_1().min_w(0))
    .child(link("wf-refresh-detail", "Refresh", state.onRefresh));
}

function renderStepRow(item, name, state) {
  const t = theme();
  const rec = item.steps?.[name] ?? null;
  if (rec == null) {
    return h_flex().id(`step-${name}`).px(SP.sm).py(SP.xs).rounded(RADIUS.control)
      .gap(SP.sm).items_center()
      .child(div().text_size(TEXT.title).text_color(t.dim).child("·"))
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
    .child(div().text_size(TEXT.title).text_color(color).child(stepIcon(rec.status)))
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

function renderDetail(item, state) {
  const t = theme();
  const findings = renderFindings(item);
  return v_flex().flex_1().min_h(0)
    .child(renderDetailHeader(item, state))
    .child(h_flex().flex_1().min_h(0)
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
        .child(renderLogPane(item, state))));
}

export function renderWorkflows(state) {
  const { runs, doc, error, loading, viewSwitch, openKey } = state;
  if (error) return renderEmpty(false, error, viewSwitch);
  if (loading && !runs.length) return renderEmpty(true, null, viewSwitch);
  if (!runs.length) return renderEmpty(false, null, viewSwitch);
  const items = doc?.items ?? [];
  const open = openKey ? items.find((i) => i.key === openKey) : null;
  return pane("workflows").text_size(TEXT.body)
    .child(open ? renderDetail(open, state) : renderList(items, state));
}
