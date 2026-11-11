import { div } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { theme } from "herdr";
import { SP, RADIUS, LAYOUT, FILL, ROW_H } from "./_lib/tokens.js";
import { statusText, placeholder, laneTitle, scrollBody } from "./_ui/ui.js";

export function renderLoadLine(load) {
  if (!load.shown) return null;
  return statusText("running", load.line, { gap: SP.sm });
}

export function renderLoadProgress(load) {
  if (!load.shown) return null;
  const t = theme();
  return h_flex().flex_none().w_full().h(SP.xxs).bg(t.border).accessibility_label(load.line)
    .child(div().h_full().w(`${load.progress}%`).bg(t.working));
}

export function renderLoadStrip(load) {
  if (!load.strip.length) return null;
  return h_flex().id("prs-load").flex_none().flex_wrap().px(SP.lg).pb(SP.xs).gap(SP.md)
    .children(load.strip.map((s) => statusText(s.kind, s.name, { gap: SP.sm })));
}

export function loadPlaceholder(load) {
  return load.shown ? renderLoadLine(load) : placeholder("Loading…");
}

export function skeletonLanes(spec) {
  const t = theme();
  return spec.map((col) => v_flex().id(`col-${col.key}`).h_full().min_h(0).rounded(RADIUS.card)
    .bg(`${t.raised}${FILL.lane}`).flex_1().min_w(LAYOUT.colMin).max_w(LAYOUT.colMax)
    .child(laneTitle(col.label))
    .child(scrollBody().p(SP.sm).gap(SP.sm)
      .child(div().border_1().border_dashed().border_color(t.border).rounded(RADIUS.card)
        .h(2 * ROW_H.list))
      .child(div().border_1().border_dashed().border_color(t.border).rounded(RADIUS.card)
        .h(ROW_H.list))));
}
