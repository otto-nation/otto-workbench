// Hosts pane: machines home (one card per machine) then a page of processes and details.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { SP } from "./_lib/tokens.js";
import {
  pane, scrollBody, laneTitle, text, type, button,
  placeholder, segmented, segment, keyValue, card,
  machineCard, machineHeader, paneHeader,
} from "./_ui/ui.js";

function renderHome(props) {
  return scrollBody().p(SP.md).gap(SP.lg)
    .children((props.cards ?? []).map((c) =>
      machineCard(`hosts-machine-${c.key || "local"}`, c.model, () => props.onSelectMachine(c.key))));
}

function renderProcesses(page, props) {
  const t = props.t;
  const by = props.by;
  const hostKey = props.key || "local";
  const el = card("hosts-processes", { linked: false }).gap(SP.xs)
    .child(laneTitle("Processes", null, segmented([
      segment("hosts-by-cpu", "CPU", by === "cpu", () => props.onBy("cpu")),
      segment("hosts-by-mem", "Memory", by === "mem", () => props.onBy("mem")),
    ])));
  const rows = page.processes ?? [];
  if (!rows.length) return el.child(placeholder("No process data"));
  return el.children(rows.map((row) =>
    v_flex().id(`hosts-proc-${hostKey}-${row.key}`).px(SP.sm).py(SP.xs)
      .child(h_flex().gap(SP.sm).items_center().min_w(0)
        .child(type(div().min_w(0).flex_1().truncate(), "body").text_color(t.foreground).child(row.command))
        .child(text("meta", row.right, t.muted)))
      .child(type(div().min_w(0).truncate(), "meta").text_color(t.muted).child(row.meta))));
}

function renderDetails(page) {
  return card("hosts-details", { linked: false }).gap(SP.xs)
    .child(laneTitle("Details"))
    .child(keyValue(page.details));
}

function renderMachine(props) {
  const page = props.page;
  const body = v_flex().gap(SP.md);
  body.child(h_flex().items_center().gap(SP.xs)
    .child(button("hosts-back", "‹ Machines", () => props.onBack())));
  body.child(machineHeader(page.model));
  if (page.health == null) {
    if (page.model.status.word === "loading") body.child(placeholder("Loading…"));
    return scrollBody().p(SP.md).gap(SP.md).child(body);
  }
  return scrollBody().p(SP.md).gap(SP.md)
    .child(body)
    .child(renderProcesses(page, props))
    .when(page.details.length > 0, (e) => e.child(renderDetails(page)));
}

export function renderHosts(props) {
  const t = theme();
  const opts = { ...props, t };
  const body = props.page == null ? renderHome(opts) : renderMachine(opts);
  return pane("hosts")
    .child(paneHeader("Hosts", { spinning: props.spinning, onRefresh: () => props.onRefresh(), id: "hosts-refresh" }))
    .child(body);
}
