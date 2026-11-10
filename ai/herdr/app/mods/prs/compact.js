import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { cardId } from "./_lib/scope.js";
import { SP, ROW_H, BREAKPOINT } from "./_lib/tokens.js";
import { sectionLabel, ciMark, hoverRow, text, type } from "./_ui/ui.js";

function numOf(pr, showRepo) {
  return showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`;
}

export function renderCompact({ sections, onOpen, showRepo, width }) {
  const t = theme();
  const two = (width ?? 0) >= BREAKPOINT.twoLine;
  const row = (pr) => {
    const title = type(h_flex().min_w(0).flex_1().truncate(), "body").child(pr.title ?? "");
    const meta = `${numOf(pr, showRepo)} · ${pr.author ?? ""}`;
    return hoverRow(cardId(pr)).min_h(two ? ROW_H.sidebar : ROW_H.listDense)
      .flex_none().px(SP.md).mx(SP.xs).justify_center()
      .on_click(() => onOpen(pr))
      .child(h_flex().gap(SP.sm).items_center().child(title).when(two, (e) => e.child(ciMark(pr.ciStatus))))
      .when(two, (e) => e.child(text("meta", meta, t.muted).truncate()));
  };
  return v_flex().size_full().min_h(0).overflow_y_scrollbar()
    .children((sections ?? []).map((s) => v_flex().flex_none()
      .child(sectionLabel(s.label, s.count).px(SP.md).pt(SP.md).pb(SP.xs))
      .children(s.items.map(row))));
}
