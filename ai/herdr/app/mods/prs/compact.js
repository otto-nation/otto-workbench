import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { prPresentation } from "./_lib/pr_status.js";
import { cardId } from "./_lib/scope.js";
import { SP, ROW_H, BREAKPOINT } from "./_lib/tokens.js";
import { sectionLabel, hoverRow, text, type, badge, tag, errorLine, placeholder } from "./_ui/ui.js";
import { batchAction } from "./board.js";

export function renderCompact({ sections, onOpen, width, onBatch, empty, workflowOf }) {
  const t = theme();
  const two = (width ?? 0) >= BREAKPOINT.twoLine;
  const list = sections ?? [];
  if (!list.length && empty) {
    return v_flex().size_full().p(SP.md).child(empty);
  }
  const row = (pr) => {
    const p = prPresentation(pr, { item: workflowOf?.(pr) ?? null });
    const title = type(h_flex().min_w(0).flex_1().truncate(), "body").child(p.title);
    return hoverRow(cardId(pr)).min_h(two ? ROW_H.sidebar : ROW_H.listDense)
      .flex_none().px(SP.md).mx(SP.xs).justify_center()
      .on_click(() => onOpen(pr))
      .child(h_flex().gap(SP.sm).items_center().child(title)
        .children(p.tags.map(tag)))
      .when(two, (e) => e.child(h_flex().gap(SP.sm).items_center()
        .child(text("meta", `${p.ref} · ${p.author}`, t.muted).min_w(0).flex_1().truncate())
        .children(p.row_badges.map(badge))));
  };
  return v_flex().size_full().min_h(0).overflow_y_scrollbar()
    .children(list.map((s) => v_flex().flex_none()
      .child(h_flex().items_center().px(SP.md).pt(SP.md).pb(SP.xs)
        .child(sectionLabel(s.label, s.count, { status: s.status, message: s.message }))
        .child(h_flex().flex_1())
        .children([batchAction(s, onBatch)].filter(Boolean)))
      .when(s.status === "error", (e) => e.child(errorLine(s.message).px(SP.md)))
      .when(!s.items.length && s.status === "loading", (e) => e.child(placeholder(s.message).px(SP.md)))
      .children(s.items.map(row))));
}
