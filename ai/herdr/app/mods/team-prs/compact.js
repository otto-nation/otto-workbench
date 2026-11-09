import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { cardId } from "./_lib/scope.js";
import { SP, TEXT, ROW_H, BREAKPOINT } from "./_lib/tokens.js";
import { eyebrow, ciMark, hoverRow } from "./_ui/ui.js";

function numOf(pr, showRepo) {
  return showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`;
}

export function renderCompact({ sections, onOpen, showRepo, width }) {
  const t = theme();
  const two = (width ?? 0) >= BREAKPOINT.twoLine;
  const row = (pr) => {
    const title = div().min_w(0).flex_1().truncate().text_size(TEXT.body).font_medium().child(pr.title ?? "");
    const meta = `${numOf(pr, showRepo)} · ${pr.author ?? ""}`;
    return hoverRow(cardId(pr)).min_h(two ? ROW_H.sidebar : ROW_H.sidebarTitleOnly)
      .flex_none().px(SP.md).mx(SP.xs).justify_center()
      .on_click(() => onOpen(pr))
      .child(h_flex().gap(SP.sm).items_center().child(title).when(two, (e) => e.child(ciMark(pr.ciStatus))))
      .when(two, (e) => e.child(div().text_size(TEXT.caption).text_color(t.dim).truncate().child(meta)));
  };
  return v_flex().size_full().min_h(0).overflow_y_scrollbar()
    .children((sections ?? []).map((s) => v_flex().flex_none()
      .child(eyebrow(s.label, s.count))
      .children(s.items.map(row))));
}
