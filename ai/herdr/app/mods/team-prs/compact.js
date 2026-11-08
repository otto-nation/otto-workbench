import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { cardId } from "./_lib/scope.js";

const CI = { passing: "✓", failing: "✗", pending: "…" };

function ciColor(status) {
  const t = theme();
  if (status === "passing") return t.ansi2;
  if (status === "failing") return t.ansi1;
  return t.dim;
}

export function renderCompact({ sections, onOpen, showRepo }) {
  const t = theme();
  const num = (pr) => (showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`);
  return v_flex().size_full().min_h(0).overflow_y_scrollbar()
    .children((sections ?? []).map((s) => v_flex().flex_none()
      .child(h_flex().h(22).flex_none().px(8).gap(8).items_center()
        .child(div().font_semibold().child(s.label))
        .child(div().text_size(11).text_color(t.dim).child(String(s.count))))
      .children(s.items.map((pr) =>
        h_flex().id(cardId(pr)).h(24).flex_none().px(8).gap(6).items_center()
          .cursor_pointer().hover((e) => e.bg(t.hover)).on_click(() => onOpen(pr))
          .child(div().flex_none().text_color(ciColor(pr.ciStatus)).child(CI[pr.ciStatus] ?? ""))
          .child(div().flex_none().text_color(t.dim).child(num(pr)))
          .child(div().min_w(0).truncate().child(pr.title ?? ""))))));
}

export function renderRail({ count }) {
  const t = theme();
  return v_flex().size_full().items_center().justify_center()
    .child(div().px(6).py(2).rounded(8).text_size(12).text_color(t.muted).child(String(count ?? 0)));
}
