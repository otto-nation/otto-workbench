import { div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { theme } from "herdr";
import { cardId } from "./_lib/scope.js";
import { SP, TEXT, RADIUS, ROW_H } from "./_lib/tokens.js";
import { eyebrow } from "./ui.js";

const CI = { passing: "✓", failing: "✗", pending: "…" };

function ciColor(status) {
  const t = theme();
  if (status === "passing") return t.ansi2;
  if (status === "failing") return t.ansi1;
  return t.dim;
}

function numOf(pr, showRepo) {
  return showRepo && pr.repoSlug ? `${pr.repoSlug.split("/").pop()}#${pr.number}` : `#${pr.number}`;
}

export function renderCompact({ sections, onOpen, showRepo, width }) {
  const t = theme();
  const two = (width ?? 0) >= 200;
  const row = (pr) => {
    const title = div().min_w(0).flex_1().truncate().text_size(TEXT.body).font_medium().child(pr.title ?? "");
    const ci = div().flex_none().text_color(ciColor(pr.ciStatus)).child(CI[pr.ciStatus] ?? "");
    const meta = `${numOf(pr, showRepo)} · ${pr.author ?? ""}`;
    return v_flex().id(cardId(pr)).h(two ? ROW_H.sidebar : ROW_H.sidebarTitleOnly)
      .flex_none().px(SP.md).mx(SP.xs).justify_center().rounded(RADIUS.control)
      .cursor_pointer().hover((e) => e.bg(t.hover)).on_click(() => onOpen(pr))
      .child(h_flex().gap(SP.sm).items_center().child(title).when(two, (e) => e.child(ci)))
      .when(two, (e) => e.child(div().text_size(TEXT.caption).text_color(t.dim).truncate().child(meta)));
  };
  return v_flex().size_full().min_h(0).overflow_y_scrollbar()
    .children((sections ?? []).map((s) => v_flex().flex_none()
      .child(eyebrow(s.label, s.count))
      .children(s.items.map(row))));
}

export function renderRail({ count }) {
  const t = theme();
  return v_flex().size_full().items_center().justify_center()
    .child(div().px(6).py(2).rounded(8).text_size(12).text_color(t.muted).child(String(count ?? 0)));
}
