// Queue pane: machines home (one card per machine) then a page of lanes per machine.
// A row expands to detailModel (command, facts, timing, log); Cancel on a live row asks before it acts.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { isLive, stateKind, timesText, detailModel, LOG_TAIL_LINES, rowTitle } from "./_lib/queue.js";
import { tailLines } from "./_lib/output.js";
import { friendlyError } from "./_lib/scope.js";
import { SP, LAYOUT } from "./_lib/tokens.js";
import {
  pane, paneBar, scrollBody, sectionLabel, laneTitle, hoverRow, text, type, statusText, button,
  dangerButton, iconButton, placeholder, errorLine, caption, meter, scrollInset, selectableCode,
  keyValue, card,
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

function isCoalesced(row) {
  return row?.subjects != null && Array.isArray(row.members);
}

function groupHeadline(row) {
  if (row.failure) return [row.action, row.failure].filter(Boolean).join(" · ");
  const bits = [row.action];
  if (row.count != null) bits.push(`${row.count} waiting`);
  if (row.line) bits.push(row.line);
  return bits.filter(Boolean).join(" · ");
}

function subjectsPreview(subjects) {
  if (subjects == null) return "";
  const bits = [...(subjects.names ?? [])];
  if (subjects.more) bits.push(subjects.more);
  return bits.join(", ");
}

function renderActions(on, row, opts) {
  if (!isLive(row.state)) return null;
  const id = domId(on, idOf(row));
  if (opts.confirming === opts.rowKey(on, row.id)) {
    return h_flex().flex_none().gap(SP.sm).items_center()
      .child(caption("Stops this job"))
      .child(dangerButton(`queue-confirm-${id}`, "Confirm cancel", () => opts.onCancel(on, row.id)))
      .child(button(`queue-keep-${id}`, "Keep", () => opts.onDismiss()));
  }
  return h_flex().flex_none()
    .child(button(`queue-cancel-${id}`, "Cancel", () => opts.onAskCancel(on, row.id)));
}

function idOf(row) {
  return row.id ?? row.lead?.id ?? row.key;
}

function renderHead(on, row, opts, { group = false, member = false } = {}) {
  const { t } = opts;
  const ink = opts.muted ? t.muted : t.foreground;
  const open = !group && opts.openKey === opts.rowKey(on, row.id);
  const abs = timesText(row);
  const id = group ? `queue-group-${domId(on, idOf(row))}` : `queue-row-${domId(on, row.id)}`;
  const click = group
    ? () => opts.onToggleGroup(on, row)
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

function renderGroupHead(on, row, opts) {
  const { t } = opts;
  const ink = opts.muted ? t.muted : t.foreground;
  const open = Boolean(opts.openGroups?.[opts.groupKey(on, row)]);
  const lead = row.lead ?? row.members?.[0];
  const state = lead?.state ?? row.state;
  const preview = subjectsPreview(row.subjects);
  return hoverRow(`queue-group-${domId(on, row.key)}`)
    .flex_1().min_w(0).px(SP.sm).py(SP.xs)
    .on_click(() => opts.onToggleGroup(on, row))
    .child(v_flex().min_w(0).gap(SP.xxs)
      .child(h_flex().gap(SP.sm).items_center().min_w(0)
        .child(text("meta", open ? "▾" : "▸", t.muted))
        .when(Boolean(state), (e) => e.child(statusText(stateKind(state), null, { aria: state ?? "unknown" })))
        .child(type(div().min_w(0).flex_1().truncate(), "body").text_color(ink).child(groupHeadline(row)))
        .when(Boolean(row.timing), (e) => e.child(text("meta", row.timing, t.muted))))
      .when(Boolean(preview), (e) => e.child(type(div().min_w(0).truncate(), "meta").text_color(t.muted).child(preview))));
}

function renderLog(row, log, id) {
  if (log == null) return placeholder("Loading log…");
  if (log.error) return errorLine(log.error);
  const lines = tailLines(log.text ?? "", LOG_TAIL_LINES);
  if (!lines.length) return placeholder(isLive(row.state) ? "No output yet" : "No output");
  return type(scrollInset(`queue-log-box-${id}`, selectableCode(`queue-log-${id}`, lines))
    .text_color(theme().foreground), "code");
}

function renderDetailActions(on, row, opts) {
  const id = domId(on, row.id);
  const pr = row.subject
    ? button(`queue-open-pr-${id}`, "Open PR", () => opts.onOpenPr(row))
    : null;
  const term = row.terminal
    ? button(`queue-open-term-${id}`, "Open terminal", () => opts.onOpenTerminal(row.terminal))
    : null;
  if (!pr && !term) return null;
  return h_flex().gap(SP.xs).flex_wrap().items_center()
    .when(pr != null, (e) => e.child(pr))
    .when(term != null, (e) => e.child(term));
}

function renderDetail(on, row, opts) {
  const id = domId(on, row.id);
  const model = detailModel(row, opts.nowMs);
  const timing = caption(model.timing);
  if (model.tooltip) timing.tooltip(model.tooltip);
  const actions = renderDetailActions(on, row, opts);
  return v_flex().gap(SP.xs).pl(SP.md).pr(SP.sm).pb(SP.sm)
    .child(type(scrollInset(`queue-cmd-box-${id}`, selectableCode(`queue-cmd-${id}`, [model.command]),
      { maxH: LAYOUT.titleMaxH * 3 }).text_color(theme().foreground), "code"))
    .when(model.facts.length > 0, (e) => e.child(keyValue(model.facts)))
    .when(Boolean(model.timing), (e) => e.child(timing))
    .when(actions != null, (e) => e.child(actions))
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
  if (isCoalesced(row)) {
    const open = Boolean(opts.openGroups?.[opts.groupKey(on, row)]);
    const out = [h_flex().gap(SP.sm).items_center().min_w(0).child(renderGroupHead(on, row, opts))];
    if (!open) return out;
    return out.concat(row.members.flatMap((m) => renderOne(on, m, opts, { member: true })));
  }
  const open = Boolean(opts.openGroups?.[opts.groupKey(on, row)]);
  const out = renderOne(on, row, opts, { group: true });
  if (!open) return out;
  return out.concat(row.members.flatMap((m) => renderOne(on, m, opts, { member: true })));
}

function staleLine(stale) {
  if (stale == null) return null;
  return stale.warn ? statusText("warn", stale.words) : caption(stale.words);
}

function hideMeters(kind) {
  return kind === "outdated" || kind === "notInstalled";
}

function renderMeters(gauges, { muted = false } = {}) {
  return h_flex().gap(SP.sm).items_center().flex_wrap()
    .children((gauges ?? []).map((g) => meter(g, { muted, label: g.class })));
}

function renderHomeCard(block, opts) {
  const t = opts.t;
  const kind = block.staleness?.kind;
  const stale = staleLine(block.staleness && {
    words: block.staleness.words,
    warn: kind != null && kind !== "fresh",
  });
  const muted = kind === "stale" || kind === "unreachable";
  const hide = hideMeters(kind);
  return card(`queue-machine-${block.key || "local"}`)
    .gap(SP.xs)
    .on_click(() => opts.onSelectMachine(block.key))
    .child(h_flex().gap(SP.sm).items_center()
      .child(sectionLabel(block.title))
      .when(stale != null, (e) => e.child(stale)))
    .when(!hide, (e) => e.child(renderMeters(block.gauges, { muted })))
    .when(!hide, (e) => e.child(caption(block.counts, muted ? t.muted : undefined)))
    .when(block.hostNote != null, (e) => e.child(statusText("warn", block.hostNote)));
}

function renderMore(lane, opts) {
  if (!lane.more) return null;
  const waiting = lane.key === "waiting";
  const id = waiting ? "queue-show-more" : "queue-show-older";
  const label = waiting ? "Show more" : "Show older";
  const click = waiting ? opts.onShowMore : opts.onShowOlder;
  return h_flex().gap(SP.sm).items_center().px(SP.sm)
    .child(button(id, label, () => click()))
    .child(caption(`${lane.more} more`));
}

function renderLane(on, lane, opts) {
  if (!lane.card) {
    return sectionLabel(lane.label, lane.count);
  }
  return card(`queue-lane-${on ?? "local"}-${lane.key}`, { linked: false })
    .gap(SP.xs)
    .child(laneTitle(lane.label, lane.count))
    .child(v_flex().gap(SP.xs)
      .children(lane.rows.flatMap((row) => renderLaneRow(on, row, opts)))
      .when(lane.more > 0, (e) => e.child(renderMore(lane, opts))));
}

function renderHome(opts) {
  return scrollBody().p(SP.md).gap(SP.lg)
    .children((opts.cards ?? []).map((block) => renderHomeCard(block, opts)));
}

function renderMachine(opts) {
  const page = opts.page;
  const summary = page.summary;
  const kind = summary.staleness?.kind;
  const stale = staleLine(summary.staleness && {
    words: summary.staleness.words,
    warn: kind != null && kind !== "fresh",
  });
  const muted = opts.muted;
  const hide = hideMeters(kind);
  const t = opts.t;
  const body = v_flex().gap(SP.md);
  body.child(h_flex().items_center().gap(SP.xs)
    .child(button("queue-back", "‹ Machines", () => opts.onBack())));
  body.child(v_flex().gap(SP.xxs)
    .child(h_flex().gap(SP.sm).items_center()
      .child(text("title", summary.title))
      .when(stale != null, (e) => e.child(stale)))
    .when(!hide, (e) => e.child(renderMeters(summary.gauges, { muted })))
    .when(!hide, (e) => e.child(caption(summary.counts, muted ? t.muted : undefined)))
    .when(summary.hostNote != null, (e) => e.child(statusText("warn", summary.hostNote))));
  if (opts.loading) return scrollBody().p(SP.md).gap(SP.md).child(body).child(placeholder("Loading…"));
  if (opts.busy) return scrollBody().p(SP.md).gap(SP.md).child(body).child(caption("busy"));
  if (opts.cancelError != null) {
    const err = opts.cancelError;
    body.child(errorLine(typeof err === "string" ? err : friendlyError(err, opts.on)));
  }
  return scrollBody().p(SP.md).gap(SP.md)
    .child(body)
    .children((page.lanes ?? []).map((lane) => renderLane(opts.on, lane, opts)));
}

export function renderQueue(props) {
  const t = theme();
  const opts = { ...props, t, openGroups: props.openGroups ?? {} };
  const body = opts.page == null ? renderHome(opts) : renderMachine(opts);
  return pane("queue")
    .child(paneBar()
      .child(h_flex().items_center().gap(SP.sm)
        .child(div().flex_1().child(text("title", "Queue")))
        .child(iconButton("queue-refresh", "mods/queue/refresh.svg",
          props.spinning ? "Refreshing…" : "Refresh", () => props.onRefresh(),
          { spinning: props.spinning }))))
    .child(body);
}
