// Hosts pane: one machine block (status, meters, collapse) then that host's top processes.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { topProcesses } from "./_lib/hosts.js";
import { SP } from "./_lib/tokens.js";
import {
  pane, paneBar, scrollBody, sectionLabel, hoverRow, text, type, statusText,
  iconButton, placeholder, caption, meter, segment, segmented,
} from "./_ui/ui.js";

// A figure past its threshold carries a status glyph beside the meter, so colour is never the only cue.
function renderMeter(g) {
  const row = h_flex().flex_none().gap(SP.xs).items_center().child(meter(g.meter, { label: g.label }));
  if (g.kind === "warn" || g.kind === "fail") row.child(statusText(g.kind, null, { aria: `${g.label} ${g.kind}` }));
  return row;
}

function staleLine(stale) {
  if (stale == null) return null;
  const kind = stale.kind;
  if (kind !== "fresh" && kind !== "loading") return statusText("warn", stale.words);
  return caption(stale.words);
}

function renderProcs(block, props) {
  if (block.health == null) {
    if (block.summary.staleness?.kind !== "loading") return null;
    return placeholder("Loading…").px(SP.sm);
  }
  const rows = topProcesses(block.health, props.by);
  if (!rows.length) return placeholder("No process data");
  const t = props.t;
  const host = block.key || "local";
  return v_flex().children(rows.map((row) =>
    v_flex().id(`hosts-proc-${host}-${row.key}`).px(SP.sm).py(SP.xs)
      .child(h_flex().gap(SP.sm).items_center().min_w(0)
        .child(type(div().min_w(0).truncate(), "body").text_color(t.foreground).child(row.command))
        .child(text("meta", row.right, t.muted)))
      .child(type(div().min_w(0).truncate(), "meta").text_color(t.muted).child(row.meta))));
}

function renderBlock(block, props) {
  const t = props.t;
  const summary = block.summary;
  const stale = staleLine(summary.staleness);
  const header = hoverRow(`hosts-machine-${block.key || "local"}`)
    .px(SP.sm).py(SP.xs)
    .on_click(() => props.onToggleMachine(block.key, block.open))
    .child(v_flex().gap(SP.xxs).min_w(0)
      .child(h_flex().gap(SP.sm).items_center()
        .child(sectionLabel(summary.title))
        .child(statusText(summary.status.kind, summary.status.word))
        .when(stale != null, (e) => e.child(stale))
        .child(div().flex_1())
        .child(text("meta", block.open ? "▾" : "▸", t.muted)))
      .child(h_flex().gap(SP.sm).items_center().flex_wrap()
        .children((summary.gauges ?? []).map(renderMeter)))
      .when(Boolean(summary.counts), (e) => e.child(caption(summary.counts)))
      .when(summary.note != null, (e) => e.child(caption(summary.note))));
  const body = v_flex().gap(SP.xs).child(header);
  if (!block.open) return body;
  const procs = renderProcs(block, props);
  return procs == null ? body : body.child(procs);
}

export function renderHosts(props) {
  const t = theme();
  const opts = { ...props, t };
  const by = props.by ?? "cpu";
  const body = scrollBody().p(SP.md).gap(SP.lg)
    .children((props.blocks ?? []).map((block) => renderBlock(block, opts)));
  return pane("hosts")
    .child(paneBar()
      .child(h_flex().items_center().gap(SP.sm)
        .child(div().flex_1().child(text("title", "Hosts")))
        .child(iconButton("hosts-refresh", "mods/queue/refresh.svg",
          props.spinning ? "Refreshing…" : "Refresh", () => props.onRefresh(),
          { spinning: props.spinning })))
      .child(h_flex().items_center().gap(SP.sm)
        .child(segmented([
          segment("hosts-by-cpu", "CPU", by === "cpu", () => props.onBy("cpu")),
          segment("hosts-by-mem", "Memory", by === "mem", () => props.onBy("mem")),
        ]))))
    .child(body);
}
