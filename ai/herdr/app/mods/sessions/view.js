// Sessions pane: per machine, pi sessions grouped by worktree — title, close-candidate mark,
// TL;DR, PR tags, state line, and Open / Close / Rename. Draws only; main.js decides.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { SP } from "./_lib/tokens.js";
import {
  pane, paneHeader, scrollBody, laneTitle, sectionLabel, card, text, type, tag,
  button, statusText, textInput, placeholder, caption,
} from "./_ui/ui.js";

function renderRename(row, props) {
  return h_flex().gap(SP.xs).items_center()
    .child(div().flex_1().min_w(0).child(textInput(props.renameInput)))
    .child(button(`sessions-rename-save-${row.key}`, "Save", () => props.onRenameSave(row)))
    .child(button(`sessions-rename-cancel-${row.key}`, "Cancel", () => props.onRenameCancel()));
}

function renderActions(row, props) {
  const renaming = props.renaming === row.key;
  return h_flex().gap(SP.xs).items_center()
    .child(button(`sessions-open-${row.key}`, "Open", () => props.onOpen(row)))
    .when(row.live, (e) => e.child(button(`sessions-close-${row.key}`, "Close", () => props.onClose(row))))
    .when(!row.live && !renaming, (e) => e.child(button(`sessions-rename-${row.key}`, "Rename", () => props.onRenameStart(row))));
}

function renderRow(row, props) {
  const t = props.t;
  const note = props.noteOf(row.key);
  return card(`sessions-row-${row.key}`, { linked: false }).gap(SP.xs)
    .child(h_flex().gap(SP.sm).items_center().min_w(0)
      .child(type(div().min_w(0).flex_1().truncate(), "body").text_color(t.foreground).child(row.title))
      .when(row.candidate, (e) => e.child(statusText("warn", "close candidate"))))
    .when(row.tldr !== "", (e) => e.child(text("meta", row.tldr, t.muted)))
    .when(row.prs.length > 0, (e) => e.child(h_flex().gap(SP.xs).children(row.prs.map((p) => tag(p.label)))))
    .child(text("meta", row.state, t.muted))
    .when(props.renaming === row.key, (e) => e.child(renderRename(row, props)))
    .child(renderActions(row, props))
    .when(note != null, (e) => e.child(statusText("fail", note)));
}

function renderMachine(machine, props) {
  const body = v_flex().gap(SP.sm).child(laneTitle(machine.title));
  if (machine.status === "loading") return body.child(placeholder("Loading…"));
  if (machine.status === "error") return body.child(statusText("fail", machine.error));
  if (!machine.groups.length) body.child(placeholder("No pi sessions in this window"));
  for (const group of machine.groups) {
    body.child(sectionLabel(group.label));
    body.children(group.rows.map((row) => renderRow(row, props)));
  }
  return body.child(h_flex().gap(SP.sm).items_center()
    .child(button(`sessions-older-${machine.key || "local"}`, "Show older", () => props.onOlder(machine.key)))
    .child(caption(`last ${machine.since} days`)));
}

export function renderSessions(props) {
  const opts = { ...props, t: theme() };
  return pane("sessions")
    .child(paneHeader("Sessions", { spinning: props.spinning, onRefresh: () => props.onRefresh(), id: "sessions-refresh" }))
    .child(scrollBody().p(SP.md).gap(SP.lg)
      .children((props.machines ?? []).map((machine) => renderMachine(machine, opts))));
}
