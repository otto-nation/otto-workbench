// Queue pane: one section per machine (this one first), live counts, two-line rows. A row expands
// to its command, cwd, timings and log tail; Cancel on a live row asks before it acts.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import {
  liveCounts, isLive, stateKind, ageText, rowTitle, rowMeta, commandText, timesText, LOG_TAIL_LINES,
} from "./_lib/queue.js";
import { tailLines } from "./_lib/output.js";
import { friendlyError } from "./_lib/scope.js";
import { SP, LAYOUT } from "./_lib/tokens.js";
import {
  pane, paneBar, scrollBody, sectionLabel, hoverRow, text, type, statusText, button, iconButton,
  placeholder, errorLine, caption, inset, selectableCode,
} from "./_ui/ui.js";

const LOCAL_LABEL = "This computer";

function countLine(rows) {
  const { queued, running } = liveCounts(rows);
  return `${queued} queued · ${running} running`;
}

const domId = (on, id) => `${on ?? "local"}-${id}`;

function renderActions(on, row, opts) {
  if (!isLive(row.state)) return null;
  const id = domId(on, row.id);
  if (opts.confirming === opts.rowKey(on, row.id)) {
    return h_flex().flex_none().gap(SP.xs).items_center()
      .child(button(`queue-confirm-${id}`, "Confirm cancel", () => opts.onCancel(on, row.id)))
      .child(button(`queue-keep-${id}`, "Keep", () => opts.onDismiss()));
  }
  return h_flex().flex_none()
    .child(button(`queue-cancel-${id}`, "Cancel", () => opts.onAskCancel(on, row.id)));
}

function renderHead(on, row, opts) {
  const { t, nowMs } = opts;
  const open = opts.openKey === opts.rowKey(on, row.id);
  return hoverRow(`queue-row-${domId(on, row.id)}`, { selected: open })
    .flex_1().min_w(0).px(SP.sm).py(SP.xs)
    .on_click(() => opts.onToggle(on, row.id))
    .child(v_flex().min_w(0).gap(SP.xxs)
      .child(h_flex().gap(SP.sm).items_center().min_w(0)
        .child(type(div().min_w(0).flex_1().truncate(), "body").child(rowTitle(row)))
        .child(statusText(stateKind(row.state), row.state ?? "", { aria: row.state ?? "unknown" }))
        .child(text("meta", ageText(row, nowMs), t.muted)))
      .child(type(div().min_w(0).truncate(), "meta").text_color(t.muted).child(rowMeta(row, nowMs))));
}

function renderLog(row, log, id) {
  if (!row.log_path) return placeholder("No log yet");
  if (log == null) return placeholder("Loading log…");
  if (log.error) return errorLine(log.error);
  const lines = tailLines(log.text ?? "", LOG_TAIL_LINES);
  if (!lines.length) return placeholder(isLive(row.state) ? "No output yet" : "No output");
  return type(inset({ scroll: false }).text_color(theme().foreground), "code")
    .child(selectableCode(`queue-log-${id}`, lines, LAYOUT.bodyMaxH));
}

function renderDetail(on, row, opts) {
  const id = domId(on, row.id);
  const facts = [
    row.kind && `kind ${row.kind}`,
    row.pid != null && `pid ${row.pid}`,
    `attempts ${row.attempts ?? 0}`,
    row.exit_status != null && `exit ${row.exit_status}`,
  ].filter(Boolean).join(" · ");
  const times = timesText(row);
  return v_flex().gap(SP.xs).pl(SP.md).pr(SP.sm).pb(SP.sm)
    .child(type(inset({ scroll: false }).text_color(theme().foreground), "code")
      .child(selectableCode(`queue-cmd-${id}`, [commandText(row.argv)], LAYOUT.titleMaxH * 3)))
    .when(Boolean(row.cwd), (e) => e.child(caption(`cwd ${row.cwd}`)))
    .child(caption(facts))
    .when(Boolean(times), (e) => e.child(caption(times)))
    .child(renderLog(row, opts.log, id));
}

function renderRow(on, row, opts) {
  const line = h_flex().gap(SP.sm).items_center().min_w(0)
    .child(renderHead(on, row, opts));
  const actions = renderActions(on, row, opts);
  if (actions) line.child(actions);
  const open = opts.openKey === opts.rowKey(on, row.id);
  return open ? [line, renderDetail(on, row, opts)] : [line];
}

function renderSection(section, opts) {
  const label = section.on ?? LOCAL_LABEL;
  const body = v_flex().gap(SP.xs)
    .child(h_flex().gap(SP.sm).items_center()
      .child(sectionLabel(label))
      .when(section.result?.ok && !section.result.busy, (e) => e.child(
        caption(countLine(section.result.rows)))));
  if (section.result == null) {
    return body.child(placeholder("Loading…").px(SP.sm));
  }
  if (!section.result.ok) {
    return body.child(errorLine(friendlyError(section.result.error, section.on)).px(SP.sm));
  }
  if (section.result.busy) {
    return body.child(caption("busy").px(SP.sm));
  }
  const rows = section.result.rows ?? [];
  if (section.cancelError != null) {
    const err = section.cancelError;
    body.child(errorLine(typeof err === "string" ? err : friendlyError(err, section.on)).px(SP.sm));
  }
  if (!rows.length) {
    return body.child(placeholder("None").px(SP.sm));
  }
  return body.children(rows.flatMap((row) => renderRow(section.on, row, opts)));
}

export function renderQueue(props) {
  const t = theme();
  const opts = { ...props, t };
  const body = scrollBody().p(SP.md).gap(SP.lg)
    .children((props.sections ?? []).map((section) => renderSection(section, opts)));
  return pane("queue")
    .child(paneBar().child(h_flex().items_center().gap(SP.sm)
      .child(div().flex_1().child(text("title", "Queue")))
      .child(iconButton("queue-refresh", "mods/queue/refresh.svg",
        props.manual ? "Refreshing…" : "Refresh", () => props.onRefresh(),
        { disabled: props.manual, spinning: props.manual }))))
    .child(body);
}
