// Queue pane: one section per remote machine, live counts, rows, Cancel on live entries.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { liveCounts, isLive, stateKind, ageText, shortArgv } from "./_lib/queue.js";
import { friendlyError } from "./_lib/scope.js";
import { SP, LAYOUT } from "./_lib/tokens.js";
import {
  pane, paneBar, scrollBody, sectionLabel, hoverRow, text, type, statusText,
  button, placeholder, errorLine, caption,
} from "./_ui/ui.js";

function countLine(rows) {
  const { queued, running } = liveCounts(rows);
  return `${queued} queued · ${running} running`;
}

function renderRow(on, row, { onCancel, nowMs, t }) {
  const id = `${on}-${row.id}`;
  return hoverRow(`queue-row-${id}`).flex_none().px(SP.sm).py(SP.xs)
    .child(h_flex().gap(SP.md).items_center().min_w(0)
      .child(type(div().flex_none(), "code").text_color(t.muted).child(`#${row.id}`))
      .child(text("meta", row.entry_class ?? ""))
      .child(statusText(stateKind(row.state), row.state ?? "", { aria: row.state ?? "unknown" }))
      .child(text("meta", ageText(row, nowMs)))
      .child(type(div().min_w(0).flex_1().truncate(), "body").child(shortArgv(row.argv)))
      .when(isLive(row.state), (e) => e.child(
        button(`queue-cancel-${id}`, "Cancel", () => onCancel(on, row.id)))));
}

function renderSection(section, opts) {
  const t = opts.t;
  const body = v_flex().gap(SP.xs)
    .child(h_flex().gap(SP.sm).items_center()
      .child(sectionLabel(section.on))
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
  if (!rows.length) {
    return body.child(placeholder("None").px(SP.sm));
  }
  return body.children(rows.map((row) => renderRow(section.on, row, opts)));
}

export function renderQueue({ sections, onCancel, nowMs }) {
  const t = theme();
  const list = sections ?? [];
  const body = scrollBody().p(SP.md).gap(SP.lg);
  if (!list.length) {
    body.child(placeholder("No remote machines").max_w(LAYOUT.msgMaxW));
  } else {
    body.children(list.map((section) => renderSection(section, { onCancel, nowMs, t })));
  }
  return pane("queue")
    .child(paneBar().child(text("title", "Queue")))
    .child(body);
}
