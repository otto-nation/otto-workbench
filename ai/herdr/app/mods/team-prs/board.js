import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { teamColumn, mineColumn, TEAM_COLUMNS, MINE_COLUMNS, filterPrs, isMine } from "./_lib/columns.js";
import { cardId } from "./_lib/scope.js";

const CI = { passing: "✓", failing: "✗", pending: "…" };

function ciColor(status) {
  const t = theme();
  if (status === "passing") return t.ansi2;
  if (status === "failing") return t.ansi1;
  return t.dim;
}

function pill(text, color) {
  const t = theme();
  return div().px(5).py(1).rounded(4).border_1().border_color(t.border).text_size(10)
    .text_color(color ?? t.muted).child(text);
}

function grouped(prs, colOf, columns) {
  const buckets = {};
  for (const c of columns) buckets[c.key] = [];
  for (const pr of prs) {
    const key = colOf(pr);
    if (buckets[key]) buckets[key].push(pr);
  }
  return columns.map((c) => ({ ...c, items: buckets[c.key] ?? [] }));
}

function column(col, opts) {
  const t = theme();
  return v_flex().id(`col-${col.key}`).flex_1().h_full().min_w(0).min_h(0)
    .child(h_flex().h(28).flex_none().px(8).gap(8).items_center()
      .child(div().font_semibold().child(col.label))
      .child(div().text_size(11).text_color(t.dim).child(String(col.items.length))))
    .child(v_flex().flex_1().min_h(0).overflow_y_scrollbar().p(6)
      .when(!col.items.length, (e) => e.child(div().p(8).text_color(t.dim).child("none")))
      .children(col.items.map((pr) => renderCard(pr, opts))));
}

export function renderBoard({ tab, prs, filters, me, onOpen, spinner, showRepo }) {
  const t = theme();
  const mine = tab === "mine";
  const list = mine
    ? prs.filter((p) => isMine(p, me))
    : filterPrs(prs, filters, me);
  const spec = mine ? MINE_COLUMNS : TEAM_COLUMNS;
  const cols = grouped(list, mine ? mineColumn : teamColumn, spec);
  return h_flex().id("prs-board").flex_1().h_full().min_h(0).gap(8).p(8).bg(t.background)
    .text_color(t.foreground).text_size(13)
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
  return v_flex().id(cardId(pr)).p(8).gap(4).rounded(8).border_1().border_color(t.border)
    .cursor_pointer().hover((s) => s.bg(t.hover)).on_click(() => onOpen(pr))
    .child(div().truncate().font_semibold().child(pr.title ?? ""))
    .child(h_flex().gap(8).items_center().text_size(11).text_color(t.dim)
      .child(div().flex_none().child(showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`))
      .child(div().min_w(0).truncate().child(pr.author ?? ""))
      .child(div().flex_1().min_w(0).truncate().child(pr.branch ?? "")))
    .child(h_flex().gap(8).items_center().text_size(11)
      .child(div().text_color(t.ansi2).child(`+${pr.additions ?? 0}`))
      .child(div().text_color(t.ansi1).child(`-${pr.deletions ?? 0}`))
      .child(div().text_color(t.dim).child(`${pr.changedFiles ?? 0} files`))
      .when(Boolean(mark), (e) => e.child(div().text_color(ciColor(pr.ciStatus)).child(mark)))
      .when(Boolean(pr.reviewing), (e) => e.child(div().text_color(t.working).child(spinner))))
    .child(h_flex().gap(4).items_center().children(badges(pr)));
}

function chip(label, name, on, onToggle) {
  const t = theme();
  return div().id(`chip-${name}`).px(8).py(4).rounded(6).text_size(11).cursor_pointer()
    .border_1().border_color(t.border).hover((s) => s.bg(t.hover))
    .when(on, (e) => e.bg(t.selected).text_color(t.foreground))
    .when(!on, (e) => e.text_color(t.muted))
    .on_click(() => onToggle(name))
    .child(label);
}

export function renderChips(filters, authors, { onToggle }) {
  const t = theme();
  const f = filters ?? {};
  const set = new Set((f.authors ?? []).map((a) => String(a).toLowerCase()));
  return h_flex().id("prs-chips").gap(6).items_center().p(8).flex_none().bg(t.background)
    .child(chip("Mine", "mine", Boolean(f.mine), onToggle))
    .child(chip("Drafts", "drafts", Boolean(f.drafts), onToggle))
    .child(chip("Landed", "landed", Boolean(f.landed), onToggle))
    .children(authors.map((a) => chip(a, `author:${a}`, set.has(String(a).toLowerCase()), onToggle)));
}
