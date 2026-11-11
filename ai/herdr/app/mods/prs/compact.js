import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { prIssues } from "./_lib/columns.js";
import { cardId } from "./_lib/scope.js";
import { SP, ROW_H, BREAKPOINT } from "./_lib/tokens.js";
import { sectionLabel, hoverRow, text, type, statusText } from "./_ui/ui.js";
import { batchAction, stateTags } from "./board.js";

function numOf(pr, showRepo) {
  return showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`;
}

export function renderCompact({ sections, onOpen, showRepo, width, onBatch, empty }) {
  const t = theme();
  const two = (width ?? 0) >= BREAKPOINT.twoLine;
  const list = sections ?? [];
  if (!list.length && empty) {
    return v_flex().size_full().p(SP.md).child(empty);
  }
  const row = (pr) => {
    const title = type(h_flex().min_w(0).flex_1().truncate(), "body").child(pr.title ?? "");
    const meta = `${numOf(pr, showRepo)} · ${pr.author ?? ""}`;
    return hoverRow(cardId(pr)).min_h(two ? ROW_H.sidebar : ROW_H.listDense)
      .flex_none().px(SP.md).mx(SP.xs).justify_center()
      .on_click(() => onOpen(pr))
      .child(h_flex().gap(SP.sm).items_center().child(title)
        .children(stateTags(pr)))
      .when(two, (e) => e.child(h_flex().gap(SP.sm).items_center()
        .child(text("meta", meta, t.muted).min_w(0).flex_1().truncate())
        .children(prIssues(pr).map((i) => statusText(i.status, i.label)))));
  };
  return v_flex().size_full().min_h(0).overflow_y_scrollbar()
    .children(list.map((s) => v_flex().flex_none()
      .child(h_flex().items_center().px(SP.md).pt(SP.md).pb(SP.xs)
        .child(sectionLabel(s.label, s.count))
        .child(h_flex().flex_1())
        .children([batchAction({ key: s.key, items: s.items }, onBatch)].filter(Boolean)))
      .children(s.items.map(row))));
}
