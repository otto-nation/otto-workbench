// Queue pane: one section per machine, Running / Waiting / Finished lanes, All/Live/Failed filter.
// A row expands to detailModel (command, facts, timing, log); Cancel on a live row asks before it acts.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { isLive, stateKind, timesText, detailModel, LOG_TAIL_LINES, rowTitle } from "./_lib/queue.js";
import { tailLines } from "./_lib/output.js";
import { friendlyError } from "./_lib/scope.js";
import { SP, RADIUS, FILL, LAYOUT } from "./_lib/tokens.js";
import {
  pane, paneBar, scrollBody, sectionLabel, laneTitle, hoverRow, text, type, statusText, button,
  iconButton, placeholder, errorLine, caption, scrollInset, selectableCode, keyValue, segment,
  segmented,
} from "./_ui/ui.js";

function headline(row) {
  const rest = row.subject || row.label;
  if (row.action && rest) return `${row.action} ${rest}`;
  return rest || row.action || rowTitle(row);
}

function metaLine(row) {
  return [`#${row.id}`, row.entry_class, row.position, row.retry, row.failure].filter(Boolean).join(" · ");
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

function renderHead(on, row, opts, { group = false, member = false } = {}) {
  const { t } = opts;
  const ink = opts.muted ? t.muted : t.foreground;
  const open = !group && opts.openKey === opts.rowKey(on, row.id);
  const abs = timesText(row);
  const id = group ? `queue-group-${domId(on, row.id)}` : `queue-row-${domId(on, row.id)}`;
  const click = group
    ? () => opts.onToggleGroup(on, row.id)
    : () => opts.onToggle(on, row.id);
  return hoverRow(id, { selected: open })
    .flex_1().min_w(0).px(SP.sm).py(SP.xs)
    .when(member, (e) => e.pl(SP.md))
    .when(Boolean(abs), (e) => e.tooltip(abs))
    .on_click(click)
    .child(v_flex().min_w(0).gap(SP.xxs)
      .child(h_flex().gap(SP.sm).items_center().min_w(0)
        .child(statusText(stateKind(row.state), null, { aria: row.state ?? "unknown" }))
        .child(type(div().min_w(0).flex_1().truncate(), "body").text_color(ink).child(headline(row)))
        .when(Boolean(row.timing), (e) => e.child(text("meta", row.timing, t.muted))))
      .child(type(div().min_w(0).truncate(), "meta").text_color(t.muted).child(metaLine(row))));
}

function renderLog(row, log, id) {
  if (log == null) return placeholder("Loading log…");
  if (log.error) return errorLine(log.error);
  const lines = tailLines(log.text ?? "", LOG_TAIL_LINES);
  if (!lines.length) return placeholder(isLive(row.state) ? "No output yet" : "No output");
  return type(scrollInset(`queue-log-box-${id}`, selectableCode(`queue-log-${id}`, lines))
    .text_color(theme().foreground), "code");
}

function renderDetail(on, row, opts) {
  const id = domId(on, row.id);
  const model = detailModel(row, opts.nowMs);
  const timing = caption(model.timing);
  if (model.tooltip) timing.tooltip(model.tooltip);
  return v_flex().gap(SP.xs).pl(SP.md).pr(SP.sm).pb(SP.sm)
    .child(type(scrollInset(`queue-cmd-box-${id}`, selectableCode(`queue-cmd-${id}`, [model.command]),
      { maxH: LAYOUT.titleMaxH * 3 }).text_color(theme().foreground), "code"))
    .when(model.facts.length > 0, (e) => e.child(keyValue(model.facts)))
    .when(Boolean(model.timing), (e) => e.child(timing))
    .child(model.logNote != null ? caption(model.logNote) : renderLog(row, opts.log, id));
}

function renderOne(on, row, opts, extra = {}) {
  const line = h_flex().gap(SP.sm).items_center().min_w(0)
    .child(renderHead(on, row, opts, extra));
  const actions = extra.group ? null : renderActions(on, row, opts);
  if (actions) line.child(actions);
  const open = !extra.group && opts.openKey === opts.rowKey(on, row.id);
  return open ? [line, renderDetail(on, row, opts)] : [line];
}

function renderLaneRow(on, row, opts) {
  if (!row.members) return renderOne(on, row, opts);
  const open = opts.openGroups.has(opts.rowKey(on, row.id));
  const out = renderOne(on, row, opts, { group: true });
  if (!open) return out;
  return out.concat(row.members.flatMap((m) => renderOne(on, m, opts, { member: true })));
}

function renderLane(section, lane, opts) {
  const t = opts.t;
  return v_flex().id(`queue-lane-${section.on ?? "local"}-${lane.key}`)
    .rounded(RADIUS.card).bg(`${t.raised}${FILL.lane}`)
    .child(laneTitle(lane.label, lane.count))
    .child(v_flex().px(SP.sm).pb(SP.sm).gap(SP.xs)
      .when(!lane.rows.length, (e) => e.child(placeholder("None").px(SP.md)))
      .children(lane.rows.flatMap((row) => renderLaneRow(section.on, row, opts))));
}

function staleLine(stale) {
  if (stale == null) return null;
  return stale.warn ? statusText("warn", stale.words) : caption(stale.words);
}

function renderSection(section, opts) {
  const stale = staleLine(section.stale);
  const body = v_flex().gap(SP.xs)
    .child(h_flex().gap(SP.sm).items_center()
      .child(sectionLabel(section.label))
      .when(stale != null, (e) => e.child(stale)));
  if (section.loading) {
    return body.child(placeholder("Loading…").px(SP.sm));
  }
  if (section.busy) {
    return body.child(caption("busy").px(SP.sm));
  }
  if (section.cancelError != null) {
    const err = section.cancelError;
    body.child(errorLine(typeof err === "string" ? err : friendlyError(err, section.on)).px(SP.sm));
  }
  const laneOpts = { ...opts, muted: section.muted };
  return body.children((section.lanes ?? []).map((lane) => renderLane(section, lane, laneOpts)));
}

export function renderQueue(props) {
  const t = theme();
  const opts = { ...props, t, openGroups: props.openGroups ?? new Set() };
  const filter = props.filter ?? "all";
  const body = scrollBody().p(SP.md).gap(SP.lg)
    .children((props.sections ?? []).map((section) => renderSection(section, opts)));
  return pane("queue")
    .child(paneBar()
      .child(h_flex().items_center().gap(SP.sm)
        .child(div().flex_1().child(text("title", "Queue")))
        .child(iconButton("queue-refresh", "mods/queue/refresh.svg",
          props.spinning ? "Refreshing…" : "Refresh", () => props.onRefresh(),
          { spinning: props.spinning })))
      .child(h_flex().items_center().gap(SP.sm)
        .child(segmented([
          segment("queue-filter-all", "All", filter === "all", () => props.onFilter("all")),
          segment("queue-filter-live", "Live", filter === "live", () => props.onFilter("live")),
          segment("queue-filter-failed", "Failed", filter === "failed", () => props.onFilter("failed")),
        ]))
        .child(caption(props.summary))))
    .child(body);
}
