import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { teamColumn, mineColumn, TEAM_COLUMNS, MINE_COLUMNS, filterPrs, filterOn, isMine, groupColumns, visibleColumns } from "./_lib/columns.js";
import { columnPreset } from "./_lib/batch_launch.js";
import { prPresentation } from "./_lib/pr_status.js";
import { cardId } from "./_lib/scope.js";
import { SP, RADIUS, LAYOUT, FILL } from "./_lib/tokens.js";
import {
  button, badgeRow, statsRow, card, placeholder, scrollBody,
  laneTitle, tag, text, type,
} from "./_ui/ui.js";
import { skeletonLanes } from "./load_status.js";

const { colMin: COL_MIN, colMax: COL_MAX } = LAYOUT;

function column(col, opts) {
  const t = theme();
  const empty = !col.items.length;
  return v_flex().id(`col-${col.key}`).h_full().min_h(0).rounded(RADIUS.card).bg(`${t.raised}${FILL.lane}`)
    .flex_1().min_w(COL_MIN).max_w(COL_MAX)
    .child(laneTitle(col.label, col.items.length, batchAction(col, opts.onBatch)))
    .child(scrollBody().p(SP.sm).gap(SP.sm)
      .when(empty, (e) => e.child(placeholder("None").px(SP.md)))
      .children(col.items.map((pr) => renderCard(pr, opts))));
}

// A Mine column a batch step can move PRs out of offers a shortcut to the Batch page with its
// PRs and that step set up (COLUMN_BATCH); an empty column, or one waiting on others, offers none.
export function batchAction(col, onBatch) {
  const preset = columnPreset(col);
  if (!onBatch || !preset) return null;
  return button(`batch-${col.key}`, "Batch", () => onBatch(col.items, preset));
}

export function renderBoard({
  tab, prs, filters, me, onOpen, loading, workflowOf, onBatch,
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
  if (loading) return board.children(skeletonLanes(spec));
  if (!shown.length) {
    return board.child(placeholder("No pull requests").p(SP.lg));
  }
  return board.children(shown.map((c) => column(c, {
    onOpen, workflowOf, onBatch: mine ? onBatch : null,
  })));
}

export function renderCard(pr, { onOpen, workflowOf }) {
  const t = theme();
  const p = prPresentation(pr, { item: workflowOf?.(pr) ?? null });
  return card(cardId(pr)).gap(SP.sm).on_click(() => onOpen(pr))
    .child(h_flex().gap(SP.sm).items_start()
      .child(type(div().flex_1().min_w(0).max_h(LAYOUT.cardTitleMaxH).overflow_hidden(), "title").child(p.title))
      .children(p.tags.map(tag)))
    .child(h_flex().gap(SP.sm).items_center()
      .child(text("meta", p.ref, t.muted).flex_none())
      .child(text("meta", p.author, t.muted).min_w(0).truncate())
      .child(type(div().flex_1().min_w(0).truncate(), "code").text_color(t.muted).child(p.branch)))
    .child(statsRow(p.stats))
    .when(p.badges.length > 0, (e) => e.child(badgeRow(p.badges)));
}

