import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { teamColumn, mineColumn, TEAM_COLUMNS, MINE_COLUMNS, filterPrs, isMine, groupColumns } from "./_lib/columns.js";
import { cardId } from "./_lib/scope.js";
import { SP, TEXT, RADIUS } from "./_lib/tokens.js";
import { segment, eyebrow } from "./ui.js";

const CI = { passing: "✓", failing: "✗", pending: "…" };
const COL_MIN = 260;
const COL_EMPTY = 140;
const TITLE_LH = 16;

function ciColor(status) {
  const t = theme();
  if (status === "passing") return t.ansi2;
  if (status === "failing") return t.ansi1;
  return t.dim;
}

function pill(text, color) {
  const t = theme();
  return div().px(SP.sm).py(1).rounded(RADIUS.control).border_1().border_color(t.border)
    .text_size(TEXT.caption).text_color(color ?? t.muted).child(text);
}

function column(col, opts) {
  const t = theme();
  const empty = !col.items.length;
  return v_flex().id(`col-${col.key}`).h_full().min_h(0)
    .when(!empty, (e) => e.flex_1().min_w(COL_MIN))
    .when(empty, (e) => e.flex_none().min_w(COL_EMPTY).max_w(COL_EMPTY).overflow_x_hidden())
    .child(eyebrow(col.label, col.items.length))
    .child(v_flex().flex_1().min_h(0).overflow_y_scrollbar().px(SP.sm).pb(SP.sm).gap(SP.sm)
      .when(empty, (e) => e.child(div().px(SP.md).text_size(TEXT.caption).text_color(t.dim).child("none")))
      .children(col.items.map((pr) => renderCard(pr, opts))));
}

export function renderBoard({ tab, prs, filters, me, onOpen, spinner, showRepo }) {
  const t = theme();
  const mine = tab === "mine";
  const list = mine
    ? prs.filter((p) => isMine(p, me))
    : filterPrs(prs, filters, me);
  const spec = mine ? MINE_COLUMNS : TEAM_COLUMNS;
  const cols = groupColumns(list, mine ? mineColumn : teamColumn, spec);
  return h_flex().id("prs-board").flex_1().h_full().min_h(0).min_w(0).gap(SP.sm).px(SP.lg).py(SP.sm)
    .bg(t.background).text_color(t.foreground).text_size(TEXT.body).overflow_x_scrollbar()
    .children(cols.map((c) => column(c, { onOpen, spinner, showRepo })));
}

function badges(pr) {
  const t = theme();
  const out = [];
  if (pr.isDraft) out.push(pill("draft"));
  if (pr.state === "MERGED") out.push(pill("merged"));
  if (pr.inMergeQueue) out.push(pill("queue"));
  if (pr.busy) out.push(pill("batch is working on this"));
  const runs = pr.reviewRuns.length;
  if (runs > 0) out.push(pill(`${runs} runs`));
  for (const name of pr.labels.slice(0, 2)) out.push(pill(name));
  return out;
}

export function renderCard(pr, { onOpen, spinner, showRepo }) {
  const t = theme();
  const mark = CI[pr.ciStatus] ?? "";
  return v_flex().id(cardId(pr)).p(SP.md).gap(SP.sm).rounded(RADIUS.card).border_1().border_color(t.border)
    .cursor_pointer().hover((s) => s.bg(t.hover)).on_click(() => onOpen(pr))
    .child(div().text_size(TEXT.body).font_medium().line_height(TITLE_LH).max_h(TITLE_LH * 2)
      .overflow_hidden().child(pr.title ?? ""))
    .child(h_flex().gap(SP.sm).items_center().text_size(TEXT.caption).text_color(t.dim)
      .child(div().flex_none().child(showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`))
      .child(div().min_w(0).truncate().child(pr.author ?? ""))
      .child(div().flex_1().min_w(0).truncate().child(pr.branch ?? "")))
    .child(h_flex().gap(SP.sm).items_center().text_size(TEXT.caption)
      .child(div().text_color(t.ansi2).child(`+${pr.additions ?? 0}`))
      .child(div().text_color(t.ansi1).child(`-${pr.deletions ?? 0}`))
      .child(div().text_color(t.dim).child(`${pr.changedFiles ?? 0} files`))
      .when(Boolean(mark), (e) => e.child(div().text_color(ciColor(pr.ciStatus)).child(mark)))
      .when(Boolean(pr.reviewing), (e) => e.child(div().text_color(t.working).child(spinner))))
    .child(h_flex().gap_x(SP.xs).gap_y(SP.xs).flex_wrap().items_center().children(badges(pr)));
}

export function renderChips(filters, authors, { onToggle }) {
  const t = theme();
  const f = filters ?? {};
  const set = new Set((f.authors ?? []).map((a) => String(a).toLowerCase()));
  const chip = (label, name, on) => segment(`chip-${name}`, label, on, () => onToggle(name));
  return h_flex().id("prs-chips").gap(SP.sm).items_center().px(SP.lg).py(SP.sm).flex_none().flex_wrap()
    .bg(t.background)
    .child(chip("Mine", "mine", Boolean(f.mine)))
    .child(chip("Drafts", "drafts", Boolean(f.drafts)))
    .child(chip("Landed", "landed", Boolean(f.landed)))
    .children(authors.map((a) => chip(a, `author:${a}`, set.has(String(a).toLowerCase()))));
}
