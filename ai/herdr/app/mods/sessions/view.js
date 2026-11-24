// Sessions pane: machines home, then one machine's folders of pi and Claude Code sessions —
// title, harness tag, close-candidate mark, TL;DR, PR tags, state line, and Open / Close / Rename
// (pi only). Draws only.
import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { folderBasename, machineCountsLine } from "./_lib/sessions.js";
import { SP } from "./_lib/tokens.js";
import {
  pane, paneHeader, scrollBody, card, text, type, tag, hoverRow,
  button, statusText, textInput, placeholder, caption,
  machineCard, machineHeader,
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
    .when(!row.live && !renaming && row.harness === "pi", (e) => e.child(button(`sessions-rename-${row.key}`, "Rename", () => props.onRenameStart(row))));
}

function renderRow(row, props) {
  const t = props.t;
  const note = props.noteOf(row.key);
  return card(`sessions-row-${row.key}`, { linked: false }).gap(SP.xs)
    .child(h_flex().gap(SP.sm).items_center().min_w(0)
      .child(type(div().min_w(0).flex_1().truncate(), "body").text_color(t.foreground).child(row.title))
      .child(tag(row.harness))
      .when(row.candidate, (e) => e.child(statusText("warn", "close candidate"))))
    .when(row.tldr !== "", (e) => e.child(text("meta", row.tldr, t.muted)))
    .when(row.prs.length > 0, (e) => e.child(h_flex().gap(SP.xs).children(row.prs.map((p) => tag(p.label)))))
    .child(text("meta", row.state, t.muted))
    .when(props.renaming === row.key, (e) => e.child(renderRename(row, props)))
    .child(renderActions(row, props))
    .when(note != null, (e) => e.child(statusText("fail", note)));
}

function renderHome(props) {
  return scrollBody().p(SP.md).gap(SP.lg)
    .children((props.cards ?? []).map((c) =>
      machineCard(
        `sessions-machine-${c.key || "local"}`,
        { title: c.title, counts: c.counts, status: c.status },
        () => props.onSelectMachine(c.key),
      )));
}

function renderFolder(group, props) {
  const t = props.t;
  const open = props.isOpen(group.key);
  const name = folderBasename(group.key);
  const candidate = (group.rows ?? []).some((r) => r.candidate);
  const el = card(`sessions-folder-${group.key}`, { linked: false }).gap(SP.xs)
    .child(hoverRow(`sessions-folder-toggle-${group.key}`).min_w(0)
      .on_click(() => props.onToggleFolder(group.key))
      .child(v_flex().min_w(0).gap(SP.xxs)
        .child(h_flex().gap(SP.sm).items_center().min_w(0)
          .child(text("meta", open ? "▾" : "▸", t.muted))
          .child(type(div().min_w(0).flex_1().truncate(), "body").text_color(t.foreground).child(name))
          .when(candidate, (e) => e.child(statusText("warn", "close candidate"))))
        .child(h_flex().gap(SP.sm).items_center().min_w(0)
          .when(group.key !== name && group.key !== "", (e) =>
            e.child(type(div().min_w(0).flex_1().truncate(), "meta").text_color(t.muted).child(group.key)))
          .child(text("meta", `${group.rows.length}`, t.muted)))));
  if (open) el.children(group.rows.map((row) => renderRow(row, props)));
  return el;
}

function renderMachinePage(props) {
  const page = props.page;
  const body = v_flex().gap(SP.md);
  body.child(h_flex().items_center().gap(SP.xs)
    .child(button("sessions-back", "‹ Machines", () => props.onBack())));
  body.child(machineHeader({
    title: page.title,
    counts: machineCountsLine(page),
    status: page.status === "error" ? { kind: "fail", word: "error" } : undefined,
  }));
  if (page.status === "loading") {
    return scrollBody().p(SP.md).gap(SP.md).child(body).child(placeholder("Loading…"));
  }
  if (page.status === "error") {
    return scrollBody().p(SP.md).gap(SP.md).child(body).child(statusText("fail", page.error));
  }
  if (!page.groups.length) body.child(placeholder("No sessions in this window"));
  return scrollBody().p(SP.md).gap(SP.md)
    .child(body)
    .children((page.groups ?? []).map((group) => renderFolder(group, props)))
    .child(h_flex().gap(SP.sm).items_center()
      .child(button(`sessions-older-${page.key || "local"}`, "Show older", () => props.onOlder(page.key)))
      .child(caption(`last ${page.since} days`)));
}

export function renderSessions(props) {
  const opts = { ...props, t: theme() };
  const body = props.page == null ? renderHome(opts) : renderMachinePage(opts);
  return pane("sessions")
    .child(paneHeader("Sessions", { spinning: props.spinning, onRefresh: () => props.onRefresh(), id: "sessions-refresh" }))
    .child(body);
}
