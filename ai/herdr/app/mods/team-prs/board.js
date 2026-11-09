import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { teamColumn, mineColumn, TEAM_COLUMNS, MINE_COLUMNS, filterPrs, isMine, groupColumns, visibleColumns } from "./_lib/columns.js";
import { cardId } from "./_lib/scope.js";
import { SP, TEXT, RADIUS, LAYOUT, FILL } from "./_lib/tokens.js";
import { chip as chipOf, filterGroup, pill, ciMark, addStat, delStat, card, eyebrow, placeholder, scrollBody } from "./ui.js";

const { colMin: COL_MIN, colMax: COL_MAX } = LAYOUT;

function column(col, opts) {
  const t = theme();
  const empty = !col.items.length;
  return v_flex().id(`col-${col.key}`).h_full().min_h(0).rounded(RADIUS.card).bg(`${t.raised}${FILL.lane}`)
    .flex_1().min_w(COL_MIN).max_w(COL_MAX)
    .child(eyebrow(col.label, col.items.length, SP.md, { strong: true }))
    .child(scrollBody().p(SP.sm).gap(SP.sm)
      .when(empty, (e) => e.child(placeholder("none").px(SP.md).text_size(TEXT.caption)))
      .children(col.items.map((pr) => renderCard(pr, opts))));
}

export function renderBoard({ tab, prs, filters, me, onOpen, spinner, showRepo, loading }) {
  const t = theme();
  const mine = tab === "mine";
  const list = mine
    ? prs.filter((p) => isMine(p, me))
    : filterPrs(prs, filters, me);
  const spec = mine ? MINE_COLUMNS : TEAM_COLUMNS;
  const cols = groupColumns(list, mine ? mineColumn : teamColumn, spec);
  const shown = visibleColumns(cols, Boolean(filters?.empty));
  const board = h_flex().id("prs-board").flex_1().h_full().min_h(0).min_w(0).gap(SP.sm).px(SP.lg).py(SP.sm)
    .bg(t.background).text_color(t.foreground).text_size(TEXT.body).overflow_x_scrollbar();
  if (!shown.length) {
    return board.child(placeholder(loading ? "Loading…" : "No PRs").p(SP.lg));
  }
  return board.children(shown.map((c) => column(c, { onOpen, spinner, showRepo })));
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
  const mark = pr.ciStatus === "passing" || pr.ciStatus === "failing" || pr.ciStatus === "pending";
  return card(cardId(pr)).gap(SP.sm).on_click(() => onOpen(pr))
    .child(div().text_size(TEXT.body).font_medium().max_h(LAYOUT.cardTitleMaxH)
      .overflow_hidden().child(pr.title ?? ""))
    .child(h_flex().gap(SP.sm).items_center().text_size(TEXT.caption).text_color(t.dim)
      .child(div().flex_none().child(showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`))
      .child(div().min_w(0).truncate().child(pr.author ?? ""))
      .child(div().flex_1().min_w(0).truncate().child(pr.branch ?? "")))
    .child(h_flex().gap(SP.sm).items_center().text_size(TEXT.caption)
      .child(addStat(pr.additions ?? 0))
      .child(delStat(pr.deletions ?? 0))
      .child(div().text_color(t.dim).child(`${pr.changedFiles ?? 0} files`))
      .when(Boolean(mark), (e) => e.child(ciMark(pr.ciStatus)))
      .when(Boolean(pr.reviewing), (e) => e.child(div().text_color(t.working).child(spinner))))
    .child(h_flex().gap_x(SP.xs).gap_y(SP.xs).flex_wrap().items_center().children(badges(pr)));
}

export function renderChips(filters, authors, { onToggle, tab, repos = [] }) {
  const t = theme();
  const f = filters ?? {};
  const set = new Set((f.authors ?? []).map((a) => String(a).toLowerCase()));
  const activeSet = new Set((f.repos ?? []).map((r) => String(r).toLowerCase()));
  const chip = (label, name, on) => chipOf(`chip-${name}`, label, on, () => onToggle(name));
  const showItems = [];
  if (tab !== "mine") showItems.push(chip("Drafts", "drafts", Boolean(f.drafts)));
  showItems.push(chip("Landed", "landed", Boolean(f.landed)));
  showItems.push(chip("Empty lanes", "empty", Boolean(f.empty)));
  return v_flex().id("prs-chips").gap(SP.xs).px(SP.lg).py(SP.sm).flex_none().bg(t.background)
    .child(filterGroup("Show", showItems))
    .when(repos.length > 1, (e) => e.child(
      filterGroup("Repo", repos.map((slug) => chip(slug.split("/").pop(), `repo:${slug}`, activeSet.has(slug.toLowerCase()))))))
    .when(tab !== "mine" && authors.length > 0, (e) => e.child(
      filterGroup("Author", authors.map((a) => chip(a, `author:${a}`, set.has(String(a).toLowerCase()))))));
}
