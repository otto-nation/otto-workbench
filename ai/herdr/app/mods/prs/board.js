import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { teamColumn, mineColumn, TEAM_COLUMNS, MINE_COLUMNS, filterPrs, filterOn, isMine, groupColumns, visibleColumns } from "./_lib/columns.js";
import { cardId } from "./_lib/scope.js";
import { SP, RADIUS, LAYOUT, FILL } from "./_lib/tokens.js";
import {
  chip as chipOf, filterGroup, ciMark, addStat, delStat, card, placeholder, scrollBody,
  laneTitle, tag, text, type, statusText,
} from "./_ui/ui.js";
import { cardStateLine } from "./workflows_view.js";

const { colMin: COL_MIN, colMax: COL_MAX } = LAYOUT;

function column(col, opts) {
  const t = theme();
  const empty = !col.items.length;
  return v_flex().id(`col-${col.key}`).h_full().min_h(0).rounded(RADIUS.card).bg(`${t.raised}${FILL.lane}`)
    .flex_1().min_w(COL_MIN).max_w(COL_MAX)
    .child(laneTitle(col.label, col.items.length))
    .child(scrollBody().p(SP.sm).gap(SP.sm)
      .when(empty, (e) => e.child(placeholder("None").px(SP.md)))
      .children(col.items.map((pr) => renderCard(pr, opts))));
}

export function renderBoard({
  tab, prs, filters, me, onOpen, spinner, showRepo, loading, workflowOf,
}) {
  const t = theme();
  const mine = tab === "mine";
  const list = mine
    ? prs.filter((p) => isMine(p, me) && (filterOn(filters, "mineDrafts") || !p.isDraft))
    : filterPrs(prs, filters, me);
  const spec = mine ? MINE_COLUMNS : TEAM_COLUMNS;
  const cols = groupColumns(list, mine ? mineColumn : teamColumn, spec);
  const shown = visibleColumns(cols, filterOn(filters, "empty"));
  const board = type(h_flex().id("prs-board").flex_1().h_full().min_h(0).min_w(0).gap(SP.sm).px(SP.lg).py(SP.sm)
    .bg(t.background).text_color(t.foreground).overflow_x_scrollbar(), "body");
  if (!shown.length) {
    return board.child(placeholder(loading ? "Loading…" : "No pull requests").p(SP.lg));
  }
  return board.children(shown.map((c) => column(c, {
    onOpen, spinner, showRepo, workflowOf,
  })));
}

function badges(pr, { hideBusy } = {}) {
  const t = theme();
  const out = [];
  if (pr.isDraft) out.push(text("meta", "Draft", t.muted));
  if (pr.state === "MERGED") out.push(statusText("pass", "Merged"));
  if (pr.inMergeQueue) out.push(statusText("running", "In merge queue"));
  if (pr.busy && !hideBusy) out.push(statusText("running", "Batch working"));
  const runs = pr.reviewRuns.length;
  if (runs > 0) out.push(text("meta", `${runs} reviews`, t.muted));
  for (const name of pr.labels.slice(0, 2)) out.push(tag(name));
  return out;
}

export function renderCard(pr, {
  onOpen, spinner, showRepo, workflowOf,
}) {
  const t = theme();
  const mark = pr.ciStatus === "passing" || pr.ciStatus === "failing" || pr.ciStatus === "pending";
  const item = workflowOf?.(pr);
  const stateLine = item ? cardStateLine(item) : null;
  const num = showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`;
  return card(cardId(pr)).gap(SP.sm).on_click(() => onOpen(pr))
    .child(h_flex().gap(SP.sm).items_center()
      .child(type(div().flex_1().min_w(0).max_h(LAYOUT.cardTitleMaxH).overflow_hidden(), "title")
        .child(pr.title ?? "")))
    .child(h_flex().gap(SP.sm).items_center()
      .child(text("meta", num, t.muted).flex_none())
      .child(text("meta", pr.author ?? "", t.muted).min_w(0).truncate())
      .child(type(div().flex_1().min_w(0).truncate(), "code").text_color(t.muted).child(pr.branch ?? "")))
    .child(h_flex().gap(SP.sm).items_center()
      .child(type(addStat(pr.additions ?? 0), "meta"))
      .child(type(delStat(pr.deletions ?? 0), "meta"))
      .child(text("meta", `${pr.changedFiles ?? 0} files`, t.muted))
      .when(Boolean(mark), (e) => e.child(ciMark(pr.ciStatus)))
      .when(Boolean(pr.reviewing), (e) => e.child(div().text_color(t.working).child(spinner))))
    .child(h_flex().gap_x(SP.xs).gap_y(SP.xs).flex_wrap().items_center().children(badges(pr, { hideBusy: Boolean(stateLine) })))
    .when(Boolean(stateLine), (e) => e.child(stateLine));
}

export function renderChips(filters, authors, { onToggle, tab, repos = [] }) {
  const t = theme();
  const f = filters ?? {};
  const set = new Set((f.authors ?? []).map((a) => String(a).toLowerCase()));
  const activeSet = new Set((f.repos ?? []).map((r) => String(r).toLowerCase()));
  const chip = (label, name, on) => chipOf(`chip-${name}`, label, on, () => onToggle(name));
  const showItems = [];
  if (tab === "mine") showItems.push(chip("Drafts", "mineDrafts", filterOn(f, "mineDrafts")));
  else showItems.push(chip("Drafts", "drafts", filterOn(f, "drafts")));
  showItems.push(chip("Landed", "landed", filterOn(f, "landed")));
  showItems.push(chip("Empty lanes", "empty", filterOn(f, "empty")));
  return v_flex().id("prs-chips").gap(SP.xs).px(SP.lg).py(SP.sm).flex_none().bg(t.background)
    .child(filterGroup("Show", showItems))
    .when(repos.length > 1, (e) => e.child(
      filterGroup("Repo", repos.map((slug) => chip(slug.split("/").pop(), `repo:${slug}`, activeSet.has(slug.toLowerCase()))))))
    .when(tab !== "mine" && authors.length > 0, (e) => e.child(
      filterGroup("Author", authors.map((a) => chip(a, `author:${a}`, set.has(String(a).toLowerCase()))))));
}
