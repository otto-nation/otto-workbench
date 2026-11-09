// Read-only pr-batch grid: header, item rows, and an optional log pane.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { STEP_ORDER, itemTone, stepTone, attentionCount } from "./_lib/batch.js";
import { SP, TEXT, LAYOUT, ROW_H } from "./_lib/tokens.js";
import {
  pill, inset, hoverRow, placeholder, caption, link, emptyPane, paneBar,
  scrollBody, segment, segmented, pane, mono,
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

function runStamp(id) {
  const i = String(id).indexOf("-");
  return i === -1 ? String(id) : String(id).slice(0, i);
}

function renderHeader({ on, runs, runId, doc, onPickRun }) {
  const t = theme();
  const selected = runId ?? runs[0]?.id;
  const picked = runs.find((r) => r.id === selected);
  const status = doc?.run?.status ?? picked?.status;
  const active = Boolean(doc?.run?.active ?? picked?.active);
  const n = doc?.items?.length ?? picked?.items ?? 0;
  const need = attentionCount(doc);
  const trail = doc?.run?.trailRoot;
  const segs = runs.slice(0, 6).map((r) => {
    const sel = r.id === selected;
    return segment(`run-${r.id}`, `${sel ? "● " : ""}${runStamp(r.id)}`, sel, () => onPickRun(r.id));
  });
  return h_flex().id("batch-header").min_h(LAYOUT.headerH).flex_none().flex_wrap()
    .px(SP.lg).py(SP.sm).gap(SP.md).items_center()
    .child(div().flex_none().text_size(TEXT.small).child(on ?? "This computer"))
    .when(segs.length > 0, (e) => e.child(segmented(segs)))
    .when(Boolean(status), (e) => e.child(pill(String(status))))
    .when(active, (e) => e.child(pill("active")))
    .child(caption(`${n} items · ${need} need attention`))
    .when(Boolean(trail), (e) => e.child(caption(`otto-log show ${trail}`)));
}

function stepCell(item, step, cell, onCell) {
  const t = theme();
  const rec = item.steps?.[step] ?? null;
  const id = `step-${item.key}-${step}`;
  if (rec == null) {
    return hoverRow(id).flex_none().px(SP.xs).min_h(ROW_H.diff).flex().items_center()
      .child(div().text_size(TEXT.caption).text_color(t.dim).child("—"));
  }
  const selected = cell?.key === item.key && cell?.step === step;
  return hoverRow(id).flex_none().px(SP.xs).min_h(ROW_H.diff).flex().items_center()
    .when(selected, (e) => e.bg(t.selected))
    .on_click(() => onCell(item.key, step))
    .child(div().text_size(TEXT.caption).text_color(stepColor(t, rec.status))
      .child(rec.status ?? "—"));
}

function renderRow(item, cell, onCell) {
  const t = theme();
  const findings = item.findings?.length ?? 0;
  return h_flex().id(`row-${item.key}`).flex_none().min_h(ROW_H.sidebarTitleOnly)
    .px(SP.md).gap(SP.sm).items_center()
    .child(div().min_w(0).flex_1().truncate().text_size(TEXT.small).child(item.key ?? ""))
    .child(pill(String(item.status ?? ""), itemColor(t, item.status)))
    .children(STEP_ORDER.map((step) => stepCell(item, step, cell, onCell)))
    .when(Boolean(item.waitReason), (e) => e.child(caption(item.waitReason)))
    .when(item.headMoved, (e) => e.child(caption("head moved", t.ansi3)))
    .when(findings > 0, (e) => e.child(caption(`${findings} findings`)));
}

function renderLog(cell, log, onClose) {
  const bar = paneBar()
    .child(h_flex().items_center().justify_between()
      .child(div().text_size(TEXT.small).child(`${cell.key} · ${cell.step}`))
      .child(link("log-close", "Close", onClose)));
  if (log == null) {
    return v_flex().flex_none().child(bar).child(placeholder("Loading log…").px(SP.lg).pb(SP.md));
  }
  if (log.error) {
    return v_flex().flex_none().child(bar).child(placeholder(log.error).px(SP.lg).pb(SP.md));
  }
  const lines = String(log.text ?? "").split("\n");
  return v_flex().flex_none().child(bar)
    .child(div().px(SP.lg).pb(SP.md)
      .child(inset().text_size(TEXT.caption).font_family(mono())
        .children(lines.map((l) => div().child(l === "" ? " " : l)))));
}

function renderEmpty(loading, error) {
  if (loading) return emptyPane().child(placeholder("Loading runs…"));
  if (error) return emptyPane().child(placeholder(error.message ?? String(error)));
  return emptyPane().child(placeholder("No batch runs on this machine"));
}

export function renderPrBatch(state) {
  const { on, runs, runId, doc, error, loading, cell, log } = state;
  if (error) return renderEmpty(false, error);
  if (loading && !runs.length) return renderEmpty(true, null);
  if (!runs.length) return renderEmpty(false, null);
  return pane("workflows").text_size(TEXT.body)
    .child(renderHeader({ on, runs, runId, doc, onPickRun: state.onPickRun }))
    .child(scrollBody().px(SP.sm).py(SP.sm).gap(SP.xxs)
      .children((doc?.items ?? []).map((item) => renderRow(item, cell, state.onCell))))
    .when(Boolean(cell), (e) => e.child(renderLog(cell, log, state.onCloseLog)));
}
